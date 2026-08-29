"""The geojson -> rows split, without a database.

The loader lives in ops/geo (it runs against Postgres, not inside the app), so it
is loaded by path rather than imported as a package.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

_path = Path(__file__).parents[2] / "ops" / "geo" / "load_ports.py"
_spec = importlib.util.spec_from_file_location("load_ports", _path)
assert _spec and _spec.loader
load_ports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load_ports)

SQUARE = {"type": "Polygon", "coordinates": [[[4, 51], [5, 51], [5, 52], [4, 52], [4, 51]]]}


def feature(locode: str, kind: str, name: str = "X") -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"locode": locode, "name": name, "kind": kind},
        "geometry": SQUARE,
    }


def test_ports_and_anchorages_are_split_by_kind() -> None:
    ports, anchorages = load_ports.rows(
        {"features": [feature("NLRTM", "port", "Rotterdam"), feature("NLRTM", "anchorage")]}
    )
    assert ports == [("NLRTM", "Rotterdam", "port", json.dumps(SQUARE))]
    assert anchorages == [("NLRTM", json.dumps(SQUARE))]


def test_an_anchorage_without_its_port_is_an_error() -> None:
    """Nothing to UPDATE means the anchorage would load silently into nowhere."""
    try:
        load_ports.rows({"features": [feature("NLRTM", "port"), feature("NLVLI", "anchorage")]})
    except ValueError as exc:
        assert "NLVLI" in str(exc)
    else:
        raise AssertionError("expected a ValueError for the orphaned anchorage")


def test_the_real_file_is_twelve_ports_and_three_anchorages() -> None:
    ports, anchorages = load_ports.rows(json.loads(load_ports.GEOJSON.read_text()))
    assert len(ports) == 12
    assert len(anchorages) == 3
