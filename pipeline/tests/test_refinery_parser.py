import json
from datetime import UTC, datetime

import pytest

from ais_pipeline.refinery.models import HEADING_NA
from ais_pipeline.refinery.parser import NotAVesselMessage, format_eta, parse, parse_time_utc

RECV = datetime(2026, 8, 26, 20, 0, 0, tzinfo=UTC)

POSITION_REPORT = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 241714000,
        "MMSI_String": 241714000,
        "ShipName": "MARAN ASPASIA       ",
        "latitude": 41.6049,
        "longitude": 29.63779,
        "time_utc": "2026-08-26 19:19:03.638827763 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "MessageID": 1,
            "NavigationalStatus": 0,
            "RateOfTurn": -3,
            "Sog": 8.7,
            "Longitude": 29.63779,
            "Latitude": 41.60489666666667,
            "Cog": 254.7,
            "TrueHeading": 256,
        }
    },
}

SHIP_STATIC = {
    "MessageType": "ShipStaticData",
    "MetaData": {
        "MMSI": 244660000,
        "ShipName": "EENDRACHT ",
        "latitude": 51.9,
        "longitude": 4.1,
        "time_utc": "2026-08-26 19:20:11.100000000 +0000 UTC",
    },
    "Message": {
        "ShipStaticData": {
            "ImoNumber": 9123456,
            "CallSign": "PBXY ",
            "Name": "EENDRACHT       ",
            "Type": 70,
            "Dimension": {"A": 100, "B": 20, "C": 8, "D": 9},
            "MaximumStaticDraught": 7.4,
            "Destination": "NLRTM  ",
            "Eta": {"Month": 8, "Day": 27, "Hour": 6, "Minute": 30},
        }
    },
}

SUBSCRIPTION_CONFIRMATION = {"MessageType": "SubscriptionConfirmation", "Message": {}}


def test_position_report_maps_to_positions_columns() -> None:
    row = parse(json.dumps(POSITION_REPORT).encode(), RECV).position
    assert row is not None
    assert row.mmsi == 241714000
    assert row.ts == datetime(2026, 8, 26, 19, 19, 3, tzinfo=UTC)
    assert row.lat == 41.60489666666667  # the report's precision beats MetaData's
    assert row.lon == 29.63779
    assert (row.sog, row.cog, row.heading, row.nav_status) == (8.7, 254.7, 256, 0)
    assert (row.msg_type, row.src) == (1, "aisstream")


def test_missing_heading_is_not_available() -> None:
    msg = json.loads(json.dumps(POSITION_REPORT))
    del msg["Message"]["PositionReport"]["TrueHeading"]
    row = parse(json.dumps(msg), RECV).position
    assert row is not None and row.heading == HEADING_NA


def test_ship_static_yields_both_rows() -> None:
    parsed = parse(json.dumps(SHIP_STATIC), RECV)
    assert parsed.position is not None
    assert parsed.position.msg_type == 5
    assert parsed.position.lat == 51.9  # only MetaData carries coordinates for type 5
    assert parsed.position.heading == HEADING_NA
    static = parsed.static
    assert static is not None
    assert static.imo == 9123456
    assert static.name == "EENDRACHT"  # aisstream pads names with spaces
    assert static.callsign == "PBXY"
    assert (static.ship_type, static.draught, static.destination) == (70, 7.4, "NLRTM")
    assert (static.dim_a, static.dim_b, static.dim_c, static.dim_d) == (100, 20, 8, 9)
    assert static.eta == "08-27 06:30"


def test_static_defaults_when_fields_are_absent() -> None:
    msg = {"MessageType": "ShipStaticData", "MetaData": SHIP_STATIC["MetaData"],
           "Message": {"ShipStaticData": {}}}
    static = parse(json.dumps(msg), RECV).static
    assert static is not None
    assert (static.imo, static.ship_type, static.draught) == (0, 0, 0.0)
    assert (static.name, static.callsign, static.destination, static.eta) == ("", "", "", "")


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps(SUBSCRIPTION_CONFIRMATION),
        "not json at all",
        json.dumps({"MessageType": "PositionReport", "MetaData": {"MMSI": 0}}),
        json.dumps({"MessageType": "StaticDataReport", "MetaData": {"MMSI": 244660000},
                    "Message": {"StaticDataReport": {}}}),
        json.dumps([1, 2, 3]),
    ],
)
def test_non_vessel_messages_are_rejected(payload: str) -> None:
    with pytest.raises(NotAVesselMessage):
        parse(payload, RECV)


def test_time_utc_falls_back_to_receive_time() -> None:
    assert parse_time_utc(None, RECV) == RECV
    assert parse_time_utc("yesterday afternoon??", RECV) == RECV
    msg = json.loads(json.dumps(POSITION_REPORT))
    msg["MetaData"]["time_utc"] = "nonsense"
    row = parse(json.dumps(msg), RECV).position
    assert row is not None and row.ts == RECV


def test_eta_formatting() -> None:
    assert format_eta({"Month": 1, "Day": 2, "Hour": 3, "Minute": 4}) == "01-02 03:04"
    assert format_eta({"Month": 0, "Day": 0, "Hour": 0, "Minute": 0}) == ""
    # AIS "not available": hour 24 / minute 60, and month/day 0
    assert format_eta({"Month": 0, "Day": 0, "Hour": 24, "Minute": 60}) == ""
    assert format_eta({"Month": 9, "Day": 0, "Hour": 0, "Minute": 0}) == ""
    assert format_eta(None) == ""
