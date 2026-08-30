"""The per-ship state machine: fixes in, `events` rows out. Pure, no I/O.

Speed leads and nav_status follows nobody. Measured on our own lake: 145,288
ship-hours carry two or more nav_status values inside a single hour against
156,492 steady, and the commonest flapping pair is "under way" against "moored"
at 77,153 of them. A machine that believed that field would file tens of
thousands of arrivals a day that never happened. So a ship is judged by how
fast she is going, and only once she has held it — still for T_STOP before she
has stopped, moving for T_GO before she has left. The tie nav_status was meant
to break is anchored against moored, and geography breaks it instead: the port
polygons (geo.py) say which one, on the fix that opens the stop.

    under way ──(slow, held T_STOP)──> stopped ──(held T_ANCHOR_MIN)──> anchored
        ^                                                    └────────> moored │
        └──────────────────(making way, held T_GO)──────────────────────────────┘
    any of them ──(no message for SILENT_AFTER_S)──> silent ──(any message)──> back

Time is the stream's own, never the wall's: the watermark is the newest fix
seen. Replaying a day off the bus must not make the whole fleet look silent,
and our own ingestor falling over must not open thirteen thousand gaps.

Events are written once, when they close, with the id minted when they opened.
The table is a plain MergeTree with no key to replace on, so a row written at
open and again at close would be two rows for one event and every reader would
have to know it. A ship that never comes back therefore leaves no gap row,
which is the honest answer: at that point she has not gone quiet so much as
left our receivers, and M3-T3 is what learns the difference.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ..config import Settings
from ..limits import SILENT_AFTER_S
from ..refinery.models import Parsed, PositionRow, StaticRow
from ..refinery.parser import MSG_TYPE_POSITION, NotAVesselMessage, parse
from ..refinery.state import UNDERWAY_MIN_SOG_KN as SOG_STILL
from .geo import ZONE_BERTH, PortHit

# events, in table column order (db/migrations/…_create_lake_tables.sql)
EVENT_COLUMNS = ("event_id", "mmsi", "kind", "t_start", "t_end", "port", "meta")

KIND_ANCHORAGE = "anchorage"
KIND_PORT_CALL = "port_call"
KIND_DEPARTURE = "departure"
KIND_GAP = "gap"
KIND_LOAD_DELTA = "load_delta"

MOTION_UNDERWAY = "underway"
MOTION_STOPPED = "stopped"
MOTION_ANCHORED = "anchored"
MOTION_MOORED = "moored"
STATE_SILENT = "silent"


def _nowhere(lat: float, lon: float) -> PortHit | None:
    """No polygons given: every stop is open water, which is the old behaviour."""
    return None

SOG_NA = 102.3  # AIS spells "speed not available" as 102.3 kn
DRAUGHT_NA = 0.0  # …and "draught not declared" as zero

# v0 says only that we do not know why she went quiet. M3-T3 replaces the word.
GAP_CLASSIFICATION = "coverage-unknown"


@dataclass(frozen=True, slots=True)
class EventRow:
    """One row of `events`. Column names and order are the schema's law."""

    event_id: UUID
    mmsi: int
    kind: str
    t_start: datetime
    t_end: datetime | None
    port: str  # UN/LOCODE of the port she stopped in; '' in open water
    meta: dict[str, Any]

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.event_id,
            self.mmsi,
            self.kind,
            self.t_start,
            self.t_end,
            self.port,
            json.dumps(self.meta, separators=(",", ":")),
        )


def _epoch(ts: datetime | None) -> int | None:
    return None if ts is None else int(ts.timestamp())


def _when(value: Any) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(int(value), tz=UTC)


