"""The detector service: ais.raw → the state machine → `events` + a Redis snapshot.

Its own consumer group on the same topic the refinery reads (docs/02 §live path,
item 4: detectors consume it independently), so a slow detector never holds the
map up and a restarted one replays only its own offsets.

Everything on a timer happens in one cycle, every snapshot interval: sweep for
ships that have gone quiet, write whatever events closed, then snapshot. One
loop because the three belong to the same tick — a snapshot taken before the
sweep would record a state the next second contradicts.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer

from ..config import Settings
from ..incidents import IncidentSink, record_incident
from ..log import kv, setup
from .coverage import CoverageModel, load_coverage
from .geo import PortResolver, load_ports
from .machine import Detector
from .sinks import EventWriter, SnapshotStore

logger = logging.getLogger("detectors")


class Ports:
    """The port polygons, whenever Postgres gets round to answering.

    Starting blind beats not starting (as with rebuild): without polygons every
    stop is an anchorage, which is wrong but readable. The cycle tick retries
    until the load succeeds, then swaps the detector's resolver.

    ponytail: loaded once and never reloaded — the polygons are static for the
    life of the process, a new port list arrives with a restart.
    """

    def __init__(
        self,
        detector: Detector,
        postgres_url: str,
        load: Callable[[str], Awaitable[PortResolver]] = load_ports,
    ) -> None:
        self._detector = detector
        self._url = postgres_url
        self._load = load
        self._warned = False
        self.loaded = False

    async def attempt(self) -> bool:
        """Try once. True the tick the ports land, False every other time."""
        if self.loaded:
            return False
        try:
            # Bounded: a stalled Postgres must not hold up the cycle tick —
            # event flushing and the gap sweep run right behind this call.
            resolver = await asyncio.wait_for(self._load(self._url), timeout=10.0)
        except Exception as exc:
            if not self._warned:  # one warning, not one per tick
                self._warned = True
                logger.warning(kv(
                    "detector_ports_unavailable",
                    reason=type(exc).__name__, detail=str(exc)[:200],
                    note="every stop counts as anchorage until Postgres answers",
                ))
            return False
        self._detector.resolve = resolver.resolve
        self.loaded = True
        logger.info(kv("detector_ports_loaded"))
        return True


# The reception picture is a trailing week; a quarter of an hour of staleness in
# it is nothing, and the query is a full scan of density_h3's window.
COVERAGE_RELOAD_S = 900.0


class Coverage:
    """The reception model, reloaded every quarter of an hour.

    Same shape as Ports and a different lifetime: polygons are drawn by a human
    and change on a restart, coverage is measured and drifts with the traffic.
    Starting without it is fine — every gap says coverage-unknown, which is what
    v0 said all along.
    """

    def __init__(
        self,
        detector: Detector,
        lake: EventWriter,
        load: Callable[[EventWriter], Awaitable[CoverageModel]] = (
            lambda lake: load_coverage(lake.fetch)
        ),
        every_s: float = COVERAGE_RELOAD_S,
    ) -> None:
        self._detector = detector
        self._lake = lake
        self._load = load
        self._every = every_s
        self._next = 0.0
        self._warned = False

    async def attempt(self, now: float | None = None) -> bool:
        """Try, if it is time. True the tick a fresh model lands."""
        clock = asyncio.get_running_loop().time() if now is None else now
        if clock < self._next:
            return False
        self._next = clock + self._every
        try:
            # Bounded like the ports load: the sweep and the flush run behind it.
            model = await asyncio.wait_for(self._load(self._lake), timeout=30.0)
        except Exception as exc:
            if not self._warned:  # one warning, not one per reload
                self._warned = True
                logger.warning(kv(
                    "detector_coverage_unavailable",
                    reason=type(exc).__name__, detail=str(exc)[:200],
                    note="every gap stays coverage-unknown until the lake answers",
                ))
            return False
        self._detector.coverage = model
        self._warned = False
        logger.info(kv("detector_coverage_loaded", cells=sum(
            len(v) for v in model.cells.values())))
        return True


async def cycle(detector: Detector, lake: EventWriter, snaps: SnapshotStore) -> None:
    """One tick: find the silence, write what closed, remember where we are."""
    detector.sweep()
    rows = detector.take_events()
    try:
        await lake.insert_events(rows)
    except Exception:
        detector.requeue(rows)  # a blinked connection must not eat an event
        raise
    await snaps.save(detector.snapshot())
    logger.info(
        kv(
            "detector_cycle",
            tracked=len(detector.ships),
            silent=detector.silent_count,
            events=len(rows),
            kinds=",".join(sorted({r.kind for r in rows})),
        )
    )


async def cycle_forever(
    detector: Detector,
    lake: EventWriter,
    snaps: SnapshotStore,
    interval_s: float,
    incidents: IncidentSink | None = None,
    ports: Ports | None = None,
    coverage: Coverage | None = None,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            if ports is not None:
                await ports.attempt()
            if coverage is not None:
                await coverage.attempt()
            await cycle(detector, lake, snaps)
        except Exception as exc:  # a bad tick must not take the service down
            reason, detail = type(exc).__name__, str(exc)[:200]
            logger.warning(kv("detector_cycle_failed", reason=reason, detail=detail))
            await record_incident(incidents, "detector_cycle_failed", reason=reason, detail=detail)


async def consume(consumer: AIOKafkaConsumer, detector: Detector) -> None:
    async for record in consumer:
        if record.value is None:
            continue
        recv_ts = datetime.fromtimestamp(record.timestamp / 1000, tz=UTC)
        detector.handle(record.value, recv_ts)


async def rebuild(detector: Detector, lake: EventWriter, snaps: SnapshotStore) -> None:
    """Come back up where we left off: the snapshot first, then the lake.

    The snapshot carries the open events and the dwell clocks. vessel_latest
    only fills in ships it missed, and only with their last fix — enough for the
    gap detector, so a cold start after a wiped Redis is lossy, not broken.
    """
    restored: dict[str, str] = {}
    try:
        restored = await snaps.load()
        detector.restore(restored)
        detector.seed_missing(await lake.last_fixes())
    except Exception as exc:  # starting blind beats not starting
        logger.warning(kv("detector_rebuild_failed", reason=type(exc).__name__,
                          detail=str(exc)[:200]))
    logger.info(kv("detector_rebuild", from_snapshot=len(restored),
                   tracked=len(detector.ships), silent=detector.silent_count))


async def run(settings: Settings) -> None:
    detector = Detector(settings)
    lake = EventWriter(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )
    snaps = SnapshotStore(settings.redis_url, settings.region_slug)
    consumer = AIOKafkaConsumer(
        settings.raw_topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.detectors_group_id,
        auto_offset_reset="earliest",
    )

    await lake.start()
    await snaps.start()
    await consumer.start()
    await rebuild(detector, lake, snaps)
    ports = Ports(detector, settings.postgres_url)
    await ports.attempt()
    coverage = Coverage(detector, lake)
    await coverage.attempt()
    logger.info(kv("detector_start", topic=settings.raw_topic,
                   group=settings.detectors_group_id, region=settings.region_slug,
                   stop_dwell_s=settings.stop_dwell_s, go_dwell_s=settings.go_dwell_s,
                   anchor_min_s=settings.anchor_min_s))

    ticker = asyncio.create_task(
        cycle_forever(detector, lake, snaps, settings.snapshot_interval_s, snaps.client, ports,
                      coverage)
    )
    try:
        await consume(consumer, detector)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        with contextlib.suppress(Exception):
            await cycle(detector, lake, snaps)  # last tick, best effort
        await consumer.stop()
        await snaps.stop()
        await lake.stop()
        logger.info(kv("detector_stop"))


def main() -> None:
    setup()
    try:
        asyncio.run(run(Settings()))
    except KeyboardInterrupt:
        logger.info(kv("detector_stop", reason="interrupt"))


if __name__ == "__main__":
    main()
