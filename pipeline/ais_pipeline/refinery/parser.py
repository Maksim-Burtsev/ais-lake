"""Raw aisstream JSON → flat rows. Pure: bytes in, dataclasses out, no I/O.

Anything without MetaData (SubscriptionConfirmation, errors) is not a vessel
message — the caller counts it as skipped_nonvessel.
"""

import json
from datetime import UTC, datetime
from typing import Any

from .models import HEADING_NA, Parsed, PositionRow, StaticRow

SRC = "aisstream"
MSG_TYPE_POSITION = 1
MSG_TYPE_STATIC = 5

# "2026-08-26 19:19:03.638827763 +0000 UTC" — nanoseconds and a Go zone name;
# positions.ts is DateTime64(0), so seconds are all we keep.
_TIME_UTC_LEN = 19
_TIME_UTC_FMT = "%Y-%m-%d %H:%M:%S"


class NotAVesselMessage(Exception):
    """No MetaData, or nothing parseable in it."""


def _numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if _numeric(value) else default


def _int(value: Any, default: int = 0) -> int:
    return int(value) if _numeric(value) else default


def _str(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def parse_time_utc(raw: Any, fallback: datetime) -> datetime:
    """MetaData.time_utc → aware UTC datetime; fallback on anything unexpected."""
    if not isinstance(raw, str) or len(raw) < _TIME_UTC_LEN:
        return fallback
    try:
        return datetime.strptime(raw[:_TIME_UTC_LEN], _TIME_UTC_FMT).replace(tzinfo=UTC)
    except ValueError:
        return fallback


def format_eta(eta: Any) -> str:
    """ShipStaticData.Eta → "MM-DD HH:MM".

    AIS spells "not available" as month/day 0 and hour 24 / minute 60; anything
    out of range means the ship did not declare an ETA, so we store "".
    """
    if not isinstance(eta, dict):
        return ""
    month, day = _int(eta.get("Month")), _int(eta.get("Day"))
    hour, minute = _int(eta.get("Hour")), _int(eta.get("Minute"))
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def parse(raw: bytes | str, recv_ts: datetime) -> Parsed:
    """One raw message → rows. Raises NotAVesselMessage when there is no vessel in it."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise NotAVesselMessage("undecodable payload") from exc
    if not isinstance(msg, dict):
        raise NotAVesselMessage("payload is not an object")

    meta = msg.get("MetaData")
    if not isinstance(meta, dict):
        raise NotAVesselMessage("no MetaData")  # SubscriptionConfirmation and friends

    mmsi = _int(meta.get("MMSI"))
    if mmsi == 0:
        raise NotAVesselMessage("no MMSI")

    ts = parse_time_utc(meta.get("time_utc"), recv_ts)
    body = msg.get("Message")
    body = body if isinstance(body, dict) else {}
    msg_kind = msg.get("MessageType")

    report = body.get("PositionReport")
    if msg_kind == "PositionReport" and isinstance(report, dict):
        return Parsed(position=_position(mmsi, ts, meta, report, MSG_TYPE_POSITION))

    static = body.get("ShipStaticData")
    if msg_kind == "ShipStaticData" and isinstance(static, dict):
        return Parsed(
            position=_position(mmsi, ts, meta, {}, MSG_TYPE_STATIC),
            static=_static(mmsi, ts, static),
        )

    raise NotAVesselMessage(f"unhandled MessageType {msg_kind!r}")


def _position(
    mmsi: int,
    ts: datetime,
    meta: dict[str, Any],
    report: dict[str, Any],
    msg_type: int,
) -> PositionRow:
    # the report's own coordinates carry full precision; MetaData is the fallback
    lat = report.get("Latitude", meta.get("latitude"))
    lon = report.get("Longitude", meta.get("longitude"))
    heading = report.get("TrueHeading")
    return PositionRow(
        ts=ts,
        mmsi=mmsi,
        lat=_num(lat),
        lon=_num(lon),
        sog=_num(report.get("Sog")),
        cog=_num(report.get("Cog")),
        heading=_int(heading, HEADING_NA) if heading is not None else HEADING_NA,
        nav_status=_int(report.get("NavigationalStatus")),
        msg_type=msg_type,
        src=SRC,
    )


def _static(mmsi: int, ts: datetime, static: dict[str, Any]) -> StaticRow:
    dim = static.get("Dimension")
    dim = dim if isinstance(dim, dict) else {}
    return StaticRow(
        mmsi=mmsi,
        imo=_int(static.get("ImoNumber")),
        name=_str(static.get("Name")),
        callsign=_str(static.get("CallSign")),
        ship_type=_int(static.get("Type")),
        dim_a=_int(dim.get("A")),
        dim_b=_int(dim.get("B")),
        dim_c=_int(dim.get("C")),
        dim_d=_int(dim.get("D")),
        draught=_num(static.get("MaximumStaticDraught")),
        destination=_str(static.get("Destination")),
        eta=format_eta(static.get("Eta")),
        ts=ts,
    )
