"""GET /v1/ships/{id}/track — the line the replay draws (S2, F14).

A GeoJSON Feature holding one LineString and, beside the coordinates, a parallel
`times` array: the scrubber needs a timestamp per point, and GeoJSON has nowhere
to put one. Same length, same order, index for index.

Two tables, one shape. `positions` keeps 90 days and is the truth; `positions_5m`
keeps a point every five minutes forever. A window that starts inside the raw
retention is read raw, an older one falls back to the downsample — the caller
never chooses, because the choice is a fact about storage and not about the ship.

`gaps` are the story's own gap events, handed over as spans so the line can be
dashed through the silence rather than drawn straight across it: without them a
26-hour hole reads as a leisurely crossing.
"""

import logging
import time
from typing import Any

from .limits import STORY_WINDOW_D
from .ships import ClickHouseClient, _epoch
from .story import DAY_S, StoryUnavailable, _query, clamp_window, resolve_mmsi

logger = logging.getLogger("track")

RAW_TTL_D = 90  # positions' TTL (lake tables migration) — older than this, use the MV.
DEFAULT_SIMPLIFY = 0.0005  # ~55 m at the equator: keeps the shape, drops the jitter.
MAX_SIMPLIFY = 0.05  # a degree-scale epsilon would straighten a voyage into one leg.
MAX_POINTS = 5_000  # what a replay can animate; beyond it the epsilon is raised.

RAW_QUERY = """
SELECT ts, lat, lon FROM positions
WHERE mmsi = %(mmsi)s AND ts >= toDateTime(%(from)s) AND ts < toDateTime(%(to)s)
ORDER BY ts
LIMIT 500000
"""
# The LIMIT is a memory backstop, not a feature: 30 days of a chatty ship is
# ~250k rows, and nothing should be able to pull an unbounded set into this
# process before Douglas-Peucker ever sees it.
# ReplacingMergeTree without FINAL: argMax over ts picks the newest row per
# bucket whether or not the parts have merged yet (vessel_latest's reasoning).
FIVE_M_QUERY = """
SELECT ts5, argMax(lat, ts), argMax(lon, ts) FROM positions_5m
WHERE mmsi = %(mmsi)s AND ts5 >= toDateTime(%(from)s) AND ts5 < toDateTime(%(to)s)
GROUP BY ts5 ORDER BY ts5
"""
GAPS_QUERY = """
SELECT t_start, t_end FROM events
WHERE mmsi = %(mmsi)s AND kind = 'gap'
  AND t_start >= toDateTime(%(from)s) AND t_start < toDateTime(%(to)s)
ORDER BY t_start
"""


def douglas_peucker(points: list[tuple[float, float]], epsilon: float) -> list[int]:
    """Indices of the points worth keeping — iterative, so a long track cannot
    blow the stack the way the textbook recursion does."""
    if len(points) < 3 or epsilon <= 0:
        return list(range(len(points)))
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        worst, worst_i = -1.0, first
        for i in range(first + 1, last):
            x, y = points[i]
            # Distance to the segment's line; for a closed loop (norm == 0) the
            # cross product is zero for every point, so fall back to the radius.
            d = (
                abs(dx * (y1 - y) - (x1 - x) * dy) / norm
                if norm
                else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
            )
            if d > worst:
                worst, worst_i = d, i
        if worst > epsilon:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [i for i, k in enumerate(keep) if k]


async def track_payload(
    ch: ClickHouseClient | None,
    key: str,
    from_: Any = None,
    to: Any = None,
    simplify: Any = None,
    now: float | None = None,
    window_d: int = STORY_WINDOW_D,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    mmsi = await resolve_mmsi(ch, key)
    assert ch is not None  # resolve_mmsi raised otherwise
    from_ts, to_ts = clamp_window(from_, to, now, window_d)

    query = RAW_QUERY if from_ts >= int(now) - RAW_TTL_D * DAY_S else FIVE_M_QUERY
    params = {"mmsi": mmsi, "from": from_ts, "to": to_ts}
    rows = (await _query(ch, query, params)).result_rows

    times = [_epoch(r[0]) for r in rows]
    points = [(float(r[2]), float(r[1])) for r in rows]  # GeoJSON is lon, lat

    try:
        epsilon = min(abs(float(simplify)), MAX_SIMPLIFY)
    except (TypeError, ValueError):
        epsilon = DEFAULT_SIMPLIFY
    kept = douglas_peucker(points, epsilon)
    while len(kept) > MAX_POINTS and epsilon < MAX_SIMPLIFY:
        epsilon = min(epsilon * 4 or DEFAULT_SIMPLIFY, MAX_SIMPLIFY)
        kept = douglas_peucker(points, epsilon)
    if len(kept) > MAX_POINTS:
        # Even MAX_SIMPLIFY kept too much: thin by a uniform stride rather than
        # truncating, which would amputate the tail of the voyage. First and last
        # indices always survive — the line must still end where she ended.
        stride = -(-len(kept) // MAX_POINTS)
        kept = kept[::stride] + ([kept[-1]] if (len(kept) - 1) % stride else [])

    gap_rows = (await _query(ch, GAPS_QUERY, params)).result_rows
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[round(points[i][0], 5), round(points[i][1], 5)] for i in kept],
        },
        "properties": {
            "mmsi": mmsi,
            "from": from_ts,
            "to": to_ts,
            "window_d": window_d,
            "source": "positions" if query is RAW_QUERY else "positions_5m",
            "simplify": epsilon,
            "times": [times[i] for i in kept],
        },
        "gaps": [
            {"t_start": _epoch(r[0]), "t_end": None if r[1] is None else _epoch(r[1])}
            for r in gap_rows
        ],
    }


__all__ = ["StoryUnavailable", "douglas_peucker", "track_payload"]
