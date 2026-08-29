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
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer

from ..config import Settings
from ..incidents import IncidentSink, record_incident
from ..log import kv, setup
from .machine import Detector
from .sinks import EventWriter, SnapshotStore

logger = logging.getLogger("detectors")


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
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
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
    logger.info(kv("detector_start", topic=settings.raw_topic,
                   group=settings.detectors_group_id, region=settings.region_slug,
                   stop_dwell_s=settings.stop_dwell_s, go_dwell_s=settings.go_dwell_s,
                   anchor_min_s=settings.anchor_min_s))

    ticker = asyncio.create_task(
        cycle_forever(detector, lake, snaps, settings.snapshot_interval_s, snaps.client)
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
