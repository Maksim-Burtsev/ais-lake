"""The refinery service: ais.raw → parse → validate → dedup → Redis + ClickHouse.

The pipeline itself (Refinery) is synchronous and sink-agnostic; the Kafka
consumer and the flush loop are the only async parts, and the sinks are
injected, so all of the logic is testable without a network.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from aiokafka import AIOKafkaConsumer

from ..config import LAUNCH_BBOX, Settings
from ..incidents import IncidentSink, record_incident
from ..log import kv, setup
from .clickhouse import ClickHouseWriter
from .dedup import Dedup
from .models import LatestRow, Parsed, PositionRow, StaticRow
from .parser import NotAVesselMessage, parse
from .redis_sink import RedisSink
from .state import LatestStore
from .symbology import UNKNOWN_SYM, sym
from .validate import Validator

logger = logging.getLogger("refinery")


class LakeSink(Protocol):
    async def insert_positions(self, rows: list[PositionRow]) -> None: ...
    async def insert_static(self, rows: list[StaticRow]) -> None: ...
    async def insert_latest(self, rows: list[LatestRow]) -> None: ...


class LiveSink(Protocol):
    async def publish(self, rows: list[LatestRow]) -> None: ...


class StatusSink(Protocol):
    async def set_status(self, fields: Mapping[str, object]) -> None: ...


@dataclass
class Counters:
    """What the refinery did in the last window — the raw material for /status."""

    in_: int = 0
    out: int = 0
    deduped: int = 0
    rejected_mmsi: int = 0
    rejected_bbox: int = 0
    rejected_teleport: int = 0
    skipped_nonvessel: int = 0

    def as_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {k.rstrip("_"): v for k, v in asdict(self).items()}
        seen = self.out + self.deduped
        fields["dedup_ratio"] = f"{self.deduped / seen:.3f}" if seen else "0.000"
        return fields

    def reset(self) -> None:
        for name in vars(self):
            setattr(self, name, 0)


class Refinery:
    """Raw bytes in, buffered rows out. No I/O — flush() hands the buffers to the sinks."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._validator = Validator(
            bbox=LAUNCH_BBOX,
            mmsi_min=settings.mmsi_min,
            mmsi_max=settings.mmsi_max,
            teleport_max_kn=settings.teleport_max_kn,
        )
        self._dedup = Dedup(
            ttl_s=settings.dedup_ttl_s,
            max_keys=settings.dedup_max_keys,
            clock=clock,
        )
        self.latest = LatestStore()
        self.counters = Counters()
        self.positions: list[PositionRow] = []
        self.statics: list[StaticRow] = []
        self._dirty: dict[int, LatestRow] = {}
        # ponytail: unbounded, one short string per ship ever seen (~10k in the
        # launch region). Bound it alongside LatestStore if a region ever needs it.
        self._sym: dict[int, str] = {}

    @property
    def pending_rows(self) -> int:
        return len(self.positions)

    def handle(self, raw: bytes | str, recv_ts: datetime) -> None:
        """One raw message: parse it, then run it through the pipeline."""
        self.counters.in_ += 1
        try:
            parsed = parse(raw, recv_ts)
        except NotAVesselMessage:
            self.counters.skipped_nonvessel += 1
            return
        self.handle_parsed(parsed)

    def handle_parsed(self, parsed: Parsed) -> None:
        """Validate, dedup and buffer already-parsed rows — the seed's entry point too."""
        row = parsed.position
        if row is None:  # pragma: no cover — parse() always yields a position today
            self.counters.skipped_nonvessel += 1
            return

        # Statics are identity, not position: a moored ship's synthetic position is
        # a dup or out of the box half the time, and dropping the class with it left
        # the sprite on unknown2 forever. Learn the token before any early return.
        if parsed.static is not None:
            self.statics.append(parsed.static)
            self._sym[row.mmsi] = sym(
                parsed.static.ship_type, parsed.static.dim_a, parsed.static.dim_b
            )

        reason = self._validator.check(row, self.latest)
        if reason is not None:
            setattr(self.counters, reason.value, getattr(self.counters, reason.value) + 1)
            return

        if self._dedup.is_duplicate(row):
            self.counters.deduped += 1
            return

        self.counters.out += 1
        self.positions.append(row)
        latest = self.latest.apply(row, self._sym.get(row.mmsi, UNKNOWN_SYM))
        self._dirty[latest.mmsi] = latest

    def take_batches(self) -> tuple[list[PositionRow], list[StaticRow], list[LatestRow]]:
        """Detach the buffers so new messages can land while the old ones are written."""
        positions, statics, dirty = self.positions, self.statics, self._dirty
        self.positions, self.statics, self._dirty = [], [], {}
        return positions, statics, list(dirty.values())

    async def flush(self, lake: LakeSink, live: LiveSink) -> None:
        positions, statics, latest = self.take_batches()
        if not positions and not statics and not latest:
            return
        await lake.insert_positions(positions)
        await lake.insert_static(statics)
        await lake.insert_latest(latest)
        await live.publish(latest)


