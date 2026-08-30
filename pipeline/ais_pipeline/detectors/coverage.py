"""Why a ship went quiet: because nobody was listening, or because she stopped talking.

A gap is the absence of messages, and the absence has two causes that look
identical in the data. Our receivers do not cover the sea evenly — 50 km out
there is one shore station and a ship reports into it once in fifteen minutes,
while off the Maasvlakte a dozen ships are heard every half minute. Silence in
the first place is the ordinary state of things. Silence in the second is worth
a sentence on the page.

So we learn the sea rather than guess it. `density_h3` already counts messages
and distinct ships per 15-minute bucket per h3 cell (res 7); rolled up to res 6
— about 5 km across, the scale at which reception actually varies — a cell gets
three numbers over a trailing week:

  interval_s  median seconds between messages from one ship in that bucket
              (900 * ships / cnt: the bucket's own arithmetic, no assumptions)
  occupancy   the fraction of the window's buckets in which anyone was heard
              there at all — normalised by the buckets the LAKE has, not by the
              wall clock, because our own ingest has holes and they are not the
              cell's fault
  ships       mean distinct ships per bucket

Measured on the live lake, 2026-08-30, 7-day window, 264 of 672 buckets present,
16,656 cells at res 6:

  occupancy    cells   median interval   ships/bucket
  >= 0.9         119        241 s            6.4
  0.5 – 0.9    1,392        300 s            6.3
  0.2 – 0.5    1,345        300 s            1.4
  0.05 – 0.2   3,773        450 s            1.3
  < 0.05      10,027        450 s            1.0

The break is clean and it is in occupancy, not in the interval: the busy lanes
and the port approaches are heard almost every bucket by half a dozen ships,
and the open-sea cells are visited by one ship, briefly, once. The thresholds
below sit on those numbers.

The verdict is taken when the gap OPENS, not when it closes, because that is
when the inputs are true — who else was still being heard, and where she was
last seen. It is stashed on the ship and written out with the row at close.
"""

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

# res 6 ≈ 36 km², ~5 km across. res 7 (what the MV stores) is ~1.2 km across:
# too fine, most cells there are one ship passing once. The spec says 6 and the
# measurement above agrees — 79k cells at res 7 against 17k at res 6.
H3_RES = 6
WINDOW_DAYS = 7
BUCKET_S = 900  # density_h3's bucket, toStartOfFifteenMinutes

CLASS_UNUSUAL = "unusual"
CLASS_COVERAGE = "coverage-likely"
CLASS_UNKNOWN = "coverage-unknown"

# Score bands. Above UNUSUAL_AT we say the silence is hers; below COVERAGE_AT we
# say it is ours; between them we say nothing, which is the honest third answer.
UNUSUAL_AT = 0.60
COVERAGE_AT = 0.35

# Ramps, read off the table above. Occupancy separates the two populations
# (0.2 is the sparse tail, 0.6 is the steady lanes); the interval barely does,
# so it carries less weight and only its ends matter (300 s is a busy cell's
# median, 600 s a thin one's). Together they say how good this patch of sea is.
OCC_LO, OCC_HI = 0.20, 0.60
INT_FAST_S, INT_SLOW_S = 300.0, 600.0
W_OCCUPANCY, W_INTERVAL = 0.55, 0.45

# The neighbours multiply rather than add, because they can veto. A cell that
# normally carries six ships and is carrying none this minute has lost its
# receiver, not its ships, however good its week looks — so no cell can score
# above NEIGHBOUR_FLOOR while nobody in it is being heard. Three still
# transmitting is as much reassurance as a fourth would add.
NEIGHBOURS_FULL = 3.0
NEIGHBOUR_FLOOR = 0.30

# A res-6 hex is ~3.2 km on the edge, so no point inside one is further than
# that from its centre. 0.06° ≈ 6.6 km of latitude leaves room for the shape
# and still refuses a point in the middle of a cell we have no data for.
MAX_CENTRE_DEG = 0.06
_GRID_DEG = 0.1  # index bucket; > MAX_CENTRE_DEG so a 3×3 search is complete


class CellStats(NamedTuple):
    interval_s: float
    occupancy: float
    ships: float


class Verdict(NamedTuple):
    classification: str
    confidence: float  # 0..1; 0.0 when we decline to call it either way
    stats: dict[str, Any]  # the expander's numbers, never a sentence's (F13)


