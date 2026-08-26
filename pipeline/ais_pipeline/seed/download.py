"""Fetch the DMA daily zips into a local cache. Plain HTTP GET, stdlib only.

A dump is a couple of gigabytes: it streams to a .part file and is renamed only
once complete, so an interrupted run never leaves a half file that looks cached.
"""

import logging
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from ..log import kv

logger = logging.getLogger("seed")

CHUNK_BYTES = 1 << 20  # 1 MiB
PROGRESS_EVERY_BYTES = 50 * CHUNK_BYTES  # a line every ~50 MB, no more
TIMEOUT_S = 60.0


def dump_name(day: date) -> str:
    return f"aisdk-{day.isoformat()}"


def dump_url(base_url: str, day: date) -> str:
    return f"{base_url.rstrip('/')}/{dump_name(day)}.zip"


def candidate_days(today: date, days: int, lookback_extra: int) -> list[date]:
    """Days to try, newest first, starting at today-2.

    DMA publishes a day's file a few days late, so we scan further back than
    the requested count and stop once enough dumps are actually fetched.
    """
    end = today - timedelta(days=2)
    return [end - timedelta(days=offset) for offset in range(days + lookback_extra)]


def fetch(url: str, target: Path) -> bool:
    """Download url to target unless it is already cached. True when it is usable."""
    if target.exists():
        logger.info(kv("seed_cached", file=target.name, bytes=target.stat().st_size))
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    logger.info(kv("seed_download", url=url))
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response, part.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            written = last_logged = 0
            while chunk := response.read(CHUNK_BYTES):
                out.write(chunk)
                written += len(chunk)
                if written - last_logged >= PROGRESS_EVERY_BYTES:
                    last_logged = written
                    logger.info(kv("seed_download_progress", file=target.name,
                                   mb=written // CHUNK_BYTES, total_mb=total // CHUNK_BYTES))
    except Exception as exc:  # a missing day must not sink the whole seed
        part.unlink(missing_ok=True)
        logger.warning(kv("seed_download_failed", url=url, reason=type(exc).__name__,
                          detail=str(exc)[:200]))
        return False

    part.rename(target)
    logger.info(kv("seed_downloaded", file=target.name, bytes=target.stat().st_size))
    return True


def ensure_dumps(base_url: str, cache_dir: Path, days: list[date], want: int) -> list[Path]:
    """Fetch dumps for `days` (newest first) until `want` are on disk.

    Returns the cached paths oldest first; a day whose download fails is
    skipped — DMA's publication lag makes the newest day or two 404 routinely.
    """
    paths: list[Path] = []
    for day in days:
        if len(paths) >= want:
            break
        target = cache_dir / f"{dump_name(day)}.zip"
        if fetch(dump_url(base_url, day), target):
            paths.append(target)
    return list(reversed(paths))
