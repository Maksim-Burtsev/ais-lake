"""GET /v1/search?q= — the top bar's grouped dropdown (F5).

The wire:
  {"q": "gas k",
   "ships": [{"mmsi", "name", "flag", "class", "sym", "state", "sentence",
              "sog", "cog", "lat", "lon", "age_h"}, …],
   "ports": [],
   "seas":  [{"slug", "name", "bbox", "count"}, …],
   "searched": {"live": 2412, "seen_30d": 41880},
   "near": <one ship row> | null}

Identity comes from `vessels_static`, state and the status sentence from
`vessel_latest`, both argMax-over-ts for the reason ships.py spells out. The
class arrives on the `sym` token of the hot hash and nowhere else — like the
card, this module never looks at ship_type. The sentence is read, never
re-rendered.

Ranking. An exact MMSI (9 digits) or IMO (7) short-circuits the whole thing.
Otherwise: a prefix hit, then a substring hit, then ClickHouse's own
`ngramDistanceCaseInsensitiveUTF8` — which returns 0 for identical and 1 for
nothing in common, so SMALLER IS BETTER and the ORDER BY is ascending. Inside a
tier the fleet that is out there now (a fix inside the map's 24 h window) comes
before the archive: a ship you could still watch beats one you can only read
about.

A miss is not a shrug. `searched` carries the two real numbers the empty state
quotes and `near` the closest name we actually hold, so the panel never invents
a figure.

Ships with no live row still list — identity is real whether or not she is
transmitting — and their sentence, state and metrics come back null.

Gaps, named rather than papered over:
  · `ports` is always empty. F5 names ports, but there is no ports table until
    M3 draws the PostGIS polygons; an invented port is worse than an empty group.
  · anchored and moored ships carry no metric. "18 h at anchor" needs an
    anchorage event with a t_start, which is F19/M5 — the fix age we do hold is a
    different fact and would read as a lie in that slot. Silent ships get the
    age, because for them it IS the number.
"""

import logging
import time
from typing import Any

from .flags import flag_for
from .limits import MAX_VESSEL_AGE_S
from .map import REGION, RedisClient, SnapshotUnavailable, counts_for, row_for
from .regions import _entries
from .ships import CLASS_NAMES, ClickHouseClient, ShipNotFound, _epoch, _resolve, _text

logger = logging.getLogger("search")

# Rows per group. A dropdown is not a result page: the frame shows three ships
# and a sea, and the full-page search that would want paging is a later screen.
# NOT a limits.json number — F27's table is history windows, follows, refresh
# floors and API quotas, and this is none of those.
RESULT_CAP = 8
# Anything longer is a paste accident; the tail cannot match a ship's name.
MAX_Q = 64
# Measured against the seed fleet: a real near-miss ("kapitan zar" vs KAPITAN
# GLOWACKI) lands ~0.48, nonsense bottoms out ~0.67. Above this we call it no
# match — and the best of the rejects becomes `near`.
NGRAM_MAX = 0.6

# The two lake tables are ReplacingMergeTree, so a read that beats the merge
# needs argMax over ts. `state` is cast to String so a ship with no fix at all
# LEFT JOINs to '' rather than to an Enum8 that has no zero.
SHIP_QUERY = """
SELECT s.mmsi, s.name, l.state, l.sentence, l.sog, l.cog, l.lat, l.lon, l.last_ts,
       multiIf(startsWith(lowerUTF8(s.name), lowerUTF8(%(q)s)), 0,
               positionCaseInsensitiveUTF8(s.name, %(q)s) > 0, 1, 2) AS tier,
       ngramDistanceCaseInsensitiveUTF8(s.name, %(q)s) AS dist,
       l.last_ts > (now() - %(age)s) AS live
FROM (
    SELECT mmsi, argMax(imo, ts) AS imo, argMax(name, ts) AS name
    FROM vessels_static GROUP BY mmsi
) AS s
LEFT JOIN (
    SELECT mmsi, argMax(toString(state), ts) AS state, argMax(sentence, ts) AS sentence,
           argMax(sog, ts) AS sog, argMax(cog, ts) AS cog, argMax(lat, ts) AS lat,
           argMax(lon, ts) AS lon, max(ts) AS last_ts
    FROM vessel_latest GROUP BY mmsi
) AS l USING (mmsi)
WHERE {where}
ORDER BY tier ASC, if(tier < 2, 0., round(dist, 1)) ASC, live DESC, s.name ASC
LIMIT %(cap)s
"""
# `dist` is noise once a name already contains the query, so the tiers that
# matched literally sort on liveness alone — that is the frame's promise, the
# ship you could still watch first.

WHERE = {
    "mmsi": "s.mmsi = %(id)s",
    "imo": "s.imo = %(id)s",
    "name": "s.name != ''",
}

# One row per ship, ~20k of them, so this measures in single-digit milliseconds
# against the live stack — no cache earns its keep here.
SEEN_30D_QUERY = """
SELECT count() FROM (
    SELECT mmsi FROM vessel_latest GROUP BY mmsi HAVING max(ts) > now() - INTERVAL 30 DAY
)
"""


def _bbox_of(slug: str) -> tuple[float, float, float, float] | None:
    entry = next((e for e in _entries() if e["slug"] == slug), None)
    return tuple(entry["bbox"]) if entry else None


