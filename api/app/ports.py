"""/v1/map/ports and /v1/ports/{locode} — the twelve ports, from Postgres.

The polygons are static reference data (12 ports, 3 of them with an anchorage),
loaded by ops/geo/load_ports.py. They change when someone edits a geojson file
and reruns the loader, which means never during a process's life — so the
FeatureCollection is decoded once and kept.

PostGIS renders the GeoJSON (ST_AsGeoJSON); python only parses it. Like the map
snapshot and unlike /status.json, a missing Postgres is a 503 rather than an
empty payload: a map with no port outlines at all reads as "no ports here".
"""

import json
import logging
from typing import Any, Protocol

from .limits import PORT_WAIT_WINDOW_D
from .map import REGION, RedisClient
from .ships import ClickHouseClient

logger = logging.getLogger("ports")

# The detector's crash snapshot, one JSON field per ship (pipeline sinks.py
# SnapshotStore) — rewritten every 30 s, which is the "recompute <=5 min" of F19
# with room to spare. It is the ONLY place the per-ship zone lives: `events`
# holds anchorages that have already ended, and a queue is made of the ones that
# have not. Reading another service's snapshot is a borrowed key, not a table —
# ponytail: the upgrade path is the detector publishing per-port counters.
SNAPSHOT_KEY = f"detector:{REGION}"

# median(), not avg(): one ship that sat out a storm for four days would drag a
# mean far past anything the next arrival will actually wait.
WAIT_QUERY = """
SELECT median(toFloat64OrNull(toString(meta.duration_s))) AS wait_s
FROM events
WHERE kind = 'anchorage' AND port = %(locode)s
  AND t_end IS NOT NULL AND t_end >= now() - INTERVAL %(days)s DAY
"""

GEOJSON_QUERY = (
    "SELECT locode, name, kind, ST_AsGeoJSON(geom) AS geom, "
    "ST_AsGeoJSON(anchorages) AS anchorages FROM ports ORDER BY locode"
)
PORT_QUERY = "SELECT locode, name FROM ports WHERE locode = $1"


class Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


class PortsUnavailable(Exception):
    """Postgres is missing or unreachable — the caller turns this into a 503."""


_cache: dict[str, Any] | None = None


def _feature(row: Any, kind: str, geom: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": json.loads(geom),
        "properties": {"locode": row["locode"], "name": row["name"], "kind": kind},
    }


async def ports_geojson(pool: Pool | None) -> dict[str, Any]:
    """Every port outline, plus a separate feature per anchorage."""
    global _cache
    if _cache is not None:
        return _cache
    if pool is None:
        raise PortsUnavailable("no postgres connection")
    try:
        rows = await pool.fetch(GEOJSON_QUERY)
    except Exception as exc:
        logger.warning("ports unavailable: %s: %s", type(exc).__name__, exc)
        raise PortsUnavailable(str(exc)) from exc

    features: list[dict[str, Any]] = []
    for row in rows:
        features.append(_feature(row, "port", row["geom"]))
        if row["anchorages"] is not None:
            features.append(_feature(row, "anchorage", row["anchorages"]))
    collection = {"type": "FeatureCollection", "features": features}
    # An empty table is the not-yet-loaded state (migrate ran, make geo hasn't):
    # caching it would pin a portless map until restart. Cache only real content.
    if features:
        _cache = collection
    return collection


async def waiting_now(client: RedisClient | None, locode: str) -> int | None:
    """How many ships are sitting in this port's anchorage right now.

    None, never 0, when we could not ask: an empty anchorage and an unreachable
    detector look identical on the wire otherwise, and the tooltip would print
    "0 waiting" over a queue of thirty.
    """
    if client is None:
        return None
    try:
        fields: Any = await client.hgetall(SNAPSHOT_KEY)
    except Exception as exc:
        logger.warning("port queue unavailable: %s: %s", type(exc).__name__, exc)
        return None
    if not fields:
        return None
    count = 0
    for raw in fields.values():
        try:
            ship = json.loads(raw)
        except ValueError:  # pragma: no cover — the detector writes json or nothing
            continue
        if ship.get("zone") == "anchorage" and ship.get("port") == locode:
            count += 1
    return count


async def typical_wait_h(ch: ClickHouseClient | None, locode: str) -> float | None:
    """The median finished anchorage of the last window, in hours. None when the
    lake is gone or has not seen one — a port nobody waited at has no typical."""
    if ch is None:
        return None
    try:
        params = {"locode": locode, "days": PORT_WAIT_WINDOW_D}
        result = await ch.query(WAIT_QUERY, parameters=params)
    except Exception as exc:
        logger.warning("port wait unavailable: %s: %s", type(exc).__name__, exc)
        return None
    rows = result.result_rows
    seconds = rows[0][0] if rows else None
    return None if seconds is None else round(float(seconds) / 3600, 1)


async def port_payload(
    pool: Pool | None,
    client: RedisClient | None,
    ch: ClickHouseClient | None,
    locode: str,
) -> dict[str, Any] | None:
    """The port panel: identity from Postgres, the queue from the detector's
    snapshot, the typical wait from the lake. Either number is null when its
    store could not answer — a missing count is not a zero."""
    if pool is None:
        raise PortsUnavailable("no postgres connection")
    try:
        row = await pool.fetchrow(PORT_QUERY, locode.upper())
    except Exception as exc:
        logger.warning("port unavailable: %s: %s", type(exc).__name__, exc)
        raise PortsUnavailable(str(exc)) from exc
    if row is None:
        return None
    code = str(row["locode"])
    return {
        "locode": code,
        "name": row["name"],
        "waiting_now": await waiting_now(client, code),
        "typical_wait_h": await typical_wait_h(ch, code),
        # The 30-day band is M5's chart, not a number this endpoint can hold.
        "band30d": None,
    }
