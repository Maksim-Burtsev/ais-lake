"""/v1/ships/{key}/story against a fake lake — golden prose, one string per kind."""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.limits import STORY_WINDOW_D
from app.ships import ShipNotFound
from app.story import DAY_S, StoryUnavailable, clamp_window, story_payload

MMSI = 249118000
NOW = time.time()
T0 = datetime.fromtimestamp(NOW - 3 * DAY_S, UTC).replace(tzinfo=None, microsecond=0)


def event(
    kind: str, port: str = "", meta: dict[str, Any] | None = None, hours: float = 2
) -> tuple[Any, ...]:
    """One events row as clickhouse-connect hands it over: meta already toString'd."""
    return (
        f"ev-{kind}",
        kind,
        T0,
        T0 + timedelta(hours=hours),
        port,
        json.dumps(meta or {}),
    )


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, rows: list[tuple[Any, ...]], mmsi: int = MMSI) -> None:
        self.rows, self.mmsi, self.params = rows, mmsi, {}

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        if "%(imo)s" in query:
            return FakeResult([(self.mmsi,)])
        self.params = parameters
        return FakeResult(self.rows if parameters["mmsi"] == self.mmsi else [])


class DeadClickHouse:
    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        raise ConnectionError("clickhouse is down")


class FakePool:
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return [{"locode": k, "name": v} for k, v in self.names.items() if k in args[0]]


ROTTERDAM = FakePool({"NLRTM": "Rotterdam"})


async def prose(row: tuple[Any, ...], pool: Any = ROTTERDAM) -> str:
    payload = await story_payload(FakeClickHouse([row]), pool, str(MMSI), now=NOW)
    return str(payload["events"][0]["prose"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (event("port_call", "NLRTM", {"duration_s": 2 * DAY_S}), "Moored in Rotterdam — 2 days"),
        (event("port_call", "", {"duration_s": 2 * DAY_S}), "Moored — 2 days"),
        (event("departure", "NLRTM"), "Left Rotterdam"),
        (event("departure", ""), "Under way again"),
        (event("anchorage", "NLRTM", {"duration_s": 14 * 3600}), "Waited off Rotterdam — 14 hours"),
        (event("anchorage", "", {"duration_s": 14 * 3600}), "Waited at anchor — 14 hours"),
        (event("gap", "", {"duration_s": 26 * 3600}), "Went silent — 26 hours"),
        (
            event("load_delta", "", {"from": 5.1, "to": 7.2}),
            "Loaded — draught 7.2 m, up from 5.1",
        ),
        (
            event("load_delta", "", {"from": 7.2, "to": 5.1}),
            "Discharged — draught 5.1 m, down from 7.2",
        ),
    ],
)
async def test_golden_prose(row: tuple[Any, ...], expected: str) -> None:
    assert await prose(row) == expected


@pytest.mark.asyncio
async def test_port_name_missing_falls_back_to_the_nameless_sentence() -> None:
    """Postgres down costs the name, never the sentence."""
    assert await prose(event("port_call", "NLRTM", {"duration_s": 2 * DAY_S}), None) == (
        "Moored — 2 days"
    )


@pytest.mark.asyncio
async def test_unusual_gap_flags_but_never_says_so() -> None:
    row = event(
        "gap",
        "",
        {
            "duration_s": 26 * 3600,
            "classification": "unusual",
            "confidence": 0.82,
            "cell_ships": 41,
            "neighbors_online": 12,
        },
    )
    payload = await story_payload(FakeClickHouse([row]), ROTTERDAM, str(MMSI), now=NOW)
    ev = payload["events"][0]
    assert ev["prose"] == "Went silent — 26 hours"
    assert "unusual" not in ev["prose"].lower() and "0.82" not in ev["prose"]
    assert ev["flag"] == {
        "label": "Unusual for this area",
        "confidence": 0.82,
        "cell_ships": 41,
        "neighbors_online": 12,
    }
    assert ev["numbers"] == {
        "classification": "unusual",
        "confidence": 0.82,
        "cell_ships": 41,
        "neighbors_online": 12,
    }


@pytest.mark.asyncio
async def test_ordinary_gap_carries_numbers_but_no_flag() -> None:
    """F13: the gap view is the expander, so every silence carries its evidence."""
    row = event(
        "gap", "", {"duration_s": 26 * 3600, "classification": "expected", "cell_occupancy": 0.4}
    )
    ev = (await story_payload(FakeClickHouse([row]), ROTTERDAM, str(MMSI), now=NOW))["events"][0]
    assert "flag" not in ev
    assert ev["numbers"] == {"classification": "expected", "cell_occupancy": 0.4}


@pytest.mark.asyncio
async def test_only_gaps_carry_numbers() -> None:
    row = event("port_call", "NLRTM", {"duration_s": DAY_S, "classification": "unusual"})
    assert "numbers" not in (
        await story_payload(FakeClickHouse([row]), ROTTERDAM, str(MMSI), now=NOW)
    )["events"][0]


@pytest.mark.asyncio
async def test_window_is_clamped_and_declared() -> None:
    """A hand-written ?from= three years back reads the last 30 days, not an error."""
    ch = FakeClickHouse([])
    payload = await story_payload(
        ch, ROTTERDAM, str(MMSI), from_=NOW - 1000 * DAY_S, to=NOW, now=NOW
    )
    assert payload["from"] == int(NOW) - STORY_WINDOW_D * DAY_S == ch.params["from"]
    assert payload["window_d"] == STORY_WINDOW_D
    assert str(STORY_WINDOW_D) in payload["limit_line"]
    assert payload["track"] == f"/v1/ships/{MMSI}/track?from={payload['from']}&to={payload['to']}"


def test_clamp_window_defaults_to_the_whole_window_ending_now() -> None:
    assert clamp_window("banana", None, NOW) == (
        int(NOW) - STORY_WINDOW_D * DAY_S,
        int(NOW),
    )


def test_clamp_window_pulls_a_future_to_back_to_now() -> None:
    assert clamp_window(None, 9_999_999_999, NOW) == (
        int(NOW) - STORY_WINDOW_D * DAY_S,
        int(NOW),
    )


@pytest.mark.asyncio
async def test_unknown_key_404s_and_a_dead_lake_503s() -> None:
    with pytest.raises(ShipNotFound):
        await story_payload(FakeClickHouse([]), ROTTERDAM, "banana")
    with pytest.raises(StoryUnavailable):
        await story_payload(DeadClickHouse(), ROTTERDAM, str(MMSI))
    with pytest.raises(StoryUnavailable):
        await story_payload(None, ROTTERDAM, str(MMSI))
