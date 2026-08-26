"""Redis adapter: the hot snapshot hash and the live delta channel.

Both payloads are positional arrays, not objects — the map ships thousands of
these per second and every key name would be paid for on the wire.

  HSET    latest:{region}  {mmsi} -> [ts_epoch, lat, lon, sog, cog, state]
  PUBLISH live:{region}            [mmsi, lat, lon, cog, sog, state]

The window counters land here too (HSET status:refinery), so /status.json can
read what the refinery is doing without touching the pipeline process.
"""

import json
from collections.abc import Mapping
from typing import Any

import redis.asyncio as redis

from .models import LatestRow

COORD_PRECISION = 5  # ~1 m; more than the map can draw
STATUS_KEY = "status:refinery"


def latest_field(row: LatestRow) -> str:
    return json.dumps(
        [
            int(row.ts.timestamp()),
            round(row.lat, COORD_PRECISION),
            round(row.lon, COORD_PRECISION),
            round(row.sog, 1),
            round(row.cog, 1),
            row.state,
        ],
        separators=(",", ":"),
    )


def live_delta(row: LatestRow) -> str:
    return json.dumps(
        [
            row.mmsi,
            round(row.lat, COORD_PRECISION),
            round(row.lon, COORD_PRECISION),
            round(row.cog, 1),
            round(row.sog, 1),
            row.state,
        ],
        separators=(",", ":"),
    )


class RedisSink:
    def __init__(self, url: str, region: str) -> None:
        self._url = url
        self._region = region
        self._client: redis.Redis | None = None

    @property
    def client(self) -> redis.Redis | None:
        """The live connection — shared with the incident log; None before start()."""
        return self._client

    @property
    def latest_key(self) -> str:
        return f"latest:{self._region}"

    @property
    def live_channel(self) -> str:
        return f"live:{self._region}"

    async def start(self) -> None:
        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def publish(self, rows: list[LatestRow]) -> None:
        """One pipeline per batch: HSET the snapshot, PUBLISH each delta."""
        if not rows or self._client is None:
            return
        pipe: Any = self._client.pipeline(transaction=False)
        pipe.hset(self.latest_key, mapping={str(r.mmsi): latest_field(r) for r in rows})
        for row in rows:
            pipe.publish(self.live_channel, live_delta(row))
        await pipe.execute()

    async def set_status(self, fields: Mapping[str, object]) -> None:
        """Publish the refinery's window counters as a flat hash for /status.json."""
        if self._client is None:
            return
        await self._client.hset(STATUS_KEY, mapping={k: str(v) for k, v in fields.items()})

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
