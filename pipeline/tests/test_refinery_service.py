"""State heuristic, the sentence, the Redis payloads, and the counters — no network."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from ais_pipeline.config import Settings
from ais_pipeline.refinery.models import LatestRow, Parsed, PositionRow, StaticRow
from ais_pipeline.refinery.redis_sink import latest_field, live_delta
from ais_pipeline.refinery.service import Counters, Refinery
from ais_pipeline.refinery.state import LatestStore, latest_from, sentence_for, state_of

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


def row(mmsi: int = 244660000, lat: float = 52.0, lon: float = 4.0, sog: float = 8.0,
        nav_status: int = 0, ts: datetime = T0) -> PositionRow:
    return PositionRow(ts=ts, mmsi=mmsi, lat=lat, lon=lon, sog=sog, cog=90.0,
                       heading=90, nav_status=nav_status, msg_type=1, src="aisstream")


class FakeLake:
    def __init__(self) -> None:
        self.positions: list[PositionRow] = []
        self.statics: list[StaticRow] = []
        self.latest: list[LatestRow] = []

    async def insert_positions(self, rows: list[PositionRow]) -> None:
        self.positions += rows

    async def insert_static(self, rows: list[StaticRow]) -> None:
        self.statics += rows

    async def insert_latest(self, rows: list[LatestRow]) -> None:
        self.latest += rows


class FakeLive:
    def __init__(self) -> None:
        self.published: list[LatestRow] = []

    async def publish(self, rows: list[LatestRow]) -> None:
        self.published += rows


@pytest.mark.parametrize(
    "nav_status,state",
    [(0, "underway"), (1, "anchored"), (5, "moored"), (8, "underway"), (15, "underway")],
)
def test_state_heuristic(nav_status: int, state: str) -> None:
    assert state_of(nav_status) == state


@pytest.mark.parametrize(
    "state,sog,sentence",
    [
        ("underway", 8.74, "Under way at 8.7 kn"),
        ("underway", 0.5, "Under way at 0.5 kn"),
        ("underway", 0.4, "Under way"),
        ("underway", 0.0, "Under way"),
        ("anchored", 0.1, "At anchor"),
        ("moored", 0.0, "Moored"),
    ],
)
def test_sentence_v0(state: str, sog: float, sentence: str) -> None:
    assert sentence_for(state, sog) == sentence


def test_latest_row_carries_state_and_sentence() -> None:
    latest = latest_from(row(sog=12.34, nav_status=1))
    assert (latest.state, latest.sentence) == ("anchored", "At anchor")


def test_latest_store_ignores_out_of_order_fixes() -> None:
    store = LatestStore()
    store.apply(row(lat=52.0, ts=T0))
    store.apply(row(lat=51.0, ts=T0 - timedelta(minutes=5)))
    fix = store.last_fix(244660000)
    assert fix is not None and fix.lat == 52.0
    assert store.last_fix(1) is None


def test_redis_payload_shapes() -> None:
    latest = latest_from(row(sog=8.74, nav_status=0))
    assert json.loads(latest_field(latest)) == [int(T0.timestamp()), 52.0, 4.0, 8.7, 90.0,
                                                "underway", "unknown2"]
    assert json.loads(live_delta(latest)) == [244660000, 52.0, 4.0, 90.0, 8.7, "underway",
                                              "unknown2"]


def test_counters_report_dedup_ratio() -> None:
    c = Counters(in_=10, out=6, deduped=2, skipped_nonvessel=2)
    fields = c.as_fields()
    assert fields["in"] == 10 and fields["dedup_ratio"] == "0.250"
    assert Counters().as_fields()["dedup_ratio"] == "0.000"
    c.reset()
    assert c.as_fields()["in"] == 0


def message(mmsi: int, lat: float, lon: float, time_utc: str, sog: float = 8.0) -> str:
    return json.dumps({
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "latitude": lat, "longitude": lon, "time_utc": time_utc},
        "Message": {"PositionReport": {"Sog": sog, "Cog": 90.0, "TrueHeading": 90,
                                       "NavigationalStatus": 0, "Latitude": lat,
                                       "Longitude": lon}},
    })


async def test_refinery_end_to_end_counts_and_flushes() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    good = message(244660000, 52.0, 4.0, "2026-08-26 12:00:00.0 +0000 UTC")
    refinery.handle(good, T0)
    refinery.handle(good, T0)  # exact duplicate
    refinery.handle(message(1234, 52.0, 4.0, "2026-08-26 12:00:01.0 +0000 UTC"), T0)  # bad MMSI
    refinery.handle(message(244660001, 10.0, 100.0, "2026-08-26 12:00:02.0 +0000 UTC"), T0)
    refinery.handle(message(244660000, 60.0, 4.0, "2026-08-26 12:00:30.0 +0000 UTC"), T0)
    refinery.handle('{"MessageType":"SubscriptionConfirmation"}', T0)

    c = refinery.counters
    assert (c.in_, c.out, c.deduped) == (6, 1, 1)
    assert (c.rejected_mmsi, c.rejected_bbox, c.rejected_teleport) == (1, 1, 1)
    assert c.skipped_nonvessel == 1

    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    assert len(lake.positions) == 1
    assert [r.mmsi for r in lake.latest] == [244660000]
    assert [r.mmsi for r in live.published] == [244660000]
    assert refinery.pending_rows == 0

    await refinery.flush(lake, live)  # nothing new — no write at all
    assert len(lake.positions) == 1


async def test_static_message_writes_both_tables_once() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    static = json.dumps({
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 244660000, "latitude": 52.0, "longitude": 4.0,
                     "time_utc": "2026-08-26 12:00:00.0 +0000 UTC"},
        "Message": {"ShipStaticData": {"Name": "EENDRACHT ", "ImoNumber": 9123456}},
    })
    refinery.handle(static, T0)
    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    assert [r.msg_type for r in lake.positions] == [5]
    assert [r.name for r in lake.statics] == ["EENDRACHT"]
    assert len(live.published) == 1


async def test_static_message_teaches_the_sprite_token() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    refinery.handle_parsed(Parsed(position=row(mmsi=244660001)))  # no static yet
    refinery.handle(json.dumps({
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 244660000, "latitude": 52.0, "longitude": 4.0,
                     "time_utc": "2026-08-26 12:00:00.0 +0000 UTC"},
        "Message": {"ShipStaticData": {"Type": 80, "Dimension": {"A": 150, "B": 60}}},
    }), T0)
    refinery.handle_parsed(Parsed(position=row(ts=T0 + timedelta(minutes=1))))

    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    syms = {r.mmsi: r.sym for r in live.published}
    assert syms == {244660000: "tanker4", 244660001: "unknown2"}


async def test_static_teaches_the_token_even_when_its_position_is_a_dup() -> None:
    """A moored ship: position first, then the static at the same second and spot.
    The static's synthetic position dedups away — the class must not go with it."""
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    refinery.handle_parsed(Parsed(position=row(nav_status=5, sog=0.0)))
    refinery.handle(json.dumps({
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 244660000, "latitude": 52.0, "longitude": 4.0,
                     "time_utc": "2026-08-26 12:00:00.0 +0000 UTC"},
        "Message": {"ShipStaticData": {"Type": 80, "Dimension": {"A": 150, "B": 60}}},
    }), T0)
    refinery.handle_parsed(Parsed(position=row(nav_status=5, sog=0.0,
                                               ts=T0 + timedelta(minutes=1))))

    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    assert refinery.counters.deduped == 1  # the static's position really was a dup
    assert [r.sym for r in live.published] == ["tanker4"]
    assert [r.ship_type for r in lake.statics] == [80]  # …and the identity row still lands