async def _ship(client: RedisClient | None, row: tuple[Any, ...]) -> dict[str, Any]:
    mmsi, name, state, sentence, sog, cog, lat, lon, last_ts = row[:9]
    # the token the map is already drawing this ship with — class comes from
    # there, exactly as it does on the card (ships.py).
    hot = await row_for(client, int(mmsi))
    sym = str(hot[6]) if hot else None
    fix = bool(state)  # '' is the LEFT JOIN miss: identity but no position ever
    return {
        "mmsi": int(mmsi),
        "name": _text(name),
        "flag": flag_for(int(mmsi)),
        "class": CLASS_NAMES.get(sym.rstrip("0123456789")) if sym else None,
        "sym": sym,
        "state": str(state) if fix else None,
        "sentence": _text(sentence) if fix else None,
        "sog": round(float(sog), 1) if fix else None,
        "cog": round(float(cog), 1) if fix else None,
        # where ⏎ flies the map to; a ship with no fix ever has nowhere to fly.
        "lat": round(float(lat), 5) if fix else None,
        "lon": round(float(lon), 5) if fix else None,
        "age_h": round((time.time() - _epoch(last_ts)) / 3600, 1) if fix else None,
    }


async def _ships(
    ch: ClickHouseClient, client: RedisClient | None, q: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The ranked hits, and the closest name that missed the cut."""
    try:
        kind, value = _resolve(q)
    except ShipNotFound:
        kind, value = "name", 0

    result = await ch.query(
        SHIP_QUERY.format(where=WHERE[kind]),
        parameters={"q": q, "id": value, "age": MAX_VESSEL_AGE_S, "cap": RESULT_CAP},
    )
    rows = [tuple(r) for r in result.result_rows]
    if kind != "name":
        return [await _ship(client, r) for r in rows], None

    # tier 0/1 matched the letters themselves; tier 2 has to earn it on distance.
    hits = [r for r in rows if r[9] < 2 or float(r[10]) <= NGRAM_MAX]
    if hits:
        return [await _ship(client, r) for r in hits], None
    # nothing matched: offer the nearest name we hold, unless it shares nothing
    # with the query at all (dist 1.0), in which case there is no "closest".
    nearest = min(rows, key=lambda r: float(r[10]), default=None)
    if nearest is None or float(nearest[10]) >= 1.0:
        return [], None
    return [], await _ship(client, nearest)


def _matching_seas(q: str) -> list[dict[str, Any]]:
    """Only regions the map can actually take you to: the picker lists a
    not-yet-live sea as "coming soon", search offers no row you cannot follow."""
    hits = [e for e in _entries() if e["live"] and q.lower() in e["name"].lower()]
    return hits[:RESULT_CAP]


async def _counts(
    client: RedisClient | None, seas: list[dict[str, Any]]
) -> tuple[list[int | None], int | None]:
    """A live count per matched sea, plus the count for the region the user is in
    — one hash read for both."""
    boxes: list[tuple[float, float, float, float] | None] = [
        _bbox_of(REGION),
        *(_bbox_of(e["slug"]) for e in seas),
    ]
    try:
        counts = await counts_for(client, boxes)
    except SnapshotUnavailable:
        counts = [None] * len(boxes)
    return counts[1:], counts[0]


async def _seen_30d(ch: ClickHouseClient) -> int | None:
    result = await ch.query(SEEN_30D_QUERY, parameters={})
    rows = result.result_rows
    return int(rows[0][0]) if rows else None


async def search_payload(
    ch: ClickHouseClient | None, client: RedisClient | None, q: str
) -> dict[str, Any]:
    q = q.strip()[:MAX_Q]
    if not q:
        return {
            "q": q,
            "ships": [],
            "ports": [],
            "seas": [],
            "near": None,
            "searched": {"live": None, "seen_30d": None},
        }

    matched = _matching_seas(q)
    ships: list[dict[str, Any]] = []
    near: dict[str, Any] | None = None
    seen_30d: int | None = None
    live: int | None = None
    if ch is not None:
        try:
            # Sequential on purpose: clickhouse-connect's async client hands one
            # sync connection to an executor, so two queries on it would race for
            # a couple of milliseconds. Both together measure ~15 ms.
            ships, near = await _ships(ch, client, q)
            # Only the empty state quotes it, and it is the cheap query of the two.
            if not ships:
                seen_30d = await _seen_30d(ch)
        except Exception as exc:
            # Like /v1/regions and unlike the card: a search box that half-works
            # beats one that 503s, and the empty state quotes a null as "—".
            logger.warning("search: query failed: %s: %s", type(exc).__name__, exc)
    else:
        logger.warning("search: no clickhouse connection")

    # The counts cost a whole scan of the hot hash (~100 ms on the seed fleet),
    # which is most of F5's 150 ms budget. Nothing on a hit-carrying dropdown
    # needs them, so pay only when a sea row or the empty state will show one.
    counts: list[int | None] = [None] * len(matched)
    if matched or not ships:
        counts, live = await _counts(client, matched)

    return {
        "q": q,
        "ships": ships,
        # Always empty: there is no ports table until M3's PostGIS polygons, and
        # an invented port is worse than an absent one. The group ships anyway so
        # the client's shape does not change the day it fills.
        "ports": [],
        "seas": [
            {"slug": e["slug"], "name": e["name"], "bbox": e["bbox"], "count": count}
            for e, count in zip(matched, counts, strict=True)
        ],
        "near": near,
        "searched": {"live": live, "seen_30d": seen_30d},
    }
