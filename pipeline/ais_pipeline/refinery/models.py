"""Flat rows, one per lake table. Column names and order are the schema's law."""

from dataclasses import dataclass
from datetime import datetime

HEADING_NA = 511  # AIS "not available"

# positions, in table column order
POSITION_COLUMNS = (
    "ts",
    "mmsi",
    "lat",
    "lon",
    "sog",
    "cog",
    "heading",
    "nav_status",
    "msg_type",
    "src",
)

# vessels_static, in table column order
STATIC_COLUMNS = (
    "mmsi",
    "imo",
    "name",
    "callsign",
    "ship_type",
    "dim_a",
    "dim_b",
    "dim_c",
    "dim_d",
    "draught",
    "destination",
    "eta",
    "ts",
)

# vessel_latest, in table column order
LATEST_COLUMNS = (
    "mmsi",
    "ts",
    "lat",
    "lon",
    "sog",
    "cog",
    "heading",
    "nav_status",
    "state",
    "sentence",
)


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One row of `positions`."""

    ts: datetime
    mmsi: int
    lat: float
    lon: float
    sog: float
    cog: float
    heading: int
    nav_status: int
    msg_type: int
    src: str

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.ts,
            self.mmsi,
            self.lat,
            self.lon,
            self.sog,
            self.cog,
            self.heading,
            self.nav_status,
            self.msg_type,
            self.src,
        )


@dataclass(frozen=True, slots=True)
class StaticRow:
    """One row of `vessels_static` (AIS type 5 / 24)."""

    mmsi: int
    imo: int
    name: str
    callsign: str
    ship_type: int
    dim_a: int
    dim_b: int
    dim_c: int
    dim_d: int
    draught: float
    destination: str
    eta: str
    ts: datetime

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.mmsi,
            self.imo,
            self.name,
            self.callsign,
            self.ship_type,
            self.dim_a,
            self.dim_b,
            self.dim_c,
            self.dim_d,
            self.draught,
            self.destination,
            self.eta,
            self.ts,
        )


@dataclass(frozen=True, slots=True)
class LatestRow:
    """One row of `vessel_latest` — the map's cold start and the card's header."""

    mmsi: int
    ts: datetime
    lat: float
    lon: float
    sog: float
    cog: float
    heading: int
    nav_status: int
    state: str
    sentence: str
    sym: str = "unknown2"  # sprite token for the map wire — NOT a lake column

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.mmsi,
            self.ts,
            self.lat,
            self.lon,
            self.sog,
            self.cog,
            self.heading,
            self.nav_status,
            self.state,
            self.sentence,
        )


@dataclass(frozen=True, slots=True)
class Parsed:
    """What one raw aisstream message yields."""

    position: PositionRow | None = None
    static: StaticRow | None = None
