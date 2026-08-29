"""Port polygons in memory: hits, holes, and who wins when two zones overlap.

No database — the resolver is fed the same GeoJSON strings PostGIS would hand
it, so these tests fail if the ray cast or the berth-first order ever drifts.
"""

import json

from ais_pipeline.config import Settings
from ais_pipeline.detectors.geo import PortResolver
from ais_pipeline.detectors.machine import Detector
from ais_pipeline.detectors.service import Ports


def square(x0: float, y0: float, x1: float, y1: float) -> list[list[tuple[float, float]]]:
    return [[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]


def multi(*parts: list[list[tuple[float, float]]]) -> str:
    return json.dumps({"type": "MultiPolygon", "coordinates": list(parts)})


# A 0..10 square with a 4..6 hole punched out of it.
HOLED = multi(square(0, 0, 10, 10) + square(4, 4, 6, 6))


def test_a_point_inside_the_berth_square_is_that_port_alongside() -> None:
    r = PortResolver([("NLRTM", multi(square(0, 0, 10, 10)), None)])
    hit = r.resolve(lat=5.0, lon=5.0)
    assert hit is not None
    assert hit == ("NLRTM", "berth")


def test_a_point_in_the_hole_of_a_ring_is_outside_the_port() -> None:
    r = PortResolver([("NLRTM", HOLED, None)])
    assert r.resolve(lat=5.0, lon=5.0) is None
    assert r.resolve(lat=1.0, lon=1.0) is not None  # still inside the doughnut


def test_the_second_part_of_a_multipolygon_counts_as_much_as_the_first() -> None:
    r = PortResolver([("DEHAM", multi(square(0, 0, 1, 1), square(20, 20, 21, 21)), None)])
    hit = r.resolve(lat=20.5, lon=20.5)
    assert hit is not None and hit.locode == "DEHAM"


def test_latitude_and_longitude_are_not_interchangeable() -> None:
    # Every other polygon here is symmetric across lat=lon, so a swapped axis
    # order inside the resolver would pass the whole file. This one is not.
    r = PortResolver([("BEANR", multi(square(0, 0, 10, 2)), None)])  # lon 0..10, lat 0..2
    assert r.resolve(lat=1.0, lon=8.0) == ("BEANR", "berth")
    assert r.resolve(lat=8.0, lon=1.0) is None


def test_open_water_belongs_to_nobody() -> None:
    r = PortResolver([("NLRTM", multi(square(0, 0, 10, 10)), multi(square(10, 10, 20, 20)))])
    assert r.resolve(lat=50.0, lon=50.0) is None


def test_a_berth_wins_over_an_anchorage_drawn_across_it() -> None:
    # Rotterdam's anchorage polygon overlaps the port; alongside beats waiting.
    r = PortResolver([("NLRTM", multi(square(0, 0, 10, 10)), multi(square(0, 0, 30, 30)))])
    assert r.resolve(lat=5.0, lon=5.0) == ("NLRTM", "berth")


def test_a_ship_only_in_the_anchorage_is_waiting_off_that_port() -> None:
    r = PortResolver([("NLRTM", multi(square(0, 0, 10, 10)), multi(square(0, 0, 30, 30)))])
    assert r.resolve(lat=25.0, lon=25.0) == ("NLRTM", "anchorage")


class FlakyLoader:
    """Postgres that refuses twice, then hands over the polygons."""

    def __init__(self, resolver: PortResolver) -> None:
        self.calls = 0
        self._resolver = resolver

    async def __call__(self, url: str) -> PortResolver:
        self.calls += 1
        if self.calls <= 2:
            raise OSError("connection refused")
        return self._resolver


async def test_the_ports_land_on_the_tick_after_postgres_answers() -> None:
    resolver = PortResolver([("NLRTM", multi(square(0, 0, 10, 10)), None)])
    detector = Detector(Settings())
    blind = detector.resolve
    loader = FlakyLoader(resolver)
    ports = Ports(detector, "postgresql://nowhere", loader)

    assert await ports.attempt() is False
    assert await ports.attempt() is False
    assert detector.resolve is blind  # still blind: every stop is an anchorage

    assert await ports.attempt() is True
    assert detector.resolve.__self__ is resolver  # type: ignore[attr-defined]
    assert detector.resolve(5.0, 5.0) == ("NLRTM", "berth")

    # Loaded once and never again — the tick keeps calling, Postgres does not.
    assert await ports.attempt() is False
    assert loader.calls == 3
