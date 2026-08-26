"""DMA CSV row → the refinery's rows. Pure: strings in, dataclasses out, no I/O.

The dump's header (one line, first field carries a leading "# "):

    # Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,
    SOG,COG,Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,
    Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D

Everything is text, including the things AIS transmits as numbers: navigational
status and ship type arrive as English phrases and are mapped back to their AIS
codes here. Missing values show up as "", "Unknown" or "Undefined".

Coordinates keep the AIS "not available" sentinels (91.0 / 181.0) — they are
left alone on purpose so the refinery's bbox check rejects and counts them,
rather than the seed quietly inventing its own filter.
"""

from datetime import UTC, datetime

from ..refinery.models import HEADING_NA, Parsed, PositionRow, StaticRow

SRC = "dma"
# Every dump row is a position fix; the static columns ride along on it, so
# msg_type is always 1 even when the row also yields a StaticRow.
MSG_TYPE_POSITION = 1

TIMESTAMP_FMT = "%d/%m/%Y %H:%M:%S"  # "01/09/2026 00:00:03", UTC

# Only real ships; base stations, AtoN and search-and-rescue aircraft are noise here.
VESSEL_MOBILE_TYPES = frozenset({"Class A", "Class B"})

NAV_UNKNOWN = 15  # AIS "undefined" — what an unrecognised phrase maps to
NAV_STATUS_CODES = {
    "under way using engine": 0,
    "at anchor": 1,
    "not under command": 2,
    "restricted maneuverability": 3,
    "restricted manoeuvrability": 3,
    "constrained by her draught": 4,
    "moored": 5,
    "aground": 6,
    "engaged in fishing": 7,
    "under way sailing": 8,
    "reserved for future amendment [hsc]": 9,
    "reserved for future amendment [wig]": 10,
    "power-driven vessel towing astern": 11,
    "power-driven vessel pushing ahead or towing alongside": 12,
    "ais-sart": 14,
    "unknown value": NAV_UNKNOWN,
}

SHIP_TYPE_UNDEFINED = 0
SHIP_TYPE_CODES = {
    "wing in ground": 20,
    "fishing": 30,
    "towing": 31,
    "towing long/wide": 32,
    "dredging": 33,
    "diving": 34,
    "military": 35,
    "sailing": 36,
    "pleasure": 37,
    "hsc": 40,
    "pilot": 50,
    "sar": 51,
    "tug": 52,
    "port tender": 53,
    "anti-pollution": 54,
    "law enforcement": 55,
    "medical": 58,
    "passenger": 60,
    "cargo": 70,
    "tanker": 80,
    "other": 90,
    "undefined": SHIP_TYPE_UNDEFINED,
    "not party to conflict": 59,
    "spare": SHIP_TYPE_UNDEFINED,
    "reserved": SHIP_TYPE_UNDEFINED,
}

# CSV field positions — the header above, in order.
COL_TIMESTAMP = 0
COL_MOBILE_TYPE = 1
COL_MMSI = 2
COL_LAT = 3
COL_LON = 4
COL_NAV_STATUS = 5
COL_SOG = 7
COL_COG = 8
COL_HEADING = 9
COL_IMO = 10
COL_CALLSIGN = 11
COL_NAME = 12
COL_SHIP_TYPE = 13
COL_DRAUGHT = 18
COL_DESTINATION = 19
COL_ETA = 20
COL_A = 22
COL_B = 23
COL_C = 24
COL_D = 25
MIN_COLUMNS = COL_D + 1

# Values the dump uses for "there is nothing here".
_BLANKS = frozenset({"", "unknown", "undefined", "n/a", "na"})


def _text(row: list[str], index: int) -> str:
    value = row[index].strip()
    return "" if value.lower() in _BLANKS else value


def _float(row: list[str], index: int, default: float = 0.0) -> float:
    try:
        return float(_text(row, index))
    except ValueError:
        return default


def _int(row: list[str], index: int, default: int = 0) -> int:
    return int(_float(row, index, float(default)))


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), TIMESTAMP_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def nav_status_code(text: str) -> int:
    return NAV_STATUS_CODES.get(text.strip().lower(), NAV_UNKNOWN)


def ship_type_code(text: str) -> int:
    return SHIP_TYPE_CODES.get(text.strip().lower(), SHIP_TYPE_UNDEFINED)


def format_eta(value: str) -> str:
    """DMA's full ETA timestamp → the "MM-DD HH:MM" the live parser stores."""
    eta = parse_timestamp(value)
    return "" if eta is None else f"{eta.month:02d}-{eta.day:02d} {eta.hour:02d}:{eta.minute:02d}"


def static_of(row: list[str], mmsi: int, ts: datetime) -> StaticRow | None:
    """The row's static half — None when the dump carries no identity for this ship."""
    name, imo = _text(row, COL_NAME), _int(row, COL_IMO)
    if not name and not imo:
        return None
    return StaticRow(
        mmsi=mmsi,
        imo=imo,
        name=name,
        callsign=_text(row, COL_CALLSIGN),
        ship_type=ship_type_code(_text(row, COL_SHIP_TYPE)),
        dim_a=_int(row, COL_A),
        dim_b=_int(row, COL_B),
        dim_c=_int(row, COL_C),
        dim_d=_int(row, COL_D),
        draught=_float(row, COL_DRAUGHT),
        destination=_text(row, COL_DESTINATION),
        eta=format_eta(row[COL_ETA]),
        ts=ts,
    )


def parse_row(row: list[str]) -> Parsed | None:
    """One CSV row → Parsed, or None when it is not a vessel fix we can use."""
    if len(row) < MIN_COLUMNS:
        return None
    if row[COL_MOBILE_TYPE].strip() not in VESSEL_MOBILE_TYPES:
        return None
    ts = parse_timestamp(row[COL_TIMESTAMP])
    if ts is None:
        return None  # the header line lands here too
    mmsi = _int(row, COL_MMSI)
    if mmsi == 0:
        return None

    static = static_of(row, mmsi, ts)
    heading = _text(row, COL_HEADING)
    position = PositionRow(
        ts=ts,
        mmsi=mmsi,
        lat=_float(row, COL_LAT),
        lon=_float(row, COL_LON),
        sog=_float(row, COL_SOG),
        cog=_float(row, COL_COG),
        heading=_int(row, COL_HEADING, HEADING_NA) if heading else HEADING_NA,
        nav_status=nav_status_code(row[COL_NAV_STATUS]),
        msg_type=MSG_TYPE_POSITION,
        src=SRC,
    )
    return Parsed(position=position, static=static)
