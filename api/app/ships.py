"""GET /v1/ships/{mmsi|imo} — the card behind a ship tap (F8).

The wire:
  {"mmsi": 249118000,
   "identity": {"imo", "name", "callsign", "flag", "class", "sym",
                "size_m", "draught_m", "destination", "eta"},
   "sentence": "Under way at 9.8 kn" | null,
   "latest": {"ts", "lat", "lon", "sog", "cog", "heading", "nav_status", "state"} | null}

Three sources and no others. `vessel_latest` carries the fix AND the status
sentence; `vessels_static` the identity; the Redis hot hash the `sym` token the
map already draws with. Both lake tables are ReplacingMergeTree, so a read that
beats the merge needs argMax over ts rather than a bare SELECT.

The sentence is READ here, never re-rendered: refinery/state.py::sentence_for
writes those words, which is F8's "one source of truth" in so many words. The
class arrives the same way — symbology.py::class_of owns ship_type -> class, so
this module never looks at ship_type at all and only translates the class key
into the word a person reads (SYMBOLOGY.md §1).

Unknowns come back null, never a plausible substitute: the card draws them as
"—" (F15). A ship with a fix but no type-5 yet still returns a usable card.

No ClickHouse means no identity and no sentence, which is not a card at all —
that 503s, on the snapshot's reasoning. No Redis costs only the sprite token, so
the class goes null and everything else stays true (/status.json's reasoning).

Gap: the frame ends its sentence with "· a 17-ship queue awaits". Port queue
numbers are F19 (M5) and do not exist yet, so nothing here invents one.
"""

import logging
from datetime import UTC
from typing import Any, Protocol

from .flags import flag_for
from .map import RedisClient, row_for

logger = logging.getLogger("ships")

MMSI_DIGITS = 9
IMO_DIGITS = 7
HEADING_NA = 511  # AIS "not available"

# SYMBOLOGY.md §1. The class KEY is the refinery's; only the display name is ours.
CLASS_NAMES = {
    "tanker": "Tanker",
    "cargo": "Cargo",
    "ferry": "Passenger / ferry",
    "fishing": "Fishing",
    "tug": "Tug / workboat",
    "hsc": "High-speed craft",
    "pleasure": "Pleasure / sailing",
    "unknown": "Unknown",
}

LATEST_QUERY = """
SELECT max(ts), argMax(lat, ts), argMax(lon, ts), argMax(sog, ts), argMax(cog, ts),
       argMax(heading, ts), argMax(nav_status, ts), argMax(state, ts), argMax(sentence, ts)
FROM vessel_latest WHERE mmsi = %(mmsi)s GROUP BY mmsi
"""
# ship_type is deliberately not selected: deriving the class from it here would
# duplicate refinery/symbology.py::class_of, and two copies drift.
STATIC_QUERY = """
SELECT argMax(imo, ts), argMax(name, ts), argMax(callsign, ts), argMax(dim_a, ts),
       argMax(dim_b, ts), argMax(draught, ts), argMax(destination, ts), argMax(eta, ts)
FROM vessels_static WHERE mmsi = %(mmsi)s GROUP BY mmsi
"""
# A hull can change flag and MMSI while keeping its IMO; the newest static row
# names the MMSI it answers to now.
MMSI_FOR_IMO_QUERY = "SELECT mmsi FROM vessels_static WHERE imo = %(imo)s ORDER BY ts DESC LIMIT 1"

NO_STATIC: tuple[Any, ...] = (0, "", "", 0, 0, 0.0, "", "")


class ClickHouseClient(Protocol):
    async def query(self, query: str, parameters: dict[str, Any]) -> Any: ...


class ShipNotFound(Exception):
    """No such ship — the route 404s it."""


class CardUnavailable(Exception):
    """ClickHouse is gone, so there is nothing to put on a card — a 503."""


def _resolve(key: str) -> tuple[str, int]:
    """A 9-digit MMSI or a 7-digit IMO, and nothing else.

    A key of the wrong shape gets the same 404 as a ship we do not hold, not a
    422: the path segment names a resource, "banana" names no ship exactly as
    999999999 names none, and one error path is one thing for the client to
    handle. It also declines to tell a prober which shapes the lake would accept.
    """
    if not (key.isascii() and key.isdigit()):
        raise ShipNotFound(key)
    if len(key) == MMSI_DIGITS:
        return "mmsi", int(key)
    if len(key) == IMO_DIGITS:
        return "imo", int(key)
    raise ShipNotFound(key)


async def _one_row(
    ch: ClickHouseClient, query: str, params: dict[str, Any]
) -> tuple[Any, ...] | None:
    try:
        result = await ch.query(query, parameters=params)
    except Exception as exc:
        logger.warning("ship card: query failed: %s: %s", type(exc).__name__, exc)
        raise CardUnavailable(str(exc)) from exc
    rows = result.result_rows
    return tuple(rows[0]) if rows else None


def _text(value: Any) -> str | None:
    return str(value).strip() or None


def _reported(value: Any) -> Any:
    """AIS reports "not available" as zero for IMO, dimensions and draught."""
    return value or None


def _epoch(ts: Any) -> int:
    """ClickHouse hands back naive UTC datetimes; .timestamp() would read them as
    local time and put the fix hours out of date."""
    return int(ts.replace(tzinfo=ts.tzinfo or UTC).timestamp())


async def card_for(
    ch: ClickHouseClient | None, redis_client: RedisClient | None, key: str
) -> dict[str, Any]:
    kind, value = _resolve(key)
    if ch is None:
        raise CardUnavailable("no clickhouse connection")

    mmsi = value
    if kind == "imo":
        found = await _one_row(ch, MMSI_FOR_IMO_QUERY, {"imo": value})
        if found is None:
            raise ShipNotFound(key)
        mmsi = int(found[0])

    latest = await _one_row(ch, LATEST_QUERY, {"mmsi": mmsi})
    static = await _one_row(ch, STATIC_QUERY, {"mmsi": mmsi})
    if latest is None and static is None:
        raise ShipNotFound(key)

    imo, name, callsign, dim_a, dim_b, draught, destination, eta = static or NO_STATIC
    # the token the map is already drawing this ship with — class comes from there.
    hot = await row_for(redis_client, mmsi)
    sym = str(hot[6]) if hot else None

    return {
        "mmsi": mmsi,
        "identity": {
            "imo": _reported(imo),
            "name": _text(name),
            "callsign": _text(callsign),
            "flag": flag_for(mmsi),
            "class": CLASS_NAMES.get(sym.rstrip("0123456789")) if sym else None,
            "sym": sym,
            "size_m": _reported(dim_a + dim_b),
            "draught_m": _reported(round(float(draught), 1)),
            "destination": _text(destination),
            "eta": _text(eta),
        },
        "sentence": _text(latest[8]) if latest else None,
        "latest": None
        if latest is None
        else {
            "ts": _epoch(latest[0]),
            "lat": round(float(latest[1]), 5),
            "lon": round(float(latest[2]), 5),
            "sog": round(float(latest[3]), 1),
            "cog": round(float(latest[4]), 1),
            "heading": None if latest[5] == HEADING_NA else int(latest[5]),
            "nav_status": int(latest[6]),
            "state": str(latest[7]),
        },
    }
