"""/v1/map/snapshot against a fake hash — no Redis, no network."""

import json

import pytest

from app.map import SnapshotUnavailable, parse_bbox, snapshot_payload


def field(ts: int, lat: float, lon: float, sog: float, cog: float, state: str) -> str:
    """Exactly what refinery/redis_sink.latest_field writes: sog BEFORE cog."""
    return json.dumps([ts, lat, lon, sog, cog, state])


class FakeRedis:
    def __init__(self, hash_: dict[str, str]) -> None:
        self._hash = hash_

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hash)


class DeadRedis:
    async def hgetall(self, name: str) -> dict[str, str]:
        raise ConnectionError("redis is down")


NORTH_SEA = {
    "244660000": field(1789000000, 55.1, 3.2, 12.4, 087.0, "underway"),
    "205344000": field(1789000001, 51.9, 4.1, 0.0, 210.0, "moored"),
}


async def test_field_order_is_transposed_to_cog_then_sog() -> None:
    payload = await snapshot_payload(FakeRedis(NORTH_SEA))
    vessels = {v[0]: v for v in payload["vessels"]}
    assert vessels[244660000] == [244660000, 55.1, 3.2, 87.0, 12.4, "underway"]
    assert payload["count"] == 2
    assert payload["region"]


async def test_bbox_culls_everything_outside() -> None:
    payload = await snapshot_payload(FakeRedis(NORTH_SEA), (2.0, 54.0, 5.0, 57.0))
    assert [v[0] for v in payload["vessels"]] == [244660000]
    assert payload["count"] == 1


async def test_dead_redis_is_not_an_empty_sea() -> None:
    with pytest.raises(SnapshotUnavailable):
        await snapshot_payload(DeadRedis())
    with pytest.raises(SnapshotUnavailable):
        await snapshot_payload(None)


async def test_garbage_fields_are_skipped() -> None:
    junk = {"1": "not json", "2": json.dumps([1, 2]), "3": NORTH_SEA["244660000"]}
    payload = await snapshot_payload(FakeRedis(junk))
    assert payload["count"] == 1


async def test_garbage_keys_are_skipped_not_fatal() -> None:
    mixed = {"ship-abc": NORTH_SEA["244660000"], "244660000": NORTH_SEA["244660000"]}
    payload = await snapshot_payload(FakeRedis(mixed))
    assert payload["count"] == 1


def test_bbox_parsing() -> None:
    assert parse_bbox("2,54,5,57") == (2.0, 54.0, 5.0, 57.0)
    for bad in (
        "2,54,5",  # arity
        "2,54,5,57,9",
        "north sea",
        "5,57,2,54",  # inverted corners -> empty-sea lie, must 422 instead
        "-200,54,5,57",  # out of range
    ):
        with pytest.raises(ValueError):
            parse_bbox(bad)


async def test_route_maps_bad_bbox_to_422_and_dead_redis_to_503() -> None:
    from fastapi import HTTPException

    from app.main import map_snapshot, runtime

    original = runtime.redis
    try:
        runtime.redis = FakeRedis(NORTH_SEA)  # type: ignore[assignment]
        with pytest.raises(HTTPException) as err:
            await map_snapshot(bbox="north sea")
        assert err.value.status_code == 422

        runtime.redis = DeadRedis()  # type: ignore[assignment]
        with pytest.raises(HTTPException) as err:
            await map_snapshot()
        assert err.value.status_code == 503
    finally:
        runtime.redis = original
