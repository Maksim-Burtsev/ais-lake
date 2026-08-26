"""Property-based checks on the validator: bbox, MMSI bounds, teleports."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from ais_pipeline.config import LAUNCH_BBOX, Settings
from ais_pipeline.refinery.models import PositionRow
from ais_pipeline.refinery.validate import (
    Fix,
    LastPositionStore,
    Reject,
    Validator,
    haversine_nm,
    implied_speed_kn,
)

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
S = Settings(_env_file=None)


def make_validator() -> Validator:
    return Validator(LAUNCH_BBOX, S.mmsi_min, S.mmsi_max, S.teleport_max_kn)


class FakeStore:
    def __init__(self, fix: Fix | None = None) -> None:
        self._fix = fix

    def last_fix(self, mmsi: int) -> Fix | None:
        return self._fix


EMPTY: LastPositionStore = FakeStore()


def row(mmsi: int = 244660000, lat: float = 52.0, lon: float = 4.0,
        ts: datetime = T0) -> PositionRow:
    return PositionRow(ts=ts, mmsi=mmsi, lat=lat, lon=lon, sog=8.0, cog=90.0,
                       heading=90, nav_status=0, msg_type=1, src="aisstream")


in_box_lat = st.floats(LAUNCH_BBOX.lat_sw, LAUNCH_BBOX.lat_ne)
in_box_lon = st.floats(LAUNCH_BBOX.lon_sw, LAUNCH_BBOX.lon_ne)


@given(lat=in_box_lat, lon=in_box_lon)
def test_coordinates_inside_the_launch_box_are_accepted(lat: float, lon: float) -> None:
    assert make_validator().check(row(lat=lat, lon=lon), EMPTY) is None


@given(
    lat=st.floats(-90, 90, allow_nan=False),
    lon=st.floats(-180, 180, allow_nan=False),
)
def test_coordinates_outside_the_launch_box_are_rejected(lat: float, lon: float) -> None:
    inside = LAUNCH_BBOX.lat_sw <= lat <= LAUNCH_BBOX.lat_ne and (
        LAUNCH_BBOX.lon_sw <= lon <= LAUNCH_BBOX.lon_ne
    )
    result = make_validator().check(row(lat=lat, lon=lon), EMPTY)
    assert (result is None) is inside
    if not inside:
        assert result is Reject.BBOX


@given(mmsi=st.integers(0, 1_000_000_000))
def test_mmsi_bounds(mmsi: int) -> None:
    result = make_validator().check(row(mmsi=mmsi), EMPTY)
    assert (result is Reject.MMSI) is not (S.mmsi_min <= mmsi <= S.mmsi_max)


@given(
    lat=in_box_lat,
    lon=in_box_lon,
    dt_s=st.integers(1, 3600),
    speed_kn=st.floats(0.0, 5000.0, allow_nan=False),
    bearing_east=st.booleans(),
)
@hyp_settings(max_examples=200)
def test_teleport_threshold_matches_implied_speed(
    lat: float, lon: float, dt_s: int, speed_kn: float, bearing_east: bool
) -> None:
    """Move a ship a known distance in a known time; the verdict must follow the speed."""
    hours = dt_s / 3600
    # 1 nm of latitude = 1/60 degree; move north (or east, near the equator-free North Sea)
    delta_deg = speed_kn * hours / 60.0
    lat2 = lat if bearing_east else lat + delta_deg
    lon2 = lon + delta_deg if bearing_east else lon
    later = row(lat=lat2, lon=lon2, ts=T0 + timedelta(seconds=dt_s))
    store = FakeStore(Fix(ts=T0, lat=lat, lon=lon))
    v = make_validator()
    if not v.in_bbox(lat2, lon2):
        return  # the bbox rejects it first; teleport is not what is under test here
    actual = implied_speed_kn(Fix(ts=T0, lat=lat, lon=lon), later)
    assert actual is not None
    expected = Reject.TELEPORT if actual > S.teleport_max_kn else None
    assert v.check(later, store) == expected


def test_same_timestamp_is_never_a_teleport() -> None:
    store = FakeStore(Fix(ts=T0, lat=52.0, lon=4.0))
    assert implied_speed_kn(Fix(ts=T0, lat=52.0, lon=4.0), row(lat=53.0, ts=T0)) is None
    assert make_validator().check(row(lat=53.0, lon=4.0, ts=T0), store) is None


def test_first_sighting_has_no_reference_and_passes() -> None:
    assert make_validator().check(row(), EMPTY) is None


def test_haversine_known_distance() -> None:
    # one degree of latitude is 60 nm by definition
    assert abs(haversine_nm(52.0, 4.0, 53.0, 4.0) - 60.0) < 0.2
    assert haversine_nm(52.0, 4.0, 52.0, 4.0) == 0.0
