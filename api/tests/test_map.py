"""/v1/map/snapshot against a fake hash — no Redis, no network."""

import json
import time

import pytest

from app.limits import MAX_VESSEL_AGE_S, SILENT_AFTER_S
from app.map import SnapshotUnavailable, parse_bbox, snapshot_payload

# _rows cuts on age against the wall clock, so fixtures are dated relative to it —
# a literal epoch would quietly go stale and take every test here with it.
NOW = int(time.time())
FRESH = NOW - 60
STALE = NOW - MAX_VESSEL_AGE_S - 60


def field(ts: int, lat: float, lon: float, sog: float, cog: float, state: str,
          sym: str | None = "cargo3") -> str:
    """Exactly what refinery/redis_sink.latest_field writes: sog BEFORE cog.
    `sym=None` is a field written before the sprite token existed."""
    row = [ts, lat, lon, sog, cog, state]
    return json.dumps(row if sym is None else [*row, sym])


class FakeRedis:
    def __init__(self, hash_: dict[str, str]) -> None:
        self._hash = hash_

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hash)


class DeadRedis:
    async def hgetall(self, name: str) -> dict[str, str]:
        raise ConnectionError("redis is down")


NORTH_SEA = {
    "244660000": field(FRESH, 55.1, 3.2, 12.4, 087.0, "underway"),
    "205344000": field(FRESH, 51.9, 4.1, 0.0, 210.0, "moored"),
}


async def test_field_order_is_transposed_to_cog_then_sog() -> None:
    payload = await snapshot_payload(FakeRedis(NORTH_SEA))
    vessels = {v[0]: v for v in payload["vessels"]}
    assert vessels[244660000] == [244660000, 55.1, 3.2, 87.0, 12.4, "underway", "cargo3"]
    assert payload["count"] == 2
    assert payload["region"]


async def test_six_element_fields_still_read_as_an_unknown_silhouette() -> None:
    old = {"244660000": field(FRESH, 55.1, 3.2, 12.4, 87.0, "underway", sym=None)}
    payload = await snapshot_payload(FakeRedis(old))
    assert payload["vessels"] == [[244660000, 55.1, 3.2, 87.0, 12.4, "underway", "unknown2"]]


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


async def test_text_coordinates_are_skipped_everywhere_not_fatal() -> None:
    """A non-numeric lat would blow up every bbox compare — snapshot AND counts."""
    from app.map import counts_for
    from app.regions import regions_payload

    bad = {
        "1": json.dumps([FRESH, "fifty-five", 3.2, 12.4, 87.0, "underway", "cargo3"]),
        "244660000": NORTH_SEA["244660000"],
    }
    payload = await snapshot_payload(FakeRedis(bad))
    assert payload["count"] == 1
    assert await counts_for(FakeRedis(bad), [(2.0, 54.0, 5.0, 57.0)]) == [1]
    counts = [r["count"] for r in (await regions_payload(FakeRedis(bad)))["regions"]]
    assert any(c is not None for c in counts)  # counts still come back, no exception


async def test_dict_and_overlong_fields_are_skipped() -> None:
    junk = {
        "1": json.dumps({"ts": 1, "lat": 55.1}),  # a dict would unpack into key names
        "2": json.dumps([FRESH, 55.1, 3.2, 12.4, 87.0, "underway", "cargo3", "extra"]),
        "3": NORTH_SEA["244660000"],
    }
    payload = await snapshot_payload(FakeRedis(junk))
    assert payload["count"] == 1


def test_a_ship_goes_silent_before_she_leaves_the_map() -> None:
    """The two windows must stay in this order or F7 has nothing to show.

    Set them equal and a ship goes silent in the same instant she vanishes from
    the snapshot, so the "Recently silent" chip can never brighten anybody — which
    is exactly the bug this ordering was introduced to fix.
    """
    assert SILENT_AFTER_S < MAX_VESSEL_AGE_S


async def test_fixes_older_than_the_age_cut_are_off_the_map() -> None:
    """The hash never expires a field, so the ghosts have to be cut on read."""
    fleet = {
        "244660000": NORTH_SEA["244660000"],
        "205344000": field(STALE, 55.2, 3.3, 0.0, 12.0, "underway"),
    }
    payload = await snapshot_payload(FakeRedis(fleet))
    assert [v[0] for v in payload["vessels"]] == [244660000]
    assert payload["count"] == 1


async def test_a_text_ts_is_skipped_not_fatal() -> None:
    bad = {
        "1": json.dumps(["yesterday", 55.1, 3.2, 12.4, 87.0, "underway", "cargo3"]),
        "244660000": NORTH_SEA["244660000"],
    }
    payload = await snapshot_payload(FakeRedis(bad))
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