class FakeSnapshot:
    """The detector's Redis hash, without Redis. `fail` makes the next load raise."""

    def __init__(self, ships: dict[int, dict[str, object]] | None = None) -> None:
        self.ships = ships or {}
        self.fail = False

    async def load(self) -> dict[str, str]:
        if self.fail:
            raise ConnectionError("redis down")
        return {str(mmsi): json.dumps(state) for mmsi, state in self.ships.items()}


def ship_state(motion: str, still_since: datetime | None = None, port_name: str = "",
               zone: str = "") -> dict[str, object]:
    return {"motion": motion, "port_name": port_name, "zone": zone,
            "still_since": None if still_since is None else int(still_since.timestamp())}


async def flush_one(refinery: Refinery) -> LatestRow:
    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    assert len(lake.latest) == 1 and lake.latest == live.published  # CH and Redis agree
    return lake.latest[0]


@pytest.mark.parametrize(
    "state,state_wire,sentence",
    [
        (ship_state("moored", T0 - timedelta(hours=3), "Rotterdam", "berth"),
         "moored", "Moored in Rotterdam — 3 hours"),
        (ship_state("anchored", T0 - timedelta(hours=14), "Rotterdam", "anchorage"),
         "anchored", "Waiting off Rotterdam — 14 hours"),
        (ship_state("anchored", T0 - timedelta(hours=14)),
         "anchored", "At anchor — 14 hours"),
        (ship_state("stopped", T0 - timedelta(minutes=8)), "underway", "Stopped"),
        (ship_state("underway"), "underway", "Under way at 8.0 kn"),
    ],
)
async def test_detector_snapshot_writes_state_and_sentence(
    state: dict[str, object], state_wire: str, sentence: str
) -> None:
    """nav_status says 5 (moored) on every one of these — the detector overrules it."""
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0,
                        snapshot=FakeSnapshot({244660000: state}))
    refinery.handle_parsed(Parsed(position=row(nav_status=5)))
    latest = await flush_one(refinery)
    assert (latest.state, latest.sentence) == (state_wire, sentence)


