"""Port polygons in memory: hits, holes, and who wins when two zones overlap.

No database — the resolver is fed the same GeoJSON strings PostGIS would hand
it, so these tests fail if the ray cast or the berth-first order ever drifts.
"""

import json

from ais_pipeline.detectors.geo import PortResolver


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
