"""The state machine: dwell times, gaps, draught, and a restart. No network.

The first test is the one that matters most. nav_status flaps in half of all
ship-hours, so the naive machine — the one that files an arrival the moment a
ship reads slow — would emit tens of thousands of false events a day. Holding
the speed for T_STOP is what stops it, and this is the test that fails if
somebody ever "simplifies" the dwell away.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from ais_pipeline import limits
from ais_pipeline.config import Settings
from ais_pipeline.detectors.coverage import (
    CLASS_COVERAGE,
    CLASS_UNKNOWN,
    CLASS_UNUSUAL,
    build,
)
from ais_pipeline.detectors.geo import ZONE_ANCHORAGE, ZONE_BERTH, PortHit
from ais_pipeline.detectors.machine import (
    EVENT_COLUMNS,
    KIND_ANCHORAGE,
    KIND_DEPARTURE,
    KIND_GAP,
    KIND_LOAD_DELTA,
    KIND_PORT_CALL,
    Detector,
    EventRow,
    ShipState,
)
from ais_pipeline.refinery.models import Parsed, PositionRow, StaticRow
from ais_pipeline.refinery.parser import MSG_TYPE_POSITION, MSG_TYPE_STATIC

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
MMSI = 244660000
QUIET = 244660002

# Enum8 of `events.kind`, from db/migrations/20260826200000_create_lake_tables.sql.
DDL_KINDS = {"port_call", "anchorage", "gap", "load_delta", "departure"}


# Three points instead of polygons: the ray casting is geo.py's own test, and
# what the machine has to get right is which answer it believes.
BERTH_LON, ANCHORAGE_LON = 4.1, 4.2
LOCODE, PORT_NAME = "NLRTM", "Rotterdam"
_MAP = {
    BERTH_LON: PortHit(LOCODE, ZONE_BERTH, PORT_NAME),
    ANCHORAGE_LON: PortHit(LOCODE, ZONE_ANCHORAGE, PORT_NAME),
}


def charted(lat: float, lon: float) -> PortHit | None:
    return _MAP.get(lon)


def det() -> Detector:
    return Detector(Settings(_env_file=None))


def det_charted() -> Detector:
    return Detector(Settings(_env_file=None), charted)


def pos(minutes: int, sog: float, mmsi: int = MMSI, msg_type: int = MSG_TYPE_POSITION,
        lon: float = 4.0) -> Parsed:
    return Parsed(
        position=PositionRow(
            ts=T0 + timedelta(minutes=minutes), mmsi=mmsi, lat=52.0, lon=lon, sog=sog,
            cog=90.0, heading=90, nav_status=0, msg_type=msg_type, src="aisstream",
        )
    )


def static(minutes: int, draught: float, mmsi: int = MMSI) -> Parsed:
    ts = T0 + timedelta(minutes=minutes)
    parsed = pos(minutes, 0.0, mmsi, msg_type=MSG_TYPE_STATIC)  # type 5: the parser's sog is 0.0
    return Parsed(
        position=parsed.position,
        static=StaticRow(mmsi=mmsi, imo=0, name="", callsign="", ship_type=80, dim_a=0,
                         dim_b=0, dim_c=0, dim_d=0, draught=draught, destination="",
                         eta="", ts=ts),
    )


def drive(d: Detector, start: int, end: int, step: int, sog: float, mmsi: int = MMSI,
          lon: float = 4.0) -> None:
    """One fix every `step` minutes, at the same speed throughout."""
    for minute in range(start, end + 1, step):
        d.handle_parsed(pos(minute, sog, mmsi, lon=lon))


def test_a_slow_patch_that_does_not_hold_is_not_a_stop() -> None:
    d = det()
    drive(d, 0, 10, 2, 8.0)     # under way
    drive(d, 12, 26, 2, 0.1)    # fifteen minutes of barely moving — short of T_STOP
    drive(d, 28, 60, 2, 8.0)    # and away again
    assert d.take_events() == []
    assert d.ships[MMSI].state == "underway"


def test_a_real_stop_is_one_anchorage_with_its_start_and_its_duration() -> None:
    d = det()
    drive(d, 0, 10, 5, 8.0)       # arriving
    drive(d, 15, 195, 5, 0.1)     # three hours at rest
    drive(d, 200, 215, 5, 9.0)    # away; T_GO is up at 210

    events = d.take_events()
    assert [e.kind for e in events] == [KIND_ANCHORAGE]
    event = events[0]
    assert event.t_start == T0 + timedelta(minutes=15)   # when she came to rest
    assert event.t_end == T0 + timedelta(minutes=200)    # when she left, not when we knew
    assert event.meta == {"duration_s": 185 * 60}
    assert event.port == ""  # no polygons given, so nowhere in particular
    assert d.ships[MMSI].state == "underway"


def test_a_stop_alongside_a_berth_is_a_port_call_and_a_departure() -> None:
    d = det_charted()
    drive(d, 0, 10, 5, 8.0, lon=BERTH_LON)
    drive(d, 15, 195, 5, 0.1, lon=BERTH_LON)
    assert d.ships[MMSI].state == "moored"  # geography, not nav_status

    drive(d, 200, 215, 5, 9.0, lon=BERTH_LON)
    events = {e.kind: e for e in d.take_events()}
    assert set(events) == {KIND_PORT_CALL, KIND_DEPARTURE}

    call = events[KIND_PORT_CALL]
    assert (call.t_start, call.t_end) == (T0 + timedelta(minutes=15), T0 + timedelta(minutes=200))
    assert call.meta == {"duration_s": 185 * 60}
    assert call.port == LOCODE

    left = events[KIND_DEPARTURE]
    assert left.t_start == left.t_end == call.t_end  # a moment, not a stretch
    assert (left.port, left.meta) == (LOCODE, {})
    assert d.ships[MMSI].state == "underway"
    assert (d.ships[MMSI].port, d.ships[MMSI].zone) == ("", "")


def test_a_moored_ship_restored_from_a_snapshot_still_closes_as_a_port_call() -> None:
    # A restart mid-call: the verdict travels in the snapshot, not the resolver.
    before = det_charted()
    drive(before, 0, 10, 5, 8.0, lon=BERTH_LON)
    drive(before, 15, 195, 5, 0.1, lon=BERTH_LON)
    before.take_events()

    after = det()  # blind on purpose: the restored zone must decide, not a lookup
    after.restore(before.snapshot())
    assert after.ships[MMSI].state == "moored"
    drive(after, 200, 215, 5, 9.0, lon=BERTH_LON)
    events = {e.kind: e for e in after.take_events()}
    assert set(events) == {KIND_PORT_CALL, KIND_DEPARTURE}
    assert events[KIND_PORT_CALL].port == LOCODE


def test_a_wait_inside_an_anchorage_polygon_is_still_an_anchorage_but_named() -> None:
    d = det_charted()
    drive(d, 0, 10, 5, 8.0, lon=ANCHORAGE_LON)
    drive(d, 15, 195, 5, 0.1, lon=ANCHORAGE_LON)
    assert d.ships[MMSI].state == "anchored"

    drive(d, 200, 215, 5, 9.0, lon=ANCHORAGE_LON)
    events = d.take_events()
    assert [e.kind for e in events] == [KIND_ANCHORAGE]
    assert events[0].port == LOCODE


def test_a_stop_in_open_water_is_unchanged_by_the_polygons() -> None:
    d = det_charted()
    drive(d, 0, 10, 5, 8.0)      # lon 4.0: the resolver knows nothing there
    drive(d, 15, 195, 5, 0.1)
    assert d.ships[MMSI].state == "anchored"

    drive(d, 200, 215, 5, 9.0)
    events = d.take_events()
    assert [e.kind for e in events] == [KIND_ANCHORAGE]
    assert events[0].port == ""


def test_where_she_stopped_survives_a_snapshot_and_an_older_one_still_loads() -> None:
    ship = ShipState(mmsi=MMSI, last_fix=T0, motion="moored", still_since=T0,
                     anchorage_id=str(uuid4()), port=LOCODE, zone=ZONE_BERTH,
                     port_name=PORT_NAME)
    back = ShipState.from_json(ship.to_json())
    assert (back.port, back.zone, back.motion) == (LOCODE, ZONE_BERTH, "moored")
    assert back.port_name == PORT_NAME  # the sentence needs the name, not the locode

    # written before the ports landed: no port, no zone, and it must still load
    old = json.loads(ship.to_json())
    del old["port"], old["zone"]
    stale = ShipState.from_json(json.dumps(old))
    assert (stale.port, stale.zone) == ("", "")


def test_a_gap_opens_after_the_silence_limit_and_closes_when_she_comes_back() -> None:
    d = det()
    d.handle_parsed(pos(0, 8.0))
    drive(d, 0, 60 * 25, 60, 8.0, mmsi=QUIET)  # the stream moves on without her
    d.sweep()

    opened = d.ships[MMSI].gap_id
    assert d.ships[MMSI].state == "silent"
    assert opened is not None
    assert d.take_events() == []  # nothing is written while it is still open

    d.handle_parsed(pos(60 * 25, 8.0))  # she is back
    events = d.take_events()
    assert [e.kind for e in events] == [KIND_GAP]
    event = events[0]
    assert (event.t_start, event.t_end) == (T0, T0 + timedelta(hours=25))
    assert event.meta == {"duration_s": 25 * 3600, "classification": "coverage-unknown"}
    assert str(event.event_id) == opened  # the id minted when she fell quiet
    assert d.ships[MMSI].state == "underway"


def test_only_a_draught_change_worth_the_name_is_reported() -> None:
    d = det()
    d.handle_parsed(static(0, 8.0))    # first sighting: nothing to compare against
    d.handle_parsed(static(10, 8.2))   # 0.2 m is inside the reporting noise
    d.handle_parsed(static(20, 11.5))

    events = d.take_events()
    assert [e.kind for e in events] == [KIND_LOAD_DELTA]
    event = events[0]
    assert event.meta == {"from": 8.2, "to": 11.5, "delta": 3.3}
    assert event.t_start == event.t_end == T0 + timedelta(minutes=20)


def test_a_static_report_is_not_a_ship_standing_still() -> None:
    """A type 5 carries no speed and the parser gives its position sog 0.0.

    Believing that would stop every ship in the fleet for a moment every six
    minutes, and no ship under way would ever hold T_GO long enough to depart.
    """
    d = det()
    for minute in range(0, 122, 2):
        d.handle_parsed(pos(minute, 9.0))
        if minute % 6 == 0:
            d.handle_parsed(static(minute + 1, 7.0))

    assert d.take_events() == []
    assert d.ships[MMSI].still_since is None
    assert d.ships[MMSI].state == "underway"


def test_a_restart_keeps_the_open_anchorage_and_the_open_gap() -> None:
    d = det()
    d.handle_parsed(pos(0, 8.0, mmsi=QUIET))     # last heard from at T0
    drive(d, 0, 60 * 25, 30, 0.1)                # at anchor a day, still reporting
    d.sweep()

    assert d.ships[MMSI].state == "anchored"
    assert d.ships[QUIET].state == "silent"
    assert d.take_events() == []  # both events are open, so neither is written yet
    anchorage_id, gap_id = d.ships[MMSI].anchorage_id, d.ships[QUIET].gap_id

    # the process dies here; the new one has only what Redis held
    revived = det()
    revived.restore(d.snapshot())
    assert revived.ships[MMSI].anchorage_id == anchorage_id
    assert revived.ships[QUIET].gap_id == gap_id

    drive(revived, 60 * 25 + 30, 60 * 26, 10, 9.0)          # she weighs anchor
    revived.handle_parsed(pos(60 * 25 + 30, 8.0, mmsi=QUIET))  # and she comes back

    events = {e.kind: e for e in revived.take_events()}
    assert set(events) == {KIND_ANCHORAGE, KIND_GAP}
    assert str(events[KIND_ANCHORAGE].event_id) == anchorage_id
    assert events[KIND_ANCHORAGE].t_start == T0  # the hours before the crash still count
    assert events[KIND_ANCHORAGE].t_end == T0 + timedelta(minutes=60 * 25 + 30)
    assert str(events[KIND_GAP].event_id) == gap_id
    assert events[KIND_GAP].t_start == T0


def test_a_cold_start_falls_back_to_the_lake_and_still_finds_the_gap() -> None:
    """Redis wiped: vessel_latest knows when we last heard from her, and that is
    all a gap needs. Her dwell run starts empty — lossy, not broken."""
    d = det()
    d.seed_missing({QUIET: T0})
    d.handle_parsed(pos(60 * 25, 8.0, mmsi=QUIET))

    events = d.take_events()
    assert [e.kind for e in events] == [KIND_GAP]
    assert events[0].t_start == T0


def test_the_lake_floor_gives_way_to_the_bus_replaying_older_history() -> None:
    """First start: the group has no offsets, so the topic replays from the
    beginning — a day older than the vessel_latest row we seeded from. The
    replay is where we actually start knowing her, and dropping it as stale
    would cost a whole day of anchorages."""
    d = det()
    d.seed_missing({MMSI: T0 + timedelta(hours=24)})  # the lake says: heard from, recently
    drive(d, 0, 60, 5, 0.1)                           # the bus says: here is yesterday
    drive(d, 65, 80, 5, 9.0)

    events = d.take_events()
    assert [e.kind for e in events] == [KIND_ANCHORAGE]
    assert events[0].t_start == T0
    assert d.ships[MMSI].seeded is False


def test_event_rows_match_the_events_table() -> None:
    """Column order and types per db/migrations/20260826200000_create_lake_tables.sql."""
    assert EVENT_COLUMNS == ("event_id", "mmsi", "kind", "t_start", "t_end", "port", "meta")
    assert {KIND_ANCHORAGE, KIND_GAP, KIND_LOAD_DELTA, KIND_PORT_CALL, KIND_DEPARTURE} <= DDL_KINDS

    values = EventRow(event_id=uuid4(), mmsi=MMSI, kind=KIND_GAP, t_start=T0, t_end=None,
                      port="", meta={"duration_s": 60}).as_tuple()
    assert len(values) == len(EVENT_COLUMNS)
    assert [type(v) for v in values] == [UUID, int, str, datetime, type(None), str, str]
    assert json.loads(str(values[6])) == {"duration_s": 60}


def test_the_silence_threshold_is_the_products_own_number() -> None:
    """F27: one limits file. A copy here would drift from the one the map draws."""
    root = json.loads((Path(__file__).resolve().parents[2] / "limits.json").read_text())
    assert limits.SILENT_AFTER_S == root["map_vessel_age_s"]["silent_after"]


# --- the gap classifier's half: what the machine does with a coverage model ---

# One busy cell at the fixes the helpers above emit (52.0 N, 4.0 E), one thin
# one further out. The verdict thresholds are test_coverage.py's business.
COVERAGE = build([(52.0, 4.0, 240.0, 0.95, 6.4), (52.0, 8.0, 900.0, 0.02, 1.0)])
NOISY = 244660003


def det_covered() -> Detector:
    return Detector(Settings(_env_file=None), coverage=COVERAGE)


def test_a_gap_in_a_busy_cell_with_the_neighbours_still_talking_reads_unusual() -> None:
    d = det_covered()
    d.handle_parsed(pos(0, 8.0))                              # she is heard, then not
    for mmsi in (QUIET, NOISY, QUIET + 100):                  # three neighbours, same cell
        drive(d, 0, 60 * 25, 60, 8.0, mmsi=mmsi)
    d.sweep()
    d.handle_parsed(pos(60 * 25, 8.0))                        # she is back

    meta = d.take_events()[0].meta
    assert meta["classification"] == CLASS_UNUSUAL
    assert meta["confidence"] > 0.9
    assert meta["neighbors_online"] == 3          # the expander's numbers, not the page's
    assert meta["cell_interval_s"] == 240


def test_a_gap_far_out_where_nobody_is_heard_is_the_coverage() -> None:
    d = det_covered()
    d.handle_parsed(pos(0, 8.0, lon=8.0))
    drive(d, 0, 60 * 25, 60, 8.0, mmsi=QUIET, lon=8.0)
    d.sweep()
    d.handle_parsed(pos(60 * 25, 8.0, lon=8.0))
    assert d.take_events()[0].meta["classification"] == CLASS_COVERAGE


def test_without_a_model_a_gap_still_says_only_that_we_do_not_know() -> None:
    d = det()
    d.handle_parsed(pos(0, 8.0))
    drive(d, 0, 60 * 25, 60, 8.0, mmsi=QUIET)
    d.sweep()
    d.handle_parsed(pos(60 * 25, 8.0))
    meta = d.take_events()[0].meta
    assert meta == {"duration_s": 25 * 3600, "classification": CLASS_UNKNOWN}


def test_the_verdict_survives_a_restart_with_the_open_gap() -> None:
    """The inputs are only true at gap open, so the answer is snapshotted, not redone."""
    d = det_covered()
    d.handle_parsed(pos(0, 8.0))
    for mmsi in (QUIET, NOISY, QUIET + 100):
        drive(d, 0, 60 * 25, 60, 8.0, mmsi=mmsi)
    d.sweep()
    verdict = d.ships[MMSI].gap_verdict
    assert verdict is not None and verdict["classification"] == CLASS_UNUSUAL

    revived = Detector(Settings(_env_file=None))  # a restart with no model loaded yet
    revived.restore(d.snapshot())
    assert revived.ships[MMSI].gap_verdict == verdict
    assert (revived.ships[MMSI].last_lat, revived.ships[MMSI].last_lon) == (52.0, 4.0)
    revived.handle_parsed(pos(60 * 25, 8.0))
    assert revived.take_events()[0].meta["classification"] == CLASS_UNUSUAL
