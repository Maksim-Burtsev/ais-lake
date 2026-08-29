"""Where a ship is: port polygons in memory, point-in-polygon by hand.

The machine (machine.py) knows a ship has stopped but not where, and "where"
is what decides whether a stop is a port call or a wait at anchor. PostGIS
holds the polygons, but asking it per fix would put a network round trip in
the hot path of a stream — so the twelve launch ports are loaded once at
start-up and answered from memory afterwards. They change when a human edits
them, which is not during a run.

Berths win over anchorages: the anchorage polygons are drawn wide enough to
cover where ships actually wait, and off Rotterdam they overlap the port
itself. A ship inside a berth is alongside, whatever else contains her.

Rings are even-odd ray cast, which gives holes for free — a point inside an
inner ring crosses the outer ring twice more and lands outside again, no
separate hole bookkeeping.
"""

import json
from typing import Any, NamedTuple

import asyncpg

ZONE_BERTH = "berth"
ZONE_ANCHORAGE = "anchorage"


class PortHit(NamedTuple):
    locode: str
    zone: str  # ZONE_BERTH | ZONE_ANCHORAGE


# A polygon plus its bounding box: (min_lon, min_lat, max_lon, max_lat, rings).
type _Poly = tuple[float, float, float, float, list[list[tuple[float, float]]]]


def _polygons(geojson: str | None) -> list[_Poly]:
    """GeoJSON (Multi)Polygon -> rings with a bbox each. Coordinates are lon, lat."""
    if not geojson:
        return []
    geom: dict[str, Any] = json.loads(geojson)
    coords = geom["coordinates"]
    if geom["type"] == "Polygon":
        coords = [coords]
    out: list[_Poly] = []
    for parts in coords:
        rings = [[(float(x), float(y)) for x, y in ring] for ring in parts]
        lons = [x for x, _ in rings[0]]
        lats = [y for _, y in rings[0]]
        out.append((min(lons), min(lats), max(lons), max(lats), rings))
    return out


def _in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Even-odd ray cast east from the point; True on an odd crossing count."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _hits(lon: float, lat: float, polys: list[_Poly]) -> bool:
    for min_lon, min_lat, max_lon, max_lat, rings in polys:
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if sum(_in_ring(lon, lat, ring) for ring in rings) % 2 == 1:
            return True
    return False


class PortResolver:
    """Twelve ports' berth and anchorage polygons, answered without a database."""

    def __init__(self, rows: list[tuple[str, str, str | None]]) -> None:
        self._berths = [(locode, _polygons(geom)) for locode, geom, _ in rows]
        self._anchorages = [(locode, _polygons(anch)) for locode, _, anch in rows]

    def resolve(self, lat: float, lon: float) -> PortHit | None:
        # ponytail: linear scan over ~15 bboxes. An r-tree is never needed at
        # dozens of lookups an hour; add one if the port list reaches hundreds.
        for locode, polys in self._berths:
            if _hits(lon, lat, polys):
                return PortHit(locode, ZONE_BERTH)
        for locode, polys in self._anchorages:
            if _hits(lon, lat, polys):
                return PortHit(locode, ZONE_ANCHORAGE)
        return None


async def load_ports(postgres_url: str) -> PortResolver:
    """Read the port polygons once, at start-up, and hand back a closed-over resolver."""
    conn = await asyncpg.connect(postgres_url)
    try:
        rows = await conn.fetch(
            "SELECT locode, ST_AsGeoJSON(geom) AS geom, "
            "ST_AsGeoJSON(anchorages) AS anchorages FROM ports"
        )
    finally:
        await conn.close()
    return PortResolver([(r["locode"], r["geom"], r["anchorages"]) for r in rows])
