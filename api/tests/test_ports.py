"""The ports endpoints against a fake pool — no Postgres, no network."""

import json
from typing import Any

import pytest

import app.ports as ports_module
from app.ports import PortsUnavailable, port_payload, ports_geojson

SQUARE = {"type": "Polygon", "coordinates": [[[4, 51], [5, 51], [5, 52], [4, 52], [4, 51]]]}


def row(locode: str, name: str, anchorage: bool = False) -> dict[str, Any]:
    """One row as asyncpg hands it over: ST_AsGeoJSON already rendered to text."""
    return {
        "locode": locode,
        "name": name,
        "kind": "port",
        "geom": json.dumps(SQUARE),
        "anchorages": json.dumps(SQUARE) if anchorage else None,
    }


# The shape of the real table: twelve ports, three of them with an anchorage.
ROWS = [row(f"NL{i:03d}", f"Port {i}", anchorage=i < 3) for i in range(12)]


class FakePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = ROWS if rows is None else rows
        self.calls = 0

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls += 1
        return list(self._rows)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return next((r for r in self._rows if r["locode"] == args[0]), None)


class DeadPool:
    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        raise ConnectionError("postgres is down")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        raise ConnectionError("postgres is down")


def ship(mmsi: int, port: str, zone: str) -> str:
    """One snapshot field, exactly as ShipState.to_json writes it."""
    return json.dumps(
        {
            "mmsi": mmsi,
            "last_fix": 1756000000,
            "motion": "anchored" if zone == "anchorage" else "moored",
            "still_since": 1755990000,
            "moving_since": None,
            "draught": 7.2,
            "anchorage_id": None,
            "gap_id": None,
            "port": port,
            "zone": zone,
            "seeded": False,
        },
        separators=(",", ":"),
    )


# Two waiting at NL000, one berthed there, one waiting at the port next door.
SNAPSHOT = {
    "1": ship(1, "NL000", "anchorage"),
    "2": ship(2, "NL000", "anchorage"),
    "3": ship(3, "NL000", "berth"),
    "4": ship(4, "NL001", "anchorage"),
    "5": ship(5, "", ""),
}


class FakeRedis:
    def __init__(self, fields: dict[str, str] | None = None) -> None:
        self._fields = SNAPSHOT if fields is None else fields

    async def hgetall(self, name: str) -> dict[str, str]:
        assert name == "detector:north-sea"
        return dict(self._fields)

    async def hget(self, name: str, key: str) -> str | None:  # pragma: no cover
        return self._fields.get(key)


class DeadRedis(FakeRedis):
    async def hgetall(self, name: str) -> dict[str, str]:
        raise ConnectionError("redis is down")


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, seconds: float | None = 5400.0) -> None:
        self.params: dict[str, Any] = {}
        self._seconds = seconds

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        self.params = parameters
        return FakeResult([(self._seconds,)])


class DeadClickHouse:
    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        raise ConnectionError("clickhouse is down")


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    ports_module._cache = None
    yield
    ports_module._cache = None


async def test_every_port_is_a_feature_and_every_anchorage_one_more() -> None:
    payload = await ports_geojson(FakePool())
    assert payload["type"] == "FeatureCollection"
    kinds = [f["properties"]["kind"] for f in payload["features"]]
    assert kinds.count("port") == 12
    assert kinds.count("anchorage") == 3
    assert len(payload["features"]) == 15
    first = payload["features"][0]
    assert first["geometry"] == SQUARE
    assert first["properties"] == {"locode": "NL000", "name": "Port 0", "kind": "port"}


async def test_the_polygons_are_read_once_per_process() -> None:
    pool = FakePool()
    assert await ports_geojson(pool) == await ports_geojson(pool)
    assert pool.calls == 1


async def test_a_failed_read_is_not_cached() -> None:
    with pytest.raises(PortsUnavailable):
        await ports_geojson(DeadPool())
    payload = await ports_geojson(FakePool())
    assert len(payload["features"]) == 15


async def test_no_postgres_is_a_503_not_an_empty_map() -> None:
    with pytest.raises(PortsUnavailable):
        await ports_geojson(None)
    with pytest.raises(PortsUnavailable):
        await port_payload(None, None, None, "NL000")
    with pytest.raises(PortsUnavailable):
        await port_payload(DeadPool(), None, None, "NL000")


async def test_the_panel_counts_its_own_anchorage_and_nobody_else_s() -> None:
    ch = FakeClickHouse()
    payload = await port_payload(FakePool(), FakeRedis(), ch, "NL000")
    assert payload == {
        "locode": "NL000",
        "name": "Port 0",
        "waiting_now": 2,  # the berthed ship and the neighbour's queue do not count
        "typical_wait_h": 1.5,
        "band30d": None,
    }
    assert ch.params == {"locode": "NL000", "days": 7}


async def test_a_store_that_cannot_answer_leaves_null_never_zero() -> None:
    payload = await port_payload(FakePool(), DeadRedis(), DeadClickHouse(), "NL000")
    assert payload is not None
    assert payload["waiting_now"] is None and payload["typical_wait_h"] is None
    # no stores at all, and no snapshot written yet: same nulls.
    for redis_client, ch in ((None, None), (FakeRedis(fields={}), FakeClickHouse(seconds=None))):
        payload = await port_payload(FakePool(), redis_client, ch, "NL000")
        assert payload is not None
        assert payload["waiting_now"] is None and payload["typical_wait_h"] is None


async def test_a_lowercase_locode_finds_the_same_port() -> None:
    assert await port_payload(FakePool(), None, None, "nl000") == await port_payload(
        FakePool(), None, None, "NL000"
    )


async def test_an_unknown_locode_is_none_so_the_route_can_404() -> None:
    assert await port_payload(FakePool(), None, None, "XXXXX") is None


async def test_an_empty_table_is_served_but_never_cached() -> None:
    # migrate ran, make geo hasn't: the next call must hit the table again.
    empty = FakePool(rows=[])
    assert (await ports_geojson(empty))["features"] == []
    assert len((await ports_geojson(FakePool()))["features"]) == 15


async def test_routes_map_no_postgres_to_503_and_unknown_locode_to_404() -> None:
    from fastapi import HTTPException

    from app.main import map_ports, port, runtime

    original = runtime.postgres
    try:
        runtime.postgres = None
        with pytest.raises(HTTPException) as err:
            await map_ports()
        assert err.value.status_code == 503
        with pytest.raises(HTTPException) as err:
            await port("NLRTM")
        assert err.value.status_code == 503

        runtime.postgres = FakePool()  # type: ignore[assignment]
        with pytest.raises(HTTPException) as err:
            await port("XXXXX")
        assert err.value.status_code == 404
        assert (await map_ports())["type"] == "FeatureCollection"
        assert (await port("NL000"))["waiting_now"] is None
    finally:
        runtime.postgres = original