def _ramp(value: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, linear between. Works either direction."""
    if lo == hi:  # pragma: no cover — the constants are never equal
        return 1.0 if value >= hi else 0.0
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


@dataclass(slots=True)
class CoverageModel:
    """Per-cell reception stats, keyed by the cell's centre, answered in memory.

    ponytail: nearest-centre lookup instead of real h3 indexing. Computing an
    h3 cell in Python needs the h3 package, which this project does not have
    and will not add for one function; ClickHouse can do it but not inside a
    pure sync classifier. So the loader asks ClickHouse for each cell's centre
    (h3ToGeo) and we snap a point to the nearest one. The ceiling: near a cell
    boundary the snap can pick the neighbour, which at res 6 means using the
    stats of a hex ~5 km away — and reception does not change over 5 km, which
    is exactly why res 6 was chosen. Swap in the h3 package if that ever stops
    being true.
    """

    cells: dict[tuple[int, int], list[tuple[float, float, CellStats]]]

    def cell_of(self, lat: float, lon: float) -> tuple[float, float] | None:
        """The centre of the cell holding this point, or None if we have no data."""
        best: tuple[float, float] | None = None
        best_d = MAX_CENTRE_DEG * MAX_CENTRE_DEG
        # Degrees of longitude shrink with latitude; without this a point at 60°N
        # snaps to a centre twice as far east as it looks.
        scale = math.cos(math.radians(lat))
        key = (int(lat // _GRID_DEG), int(lon // _GRID_DEG))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for clat, clon, _ in self.cells.get((key[0] + dy, key[1] + dx), ()):
                    d = (clat - lat) ** 2 + ((clon - lon) * scale) ** 2
                    if d < best_d:
                        best_d, best = d, (clat, clon)
        return best

    def stats_at(self, lat: float, lon: float) -> CellStats | None:
        centre = self.cell_of(lat, lon)
        if centre is None:
            return None
        key = (int(centre[0] // _GRID_DEG), int(centre[1] // _GRID_DEG))
        for clat, clon, stats in self.cells.get(key, ()):
            if (clat, clon) == centre:
                return stats
        return None  # pragma: no cover — the centre came out of this index

    def classify(self, lat: float, lon: float, neighbours_online: int = 0) -> Verdict:
        """Is this silence the sea's fault or hers? Numbers only, no words for the page."""
        stats = self.stats_at(lat, lon)
        if stats is None:
            return Verdict(CLASS_UNKNOWN, 0.0, {"neighbors_online": neighbours_online})
        quality = (
            W_OCCUPANCY * _ramp(stats.occupancy, OCC_LO, OCC_HI)
            + W_INTERVAL * _ramp(stats.interval_s, INT_SLOW_S, INT_FAST_S)
        )
        heard = NEIGHBOUR_FLOOR + (1.0 - NEIGHBOUR_FLOOR) * _ramp(
            neighbours_online, 0.0, NEIGHBOURS_FULL)
        score = quality * heard
        numbers = {
            "cell_interval_s": round(stats.interval_s),
            "cell_occupancy": round(stats.occupancy, 3),
            "cell_ships": round(stats.ships, 2),
            "neighbors_online": neighbours_online,
        }
        if score >= UNUSUAL_AT:
            return Verdict(CLASS_UNUSUAL, round(score, 2), numbers)
        if score <= COVERAGE_AT:
            return Verdict(CLASS_COVERAGE, round(1.0 - score, 2), numbers)
        return Verdict(CLASS_UNKNOWN, 0.0, numbers)


def build(rows: Sequence[Sequence[Any]]) -> CoverageModel:
    """(lat, lon, interval_s, occupancy, ships) rows -> the grid index."""
    cells: dict[tuple[int, int], list[tuple[float, float, CellStats]]] = {}
    for lat, lon, interval_s, occupancy, ships in rows:
        key = (int(float(lat) // _GRID_DEG), int(float(lon) // _GRID_DEG))
        cells.setdefault(key, []).append(
            (float(lat), float(lon),
             CellStats(float(interval_s), float(occupancy), float(ships)))
        )
    return CoverageModel(cells)


# One query, one pass over density_h3's last week. Everything h3 happens here,
# in ClickHouse, because that is the only place in this process that can.
COVERAGE_QUERY = f"""
WITH
    greatest(1, (SELECT uniq(bucket) FROM density_h3
                 WHERE bucket >= now() - INTERVAL {WINDOW_DAYS} DAY)) AS total,
    buckets AS (
        SELECT h3ToParent(h3, {H3_RES}) AS cell,
               bucket,
               sum(cnt) AS cnt,
               uniqCombinedMerge(12)(ships) AS ships
        FROM density_h3
        WHERE bucket >= now() - INTERVAL {WINDOW_DAYS} DAY
        GROUP BY cell, bucket
    )
SELECT centre.1, centre.2, interval_s, occupancy, ships_per_bucket
FROM (
    SELECT h3ToGeo(cell) AS centre,
           median({BUCKET_S}.0 * ships / cnt) AS interval_s,
           count() / total AS occupancy,
           avg(ships) AS ships_per_bucket
    FROM buckets
    GROUP BY cell
)
"""


async def load_coverage(
    fetch: Callable[[str], Awaitable[Sequence[Sequence[Any]]]],
) -> CoverageModel:
    """Read the week's reception stats out of the lake and index them."""
    return build(await fetch(COVERAGE_QUERY))


__all__ = [
    "CLASS_COVERAGE",
    "CLASS_UNKNOWN",
    "CLASS_UNUSUAL",
    "CellStats",
    "CoverageModel",
    "Verdict",
    "build",
    "load_coverage",
]
