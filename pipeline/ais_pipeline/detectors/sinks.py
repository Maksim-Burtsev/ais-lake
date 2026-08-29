"""The detector's two adapters: `events` in ClickHouse, its state in Redis.

Both are thin on purpose — every decision the detector makes is in machine.py,
where it can be tested without either of them running.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from ..refinery.clickhouse import ClickHouseWriter
from .machine import EVENT_COLUMNS, EventRow

EVENTS_TABLE = "events"

# One row per ship, so the detector's own state does not need the lake to
# restart. vessel_latest is the fallback and knows only her last fix.
LAST_FIX_QUERY = "SELECT mmsi, max(ts) FROM vessel_latest GROUP BY mmsi"


class EventWriter(ClickHouseWriter):
    """The refinery's client plus the two calls the detector needs.

    Inherited rather than copied: the connection handling is the same handful of
    lines, and a second copy of it would drift the first time one was fixed.
    """

    async def insert_events(self, rows: list[EventRow]) -> None:
        await self._insert(EVENTS_TABLE, [r.as_tuple() for r in rows], EVENT_COLUMNS)

    async def last_fixes(self) -> dict[int, datetime]:
        """When we last heard from each ship — the cold-start floor for gaps."""
        if self._client is None:  # pragma: no cover — start() precedes every call
            return {}
        result = await self._client.query(LAST_FIX_QUERY)
        return {int(mmsi): _utc(ts) for mmsi, ts in result.result_rows}


def _utc(ts: datetime) -> datetime:
    """ClickHouse DateTime comes back naive; it is UTC and the machine is aware."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


class SnapshotStore:
    """The crash-safety snapshot: one hash, one field per ship."""

    def __init__(self, url: str, region: str) -> None:
        self._url = url
        self._region = region
        self._client: redis.Redis | None = None

    @property
    def client(self) -> redis.Redis | None:
        """The live connection — shared with the incident log; None before start()."""
        return self._client

    @property
    def key(self) -> str:
        return f"detector:{self._region}"

    async def start(self) -> None:
        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def save(self, fields: Mapping[str, str]) -> None:
        if not fields or self._client is None:
            return
        await self._client.hset(self.key, mapping={k: v for k, v in fields.items()})

    async def load(self) -> dict[str, str]:
        if self._client is None:  # pragma: no cover — start() precedes every call
            return {}
        # decode_responses is on, but the client's own types cannot know that.
        loaded: Any = await self._client.hgetall(self.key)
        return {str(mmsi): str(state) for mmsi, state in loaded.items()}

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
