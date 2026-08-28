"""GET /v1/search?q= — the top bar's grouped dropdown (F5).

The wire:
  {"q": "gas k",
   "answering": true,
   "ships": [{"mmsi", "name", "flag", "class", "sym", "state", "sentence",
              "sog", "cog", "lat", "lon", "age_h"}, …],
   "ports": [],
   "seas":  [{"slug", "name", "bbox", "count"}, …],
   "searched": {"live": 2412, "seen_30d": 41880, "region": "North Sea"},
   "near": <one ship row> | null}

`answering` is false when the lake never answered — no connection, or the query
in pieces. Without it a broken ClickHouse and a genuine miss are the same 200,
and `ships: []` reads as "nothing called GAS KHIOS is transmitting" in 22 px
display type while Redis is fine and the map behind the panel is still drawing
her. That is the lie /v1/map/snapshot 503s to avoid, told with more words. So:
`[]` never means "did not ask", the panel says the search is not answering, and
the failure is on the wire rather than in a logger.warning nobody reads.

`searched.region` NAMES the box those `live` vessels were counted in — this
process's own REGION_SLUG. /v1/search takes no region and cannot know where the
user is, so the copy has to print the region the server measured; the client's
own picker state would put a North Sea number in a Kattegat sentence.

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
from .ships import ClickHouseClient, ShipNotFound, _epoch, _resolve, _text, class_name

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
ORDER BY tier ASC, if(tier < 2, 0, dist > %(ngram_max)s) ASC,
         if(tier < 2, 0., round(dist, 1)) ASC, live DESC, s.name ASC
LIMIT %(cap)s
"""
# `dist` is noise once a name already contains the query, so the tiers that
# matched literally sort on liveness alone — that is the frame's promise, the
# ship you could still watch first.
#
# Accepted-before-rejected comes FIRST inside tier 2, and it has to: the bucket
# `round(dist, 1)` puts ships in straddles the threshold, so [0.55, 0.65) holds
# both hits and rejects, and for a fuzzy query against 20k names most of the
# fleet lands there. Ordered on the bucket alone the tiebreak is `s.name`, so
# eight rejects beginning "A" fill the LIMIT, python drops all eight as over
# threshold, and a real 0.58 match beginning "V" is answered "no ships" — it was
# never returned. A reject must never displace a hit inside the LIMIT. The
# threshold rides as a parameter so SQL and python read the ONE number rather
# than two copies of 0.6.

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


# The display name of the region this process serves, for `searched.region`.
# regions.json holds both, and the copy prints the name in a sentence. NOT a
# ?region= parameter: scoping the ship query per region is M3 work, so all this
# can honestly do is say which box the number came from.
REGION_NAME: str | None = next((e["name"] for e in _entries() if e["slug"] == REGION), None)


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
        "class": class_name(sym),
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
        parameters={
            "q": q,
            "id": value,
            "age": MAX_VESSEL_AGE_S,
            "cap": RESULT_CAP,
            "ngram_max": NGRAM_MAX,
        },
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
    """A live count per matched sea, plus the count for the region this process
    serves — one hash read for both. Not the region the USER is in: /v1/search
    takes none, which is why the count travels with REGION_NAME beside it."""
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
        # Nothing was asked, so nothing failed: `answering` stays true. The panel
        # draws no empty state for a blank box, so this claims nothing either way.
        return {
            "q": q,
            "answering": True,
            "ships": [],
            "ports": [],
            "seas": [],
            "near": None,
            "searched": {"live": None, "seen_30d": None, "region": REGION_NAME},
        }

    matched = _matching_seas(q)
    ships: list[dict[str, Any]] = []
    near: dict[str, Any] | None = None
    seen_30d: int | None = None
    live: int | None = None
    answering = ch is not None
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
            # beats one that 503s. But the empty list has to say so — Redis may
            # be fine and the map full of ships, and "nothing is transmitting"
            # would be a truth-claim about a question we never got to ask.
            answering = False
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
        "answering": answering,
        "ships": ships,
        # Always empty: there is no ports table until M3's PostGIS polygons, and
        # an invented port is worse than an absent one. The group ships anyway so
        # that filling it is an api change and not a wire change — the client is
        # NOT already shaped for it (Search.tsx's header names the three places
        # that have to move) and would otherwise call three port hits "nothing".
        "ports": [],
        "seas": [
            {"slug": e["slug"], "name": e["name"], "bbox": e["bbox"], "count": count}
            for e, count in zip(matched, counts, strict=True)
        ],
        "near": near,
        # `region` names the box `live` was counted in, so the sentence cannot
        # quote a North Sea number as the Kattegat's.
        "searched": {"live": live, "seen_30d": seen_30d, "region": REGION_NAME},
    }