async def flush_forever(
    refinery: Refinery,
    lake: LakeSink,
    live: LiveSink,
    interval_s: float,
    incidents: IncidentSink | None = None,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await refinery.flush(lake, live)
        except Exception as exc:  # a bad batch must not take the service down
            reason, detail = type(exc).__name__, str(exc)[:200]
            logger.warning(kv("flush_failed", reason=reason, detail=detail))
            await record_incident(incidents, "flush_failed", reason=reason, detail=detail)


async def report_forever(
    refinery: Refinery,
    interval_s: float,
    status: StatusSink | None = None,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        fields = refinery.counters.as_fields()
        tracked = len(refinery.latest)
        logger.info(kv("refinery_counters", window_s=interval_s, tracked=tracked, **fields))
        if status is not None:
            try:
                await status.set_status(
                    {**fields, "tracked": tracked, "window_s": interval_s, "ts": int(time.time())}
                )
            except Exception as exc:  # the counters are a nicety, never a reason to die
                logger.debug("status not published: %s: %s", type(exc).__name__, exc)
        refinery.counters.reset()


async def consume(
    consumer: AIOKafkaConsumer,
    refinery: Refinery,
    lake: LakeSink,
    live: LiveSink,
    flush_max_rows: int,
) -> None:
    async for record in consumer:
        if record.value is None:
            continue
        recv_ts = datetime.fromtimestamp(record.timestamp / 1000, tz=UTC)
        refinery.handle(record.value, recv_ts)
        if refinery.pending_rows >= flush_max_rows:
            await refinery.flush(lake, live)


async def run(settings: Settings) -> None:
    refinery = Refinery(settings)
    lake = ClickHouseWriter(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )
    live = RedisSink(settings.redis_url, settings.region_slug)
    consumer = AIOKafkaConsumer(
        settings.raw_topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.refinery_group_id,
        auto_offset_reset="earliest",
    )

    await lake.start()
    await live.start()
    await consumer.start()
    logger.info(kv("refinery_start", topic=settings.raw_topic, group=settings.refinery_group_id,
                   region=settings.region_slug, clickhouse=settings.clickhouse_host))

    flusher = asyncio.create_task(
        flush_forever(refinery, lake, live, settings.flush_interval_s, live.client)
    )
    reporter = asyncio.create_task(
        report_forever(refinery, settings.metrics_interval_s, live)
    )
    try:
        await consume(consumer, refinery, lake, live, settings.flush_max_rows)
    finally:
        for task in (flusher, reporter):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await refinery.flush(lake, live)  # last batch, best effort
        await consumer.stop()
        await live.stop()
        await lake.stop()
        logger.info(kv("refinery_stop"))


def main() -> None:
    setup()
    try:
        asyncio.run(run(Settings()))
    except KeyboardInterrupt:
        logger.info(kv("refinery_stop", reason="interrupt"))


if __name__ == "__main__":
    main()
