import json

from app.consumer import LatestShips

POSITION = {
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 241714000, "ShipName": "MARAN ASPASIA       ",
                 "latitude": 41.6049, "longitude": 29.63779},
    "Message": {"PositionReport": {"Sog": 8.7}},
}
STATIC = {
    "MessageType": "ShipStaticData",
    "MetaData": {"MMSI": 241714000, "ShipName": "MARAN ASPASIA       ",
                 "latitude": 41.61, "longitude": 29.64},
    "Message": {"ShipStaticData": {"Destination": "CONSTANTA"}},
}
CONFIRMATION = {"Message": {"CompressionEnabled": True}, "MessageType": "SubscriptionConfirmation"}


def test_position_report_lands_trimmed() -> None:
    ships = LatestShips()
    ships.apply(json.dumps(POSITION), now=100.0)
    (s,) = ships.top()
    assert (s.mmsi, s.name, s.sog) == (241714000, "MARAN ASPASIA", 8.7)
    assert s.ts == 100.0


def test_static_updates_position_keeps_sog() -> None:
    ships = LatestShips()
    ships.apply(json.dumps(POSITION), now=100.0)
    ships.apply(json.dumps(STATIC), now=101.0)
    (s,) = ships.top()
    assert s.lat == 41.61 and s.sog == 8.7  # sog carried from the last position


def test_non_vessel_messages_ignored() -> None:
    ships = LatestShips()
    ships.apply(json.dumps(CONFIRMATION))
    ships.apply(b"not json at all")
    assert len(ships) == 0


def test_top_is_newest_first_and_capped() -> None:
    ships = LatestShips()
    for i in range(30):
        m = {"MetaData": {"MMSI": 100000000 + i, "latitude": 41.0, "longitude": 29.0}}
        ships.apply(json.dumps(m), now=float(i))
    top = ships.top(20)
    assert len(top) == 20
    assert top[0].mmsi == 100000029  # newest first