async def test_ship_absent_from_snapshot_keeps_the_v0_heuristic() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0,
                        snapshot=FakeSnapshot({999: ship_state("moored")}))
    refinery.handle_parsed(Parsed(position=row(nav_status=1, sog=0.1)))
    latest = await flush_one(refinery)
    assert (latest.state, latest.sentence) == ("anchored", "At anchor")


async def test_no_snapshot_source_keeps_the_v0_heuristic() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    refinery.handle_parsed(Parsed(position=row(nav_status=5, sog=0.0)))
    latest = await flush_one(refinery)
    assert (latest.state, latest.sentence) == ("moored", "Moored")


async def test_stale_snapshot_falls_back_and_logs_once(
    caplog: pytest.LogCaptureFixture
) -> None:
    now = 0.0
    snapshot = FakeSnapshot({244660000: ship_state("moored", T0 - timedelta(hours=3),
                                                   "Rotterdam", "berth")})
    refinery = Refinery(Settings(_env_file=None), clock=lambda: now,
                        snapshot=snapshot)

    refinery.handle_parsed(Parsed(position=row(nav_status=0)))
    assert (await flush_one(refinery)).sentence == "Moored in Rotterdam — 3 hours"

    snapshot.fail = True  # Redis goes away; the last load still carries us
    now = 60.0
    refinery.handle_parsed(Parsed(position=row(nav_status=0, ts=T0 + timedelta(minutes=1))))
    assert (await flush_one(refinery)).state == "moored"

    now = 500.0  # …until it is older than SNAPSHOT_MAX_AGE_S
    with caplog.at_level("INFO", logger="refinery"):
        refinery.handle_parsed(Parsed(position=row(nav_status=0, ts=T0 + timedelta(minutes=2))))
        latest = await flush_one(refinery)
        assert (latest.state, latest.sentence) == ("underway", "Under way at 8.0 kn")

        snapshot.fail = False  # and back again, once
        refinery.handle_parsed(Parsed(position=row(nav_status=0, ts=T0 + timedelta(minutes=3))))
        assert (await flush_one(refinery)).state == "moored"

    events = [r.getMessage().split()[0] for r in caplog.records]
    assert events.count("event=detector_snapshot_lost") == 1
    assert events.count("event=detector_snapshot_back") == 1


async def test_one_latest_row_per_ship_per_flush() -> None:
    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    for step in range(3):  # 0.001 deg every 10 s — about 22 kn, no teleport
        refinery.handle(
            message(244660000, 52.0 + step * 0.001, 4.0,
                    f"2026-08-26 12:00:{step * 10:02d}.0 +0000 UTC"),
            T0,
        )
    lake, live = FakeLake(), FakeLive()
    await refinery.flush(lake, live)
    assert len(lake.positions) == 3
    assert len(lake.latest) == 1 and lake.latest[0].lat == 52.002  # newest fix wins
