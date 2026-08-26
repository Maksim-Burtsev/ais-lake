"""/status.json assembly against fake stores — no ClickHouse, no Redis, no network."""

import json
from typing import Any

from app.status import build_status, last_connect

PARTS_ROWS = [("positions", 1_000_000, 24_000_000), ("vessel_latest", 12_000, 600_000)]


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, rate: float = 812.5, parts: list[tuple[Any, ...]] | None = None) -> None:
        self.rate = rate
        self.parts = PARTS_ROWS if parts is None else parts
        self.queries: list[str] = []

    async def query(self, query: str) -> FakeResult:
        self.queries.append(query)
        if "system.parts" in query:
            return FakeResult(self.parts)
        return FakeResult([(self.rate,)])


class DeadClickHouse:
    async def query(self, query: str) -> FakeResult:
        raise ConnectionError("clickhouse is down")


class FakeRedis:
    def __init__(self, status: dict[str, str], incidents: list[str]) -> None:
        self._status = status
        self._incidents = incidents

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._status)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        return self._incidents[start : None if end == -1 else end + 1]


class DeadRedis:
    async def hgetall(self, name: str) -> dict[str, str]:
        raise ConnectionError("redis is down")

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        raise ConnectionError("redis is down")


def incident(event: str, ts: int, **fields: Any) -> str:
    return json.dumps({"ts": ts, "event": event, **fields})


STATUS_HASH = {"in": "1200", "out": "900", "deduped": "300", "dedup_ratio": "0.250",
               "tracked": "8421", "ts": "1789000000"}
INCIDENTS = [
    incident("ws_disconnect", 1789000300, reason="ConnectionClosedError"),
    incident("flush_failed", 1789000200, reason="TimeoutError"),
    incident("ws_connect", 1789000100, url="wss://stream.aisstream.io/v0/stream"),
    incident("ws_connect", 1789000000, url="wss://stream.aisstream.io/v0/stream"),
]


async def test_full_payload() -> None:
    ch = FakeClickHouse()
    payload = await build_status(ch, FakeRedis(STATUS_HASH, INCIDENTS), uptime_s=123.456)

    assert payload["uptime_s"] == 123.5
    assert payload["msg_per_s_1m"] == 812.5

    refinery = payload["refinery"]
    assert refinery == {"in": 1200, "out": 900, "deduped": 300, "dedup_ratio": 0.25,
                        "tracked": 8421, "ts": 1789000000}

    lake = payload["lake"]
    assert lake["total_gb"] == round(24_600_000 / 1024**3, 3)
    positions = lake["tables"][0]
    assert positions == {"table": "positions", "rows": 1_000_000, "bytes": 24_000_000,
                         "bytes_per_row": 24.0}

    assert payload["last_ws_connect"]["ts"] == 1789000100  # the newest connect, not the oldest
    assert [e["event"] for e in payload["incidents"]][0] == "ws_disconnect"


async def test_incidents_are_capped_at_ten() -> None:
    many = [incident("flush_failed", 1789000000 + i) for i in range(25)]
    payload = await build_status(FakeClickHouse(), FakeRedis({}, many), uptime_s=1.0)
    assert len(payload["incidents"]) == 10
    assert payload["refinery"] is None          # empty hash reads as "no refinery yet"
    assert payload["last_ws_connect"] is None   # no connect in the log


async def test_dead_stores_degrade_to_nulls() -> None:
    payload = await build_status(DeadClickHouse(), DeadRedis(), uptime_s=5.0)
    assert payload["msg_per_s_1m"] is None
    assert payload["refinery"] is None
    assert payload["lake"] == {"tables": [], "total_gb": None}
    assert payload["incidents"] == [] and payload["last_ws_connect"] is None
    assert payload["uptime_s"] == 5.0


async def test_missing_stores_are_not_an_error() -> None:
    payload = await build_status(None, None, uptime_s=0.0)
    assert payload["msg_per_s_1m"] is None and payload["refinery"] is None
    assert payload["lake"]["tables"] == []


async def test_garbage_incident_entries_are_skipped() -> None:
    junk = ["not json", json.dumps([1, 2, 3]), incident("ws_connect", 1789000000)]
    payload = await build_status(None, FakeRedis({}, junk), uptime_s=0.0)
    assert len(payload["incidents"]) == 1
    assert payload["last_ws_connect"] is not None


async def test_empty_table_has_no_bytes_per_row() -> None:
    ch = FakeClickHouse(parts=[("events", 0, 0)])
    payload = await build_status(ch, None, uptime_s=0.0)
    assert payload["lake"]["tables"][0]["bytes_per_row"] is None


def test_last_connect_picks_the_first_match() -> None:
    assert last_connect([]) is None
