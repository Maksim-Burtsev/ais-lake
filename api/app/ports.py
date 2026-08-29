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

logger = logging.getLogger("ports")

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


async def port_payload(pool: Pool | None, locode: str) -> dict[str, Any] | None:
    """The port panel's skeleton. The queue numbers are M3's next step; until the
    detector fills them they are null rather than zero — nobody has counted yet."""
    if pool is None:
        raise PortsUnavailable("no postgres connection")
    try:
        row = await pool.fetchrow(PORT_QUERY, locode.upper())
    except Exception as exc:
        logger.warning("port unavailable: %s: %s", type(exc).__name__, exc)
        raise PortsUnavailable(str(exc)) from exc
    if row is None:
        return None
    return {
        "locode": row["locode"],
        "name": row["name"],
        "waiting_now": None,
        "typical_wait_h": None,
        "band30d": None,
    }
