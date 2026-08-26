from datetime import UTC, datetime, timedelta

from ais_pipeline.refinery.dedup import Dedup, key_of
from ais_pipeline.refinery.models import PositionRow

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def row(mmsi: int = 244660000, lat: float = 52.0, lon: float = 4.0,
        ts: datetime = T0, sog: float = 8.0) -> PositionRow:
    return PositionRow(ts=ts, mmsi=mmsi, lat=lat, lon=lon, sog=sog, cog=90.0,
                       heading=90, nav_status=0, msg_type=1, src="aisstream")


def make(ttl_s: float = 60.0, max_keys: int = 1000) -> tuple[Dedup, FakeClock]:
    clock = FakeClock()
    return Dedup(ttl_s=ttl_s, max_keys=max_keys, clock=clock), clock


def test_second_sighting_within_ttl_is_a_duplicate() -> None:
    d, clock = make()
    assert d.is_duplicate(row()) is False
    clock.now = 59.0
    assert d.is_duplicate(row()) is True


def test_the_same_fix_after_the_ttl_is_new_again() -> None:
    d, clock = make()
    d.is_duplicate(row())
    clock.now = 60.0
    assert d.is_duplicate(row()) is False


def test_key_ignores_fields_outside_mmsi_time_and_position() -> None:
    assert key_of(row(sog=8.0)) == key_of(row(sog=0.1))
    assert key_of(row(lat=52.000001)) == key_of(row())  # below 4-decimal precision
    assert key_of(row(lat=52.001)) != key_of(row())
    assert key_of(row(mmsi=244660001)) != key_of(row())
    assert key_of(row(ts=T0 + timedelta(seconds=1))) != key_of(row())


def test_neighbouring_ships_do_not_collide() -> None:
    d, _ = make()
    assert d.is_duplicate(row(mmsi=1)) is False
    assert d.is_duplicate(row(mmsi=2)) is False
    assert d.is_duplicate(row(mmsi=1)) is True


def test_expired_keys_are_evicted_not_just_ignored() -> None:
    d, clock = make(ttl_s=10.0)
    for i in range(5):
        clock.now = float(i)
        d.is_duplicate(row(mmsi=244660000 + i))
    assert len(d) == 5
    clock.now = 100.0
    d.is_duplicate(row(mmsi=999999999))
    assert len(d) == 1  # the five old ones are gone, only the fresh key remains


def test_lru_bound_holds() -> None:
    d, _ = make(max_keys=10)
    for i in range(50):
        d.is_duplicate(row(mmsi=200000000 + i))
    assert len(d) == 10
    assert d.is_duplicate(row(mmsi=200000049)) is True  # newest kept
    assert d.is_duplicate(row(mmsi=200000000)) is False  # oldest dropped