@dataclass(slots=True)
class ShipState:
    """Everything the machine remembers about one ship — and all it snapshots."""

    mmsi: int
    last_fix: datetime
    motion: str = MOTION_UNDERWAY
    # The first fix of the current unbroken slow run — and, once she is
    # anchored, the moment the anchorage began.
    still_since: datetime | None = None
    moving_since: datetime | None = None
    draught: float | None = None
    anchorage_id: str | None = None
    gap_id: str | None = None
    # Where the current stop is, decided once when it opened: the port's
    # UN/LOCODE and which of its polygons held her. Both '' in open water.
    port: str = ""
    zone: str = ""
    # The port's display name, carried so the sentence can say "Moored in
    # Rotterdam" — a locode on a public page would break the voice rule.
    port_name: str = ""
    # last_fix came out of the lake rather than off the bus — a floor for the
    # gap sweep, and nothing the replay has to respect.
    seeded: bool = False

    @property
    def state(self) -> str:
        return STATE_SILENT if self.gap_id is not None else self.motion

    def to_json(self) -> str:
        return json.dumps(
            {
                "mmsi": self.mmsi,
                "last_fix": _epoch(self.last_fix),
                "motion": self.motion,
                "still_since": _epoch(self.still_since),
                "moving_since": _epoch(self.moving_since),
                "draught": self.draught,
                "anchorage_id": self.anchorage_id,
                "gap_id": self.gap_id,
                "port": self.port,
                "zone": self.zone,
                "port_name": self.port_name,
                "seeded": self.seeded,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> ShipState:
        d: Any = json.loads(raw)
        last_fix = _when(d["last_fix"])
        if last_fix is None:  # pragma: no cover — never written without one
            raise ValueError("snapshot entry has no last fix")
        return cls(
            mmsi=int(d["mmsi"]),
            last_fix=last_fix,
            motion=str(d["motion"]),
            still_since=_when(d["still_since"]),
            moving_since=_when(d["moving_since"]),
            draught=d["draught"],
            anchorage_id=d["anchorage_id"],
            gap_id=d["gap_id"],
            # .get: snapshots written before the ports landed have neither.
            port=str(d.get("port", "")),
            zone=str(d.get("zone", "")),
            port_name=str(d.get("port_name", "")),
            seeded=bool(d.get("seeded", False)),
        )


class Detector:
    """Raw messages in, buffered event rows out. No I/O — the service flushes."""

    def __init__(
        self,
        settings: Settings,
        resolve: Callable[[float, float], PortHit | None] = _nowhere,
    ) -> None:
        # Swapped in by the service once Postgres answers (service.py: Ports).
        self.resolve = resolve
        self._stop_dwell = timedelta(seconds=settings.stop_dwell_s)
        self._go_dwell = timedelta(seconds=settings.go_dwell_s)
        self._anchor_min = timedelta(seconds=settings.anchor_min_s)
        self._silent_after = timedelta(seconds=SILENT_AFTER_S)
        self._draught_delta = settings.draught_min_delta_m
        self._mmsi_min = settings.mmsi_min
        self._mmsi_max = settings.mmsi_max
        # ponytail: ships are never evicted, so the map's 48 h age cut does not
        # apply here and a region's state grows by its transients. ~13k ships in
        # the launch box; prune on last_fix if a region ever makes it hurt.
        self.ships: dict[int, ShipState] = {}
        self.events: list[EventRow] = []
        self.watermark: datetime | None = None

    # --- intake ---------------------------------------------------------

    def handle(self, raw: bytes | str, recv_ts: datetime) -> None:
        try:
            parsed = parse(raw, recv_ts)
        except NotAVesselMessage:
            return
        self.handle_parsed(parsed)

    def handle_parsed(self, parsed: Parsed) -> None:
        row = parsed.position
        if row is None:  # pragma: no cover — parse() always yields a position today
            return
        # The refinery's MMSI range check, without its bbox one: a fix outside
        # the box is still a fix, and silence is what we are measuring.
        if not self._mmsi_min <= row.mmsi <= self._mmsi_max:
            return
        ship = self._advance(row)
        if parsed.static is not None:
            self._on_draught(ship, parsed.static)

    def _advance(self, row: PositionRow) -> ShipState:
        """Fold one fix into the ship's state and hand her back."""
        self.watermark = row.ts if self.watermark is None else max(self.watermark, row.ts)
        ship = self.ships.get(row.mmsi)
        if ship is None:
            ship = self.ships[row.mmsi] = ShipState(mmsi=row.mmsi, last_fix=row.ts)
        elif ship.seeded and row.ts <= ship.last_fix:
            # Her floor came from vessel_latest and the bus is replaying history
            # older than it — a first start, reading the topic from the
            # beginning. That history is where our knowledge of her really
            # begins, so take it and forget the floor. Any silence inside the
            # replay window turns up again as the replay walks through it.
            ship.last_fix = row.ts
            ship.gap_id = None
        elif row.ts <= ship.last_fix:
            return ship  # a late or repeated fix never rewinds a run
        else:
            self._resume(ship, row.ts)
        ship.seeded = False

        # A type 5 carries no speed, and the parser gives its synthetic position
        # sog 0.0. Feeding that to the dwell logic would stop every ship in the
        # fleet for a moment every six minutes, and no ship under way would ever
        # hold T_GO long enough to be called departed. It counts as a message —
        # she is transmitting — and as nothing else.
        if row.msg_type != MSG_TYPE_POSITION or row.sog >= SOG_NA:
            return ship

        if row.sog < SOG_STILL:
            ship.moving_since = None
            if ship.still_since is None:
                ship.still_since = row.ts
            self._settle(ship, row.ts, row.lat, row.lon)
        else:
            if ship.moving_since is None:
                ship.moving_since = row.ts
            if ship.motion != MOTION_UNDERWAY and row.ts - ship.moving_since >= self._go_dwell:
                self._depart(ship)
        return ship

    # --- transitions ----------------------------------------------------

    def _settle(self, ship: ShipState, ts: datetime, lat: float, lon: float) -> None:
        """She has not moved. Promote her as far as the clock allows."""
        assert ship.still_since is not None
        held = ts - ship.still_since
        if ship.motion == MOTION_UNDERWAY and held >= self._stop_dwell:
            ship.motion = MOTION_STOPPED
        if ship.motion == MOTION_STOPPED and held >= self._anchor_min:
            # The seam left for M3-T1 is closed: the polygons decide, not
            # nav_status. Where she was standing when the stop became real is
            # what the whole stop is called — inside a berth she is moored and
            # this will be a port call, inside an anchorage or out in open
            # water she is anchored. One lookup, on the fix that opens the
            # event, because a swinging ship must not change her own verdict.
            hit = self.resolve(lat, lon)
            if hit is not None:
                ship.port, ship.zone, ship.port_name = hit.locode, hit.zone, hit.name
                if hit.zone == ZONE_BERTH:
                    ship.motion = MOTION_MOORED
            # The uuid identifies the stop, whatever we end up calling it.
            if ship.motion == MOTION_STOPPED:
                ship.motion = MOTION_ANCHORED
            ship.anchorage_id = str(uuid4())

    def _depart(self, ship: ShipState) -> None:
        """She is making way again. Close the stop at the moment she left."""
        assert ship.moving_since is not None
        if ship.anchorage_id is not None and ship.still_since is not None:
            waited = ship.moving_since - ship.still_since
            berthed = ship.zone == ZONE_BERTH
            self._emit(
                KIND_PORT_CALL if berthed else KIND_ANCHORAGE,
                ship.mmsi,
                t_start=ship.still_since,
                t_end=ship.moving_since,
                port=ship.port,
                meta={"duration_s": int(waited.total_seconds())},
                event_id=ship.anchorage_id,
            )
            if berthed:
                # A call is a stretch of time; leaving is a moment, and the two
                # are separate rows because readers ask different questions of
                # them — how long she was alongside, and when the berth freed.
                self._emit(
                    KIND_DEPARTURE,
                    ship.mmsi,
                    t_start=ship.moving_since,
                    t_end=ship.moving_since,
                    port=ship.port,
                    meta={},
                )
        ship.motion = MOTION_UNDERWAY
        ship.anchorage_id = None
        ship.still_since = None
        ship.port = ship.zone = ship.port_name = ""

    def _resume(self, ship: ShipState, ts: datetime) -> None:
        """A message after silence. Long enough silence is a gap, and it just closed."""
        silence = ts - ship.last_fix
        if silence >= self._silent_after:
            # gap_id is None when the sweep never saw her go quiet — a restart
            # with the snapshot gone. The arithmetic is the truth either way;
            # the id only carries the number minted when she first fell silent.
            self._emit(
                KIND_GAP,
                ship.mmsi,
                t_start=ship.last_fix,
                t_end=ts,
                meta={
                    "duration_s": int(silence.total_seconds()),
                    "classification": GAP_CLASSIFICATION,
                },
                event_id=ship.gap_id,
            )
        ship.gap_id = None
        ship.last_fix = ts

    def _on_draught(self, ship: ShipState, static: StaticRow) -> None:
        """A new type 5. She sits deeper, or lighter, than the last one said.

        vessels_static keeps no history — latest row wins, by design — so the
        last draught we saw is held here and the events table becomes the
        record. What she loaded, and how much, is in neither the message nor
        this event: only the two numbers and the difference between them.
        """
        # ponytail: the reading is taken at face value, and some of them are junk.
        # Measured on the first live run: NIETS BESTENDIG, 25 m long, reports 13.2 m
        # of draught and flips back to 1.2 m and round again; KRAICHGAU 2, 86 m,
        # does the same between 17.5 and 1.2. Six events of 1,228 are physically
        # impossible and about 28 are doubtful. Magnitude cannot catch them: real
        # draught/LOA runs to 0.229 at p99 across 12,301 of our ships (heavy-lift
        # hulls are genuinely that deep), so a ratio bound tight enough to reject
        # 13.2 m on a 25 m boat throws out a tenth of the honest fleet, and one
        # loose enough to keep them misses the 86 m barge. What marks both is that
        # the value OSCILLATES — a retyped field, not moved cargo — and telling
        # that apart needs history and a rule. Nothing renders these until M4, and
        # M3-T3's spot-check against 20 labelled voyages is where it belongs.
        draught = static.draught
        if draught <= DRAUGHT_NA:
            return
        was, ship.draught = ship.draught, draught
        if was is None or abs(draught - was) < self._draught_delta:
            return
        self._emit(
            KIND_LOAD_DELTA,
            ship.mmsi,
            t_start=static.ts,
            t_end=static.ts,  # a reading, not a stretch of time
            meta={"from": round(was, 1), "to": round(draught, 1),
                  "delta": round(draught - was, 1)},
        )

    def sweep(self) -> None:
        """Open a gap for every ship we have not heard from in SILENT_AFTER_S.

        A gap is the absence of messages, so nothing on the consume path can
        find it — this runs on the snapshot timer instead. It only opens: the
        row is written when she comes back.
        """
        if self.watermark is None:
            return
        cutoff = self.watermark - self._silent_after
        for ship in self.ships.values():
            if ship.gap_id is None and ship.last_fix <= cutoff:
                ship.gap_id = str(uuid4())

    def _emit(
        self,
        kind: str,
        mmsi: int,
        t_start: datetime,
        t_end: datetime | None,
        meta: dict[str, Any],
        port: str = "",
        event_id: str | None = None,
    ) -> None:
        self.events.append(
            EventRow(
                event_id=UUID(event_id) if event_id else uuid4(),
                mmsi=mmsi,
                kind=kind,
                t_start=t_start,
                t_end=t_end,
                port=port,
                meta=meta,
            )
        )

    # --- buffers and crash safety ---------------------------------------

    def take_events(self) -> list[EventRow]:
        """Detach the buffer so new messages can land while the old rows are written."""
        rows, self.events = self.events, []
        return rows

    def requeue(self, rows: list[EventRow]) -> None:
        """Put a failed batch back at the front. Events are the product; losing
        one to a blinked connection is worse than holding it for 30 s."""
        self.events[:0] = rows

    @property
    def silent_count(self) -> int:
        return sum(1 for s in self.ships.values() if s.gap_id is not None)

    def snapshot(self) -> dict[str, str]:
        return {str(mmsi): ship.to_json() for mmsi, ship in self.ships.items()}

    def restore(self, fields: Mapping[str, str]) -> None:
        """Rebuild from the last snapshot. Up to one snapshot interval of
        t_start precision can be lost; the state itself re-derives from her next
        fix, and Kafka offsets do the rest."""
        for raw in fields.values():
            ship = ShipState.from_json(raw)
            self.ships[ship.mmsi] = ship
            self._mark(ship.last_fix)

    def seed_missing(self, fixes: Mapping[int, datetime]) -> None:
        """Ships the snapshot did not have, from vessel_latest.

        When we last heard from her is all the gap detector needs, so a cold
        start after a wiped Redis is lossy rather than broken. Her dwell run
        starts empty on purpose: we can see that she is stopped now, never for
        how long, and inventing the hours would be worse than losing them.
        """
        for mmsi, ts in fixes.items():
            if mmsi not in self.ships:
                self.ships[mmsi] = ShipState(mmsi=mmsi, last_fix=ts, seeded=True)
                self._mark(ts)

    def _mark(self, ts: datetime) -> None:
        """Carry the watermark over a restart, so the sweep works before the
        first message of the new run arrives."""
        self.watermark = ts if self.watermark is None else max(self.watermark, ts)


__all__ = [
    "EVENT_COLUMNS",
    "KIND_ANCHORAGE",
    "KIND_DEPARTURE",
    "KIND_GAP",
    "KIND_LOAD_DELTA",
    "KIND_PORT_CALL",
    "Detector",
    "EventRow",
    "ShipState",
]