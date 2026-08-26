"""The seed runner: download the dumps, stream them, feed the refinery.

Same pipeline as live — Refinery.handle_parsed does validation, dedup and
vessel_latest, and the same ClickHouseWriter + RedisSink take the batches. The
only differences are the source (a zipped CSV instead of Kafka) and the clock:
recv_ts is the row's own timestamp, so history lands with history's times.

Dedup keeps its wall clock on purpose. Historical rows carry distinct
(mmsi, ts, lat, lon) keys, so the TTL never gets in the way and there is
nothing to special-case.
"""

import asyncio
import csv
import io
import logging
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings
from ..log import kv, setup
from ..refinery.clickhouse import ClickHouseWriter
from ..refinery.models import Parsed, StaticRow
from ..refinery.redis_sink import RedisSink
from ..refinery.service import LakeSink, LiveSink, Refinery
from .dma import parse_row
from .download import candidate_days, ensure_dumps

logger = logging.getLogger("seed")

PROGRESS_EVERY_ROWS = 1_000_000
CSV_ENCODING = "utf-8"


class StaticSeen:
    """Which identity we last emitted per ship — the dump repeats it on every row."""

    def __init__(self) -> None:
        self._seen: dict[int, tuple[object, ...]] = {}

    @staticmethod
    def content(row: StaticRow) -> tuple[object, ...]:
        return row.as_tuple()[:-1]  # everything but ts

    def changed(self, row: StaticRow) -> bool:
        content = self.content(row)
        if self._seen.get(row.mmsi) == content:
            return False
        self._seen[row.mmsi] = content
        return True


def iter_rows(path: Path, stride: int) -> Iterator[list[str]]:
    """Every stride-th CSV row inside the zip. Streamed — nothing is extracted to disk."""
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        with archive.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding=CSV_ENCODING, errors="replace", newline="")
            for index, row in enumerate(csv.reader(text)):
                if index % stride == 0:
                    yield row


async def replay_file(
    path: Path,
    refinery: Refinery,
    lake: LakeSink,
    live: LiveSink,
    settings: Settings,
    statics: StaticSeen,
) -> tuple[int, int]:
    """Stream one dump through the refinery. Returns (rows_read, rows_out)."""
    read = out = 0
    last_flush = time.monotonic()
    for row in iter_rows(path, settings.seed_stride):
        read += 1
        parsed = parse_row(row)
        if parsed is not None:
            static = parsed.static
            if static is not None and not statics.changed(static):
                parsed = Parsed(position=parsed.position, static=None)
            before = refinery.counters.out
            refinery.counters.in_ += 1
            refinery.handle_parsed(parsed)
            out += refinery.counters.out - before

        now = time.monotonic()
        due = refinery.pending_rows >= settings.flush_max_rows
        if due or now - last_flush >= settings.flush_interval_s:
            await refinery.flush(lake, live)
            last_flush = now
        if read % PROGRESS_EVERY_ROWS == 0:
            logger.info(kv("seed_progress", file=path.name, rows_read=read, rows_out=out))
            await asyncio.sleep(0)  # a long file must not starve the event loop

    await refinery.flush(lake, live)
    logger.info(kv("seed_file_done", file=path.name, rows_read=read, rows_out=out))
    return read, out


async def replay(
    paths: list[Path],
    refinery: Refinery,
    lake: LakeSink,
    live: LiveSink,
    settings: Settings,
) -> tuple[int, int]:
    statics = StaticSeen()
    read = out = 0
    for path in paths:
        file_read, file_out = await replay_file(path, refinery, lake, live, settings, statics)
        read += file_read
        out += file_out
    return read, out


async def run(settings: Settings) -> None:
    cache_dir = settings.seed_cache_path()
    days = candidate_days(
        datetime.now(tz=UTC).date(), settings.seed_days, settings.seed_lookback_extra_days
    )
    if not days:
        logger.warning(kv("seed_done", reason="seed_days_is_zero", files=0))
        return
    logger.info(kv("seed_start", days=len(days), first=str(days[0]), last=str(days[-1]),
                   stride=settings.seed_stride, cache=str(cache_dir)))

    paths = ensure_dumps(settings.seed_base_url, cache_dir, days, want=settings.seed_days)
    if not paths:
        logger.warning(kv("seed_done", reason="no_dumps", files=0))
        return

    refinery = Refinery(settings)
    lake = ClickHouseWriter(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )
    live = RedisSink(settings.redis_url, settings.region_slug)
    await lake.start()
    await live.start()

    started = time.monotonic()
    try:
        read, out = await replay(paths, refinery, lake, live, settings)
    finally:
        await live.stop()
        await lake.stop()

    counters = refinery.counters
    logger.info(kv("seed_done", files=len(paths), rows_read=read, rows_out=out,
                   elapsed_s=f"{time.monotonic() - started:.1f}",
                   tracked=len(refinery.latest), **counters.as_fields()))


def main() -> None:
    setup()
    try:
        asyncio.run(run(Settings()))
    except KeyboardInterrupt:
        logger.info(kv("seed_stop", reason="interrupt"))


if __name__ == "__main__":
    main()
