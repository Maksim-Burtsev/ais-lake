"""/v1/ships/{key}/track against a fake lake — the line, its clock and its silences."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.story import DAY_S
from app.track import DEFAULT_SIMPLIFY, douglas_peucker, track_payload
from tests.test_story import MMSI, FakeResult

NOW = time.time()
T0 = datetime.fromtimestamp(NOW - 3 * DAY_S, UTC).replace(tzinfo=None, microsecond=0)

# A straight run east with one point nudged off the line, then a real corner.
FIXES = [(51.0, 4.0), (51.0, 4.1), (51.0, 4.2), (51.0, 4.3), (51.5, 4.4)]
POSITIONS = [(T0 + timedelta(minutes=10 * i), lat, lon) for i, (lat, lon) in enumerate(FIXES)]
GAP = (T0 + timedelta(minutes=15), T0 + timedelta(minutes=25))


class FakeClickHouse:
    """Positions from one fixture, gaps from another; remembers which table it read."""

    def __init__(self, positions: list[tuple[Any, ...]], gaps: list[tuple[Any, ...]]) -> None:
        self.positions, self.gaps = positions, gaps
        self.tables: list[str] = []

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        if "events" in query:
            return FakeResult(self.gaps)
        self.tables.append("positions_5m" if "positions_5m" in query else "positions")
        return FakeResult(self.positions)


async def track(**kwargs: Any) -> dict[str, Any]:
    ch = FakeClickHouse(POSITIONS, [GAP])
    payload = await track_payload(ch, str(MMSI), now=NOW, **kwargs)
    payload["_tables"] = ch.tables
    return payload


def test_collinear_points_collapse_and_epsilon_is_respected() -> None:
    line = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    assert douglas_peucker(line, 0.001) == [0, 3]
    bump = [(0.0, 0.0), (1.0, 0.01), (2.0, 0.0)]
    assert douglas_peucker(bump, 0.1) == [0, 2]  # under epsilon: dropped
    assert douglas_peucker(bump, 0.001) == [0, 1, 2]  # over it: kept


@pytest.mark.asyncio
async def test_line_carries_a_time_per_point() -> None:
    payload = await track()
    coords = payload["geometry"]["coordinates"]
    times = payload["properties"]["times"]
    assert payload["type"] == "Feature" and payload["geometry"]["type"] == "LineString"
    assert len(coords) == len(times)
    assert coords[0] == [4.0, 51.0]  # lon, lat — GeoJSON order, not the table's
    assert times == sorted(times)
    # The straight run collapses; the corner survives.
    assert len(coords) < len(FIXES)
    assert coords[-1] == [4.4, 51.5]


@pytest.mark.asyncio
async def test_simplify_param_beats_the_default() -> None:
    assert (await track())["properties"]["simplify"] == DEFAULT_SIMPLIFY
    dense = await track(simplify=0.0)
    assert len(dense["geometry"]["coordinates"]) == len(FIXES)
    assert (await track(simplify="banana"))["properties"]["simplify"] == DEFAULT_SIMPLIFY


@pytest.mark.asyncio
async def test_gaps_come_back_as_spans() -> None:
    payload = await track()
    gap = payload["gaps"][0]
    assert gap["t_end"] - gap["t_start"] == 600


@pytest.mark.asyncio
async def test_recent_window_reads_raw_and_an_old_one_reads_the_downsample() -> None:
    assert (await track())["_tables"] == ["positions"]
    # Beyond the 90-day retention there is no raw row left to read — only the MV.
    old = NOW - 200 * DAY_S
    ch = FakeClickHouse(POSITIONS, [])
    await track_payload(ch, str(MMSI), from_=old, to=old + DAY_S, now=NOW, window_d=365)
    assert ch.tables == ["positions_5m"]
