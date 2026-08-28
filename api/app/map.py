"""/v1/map/snapshot — every vessel the refinery knows about right now.

The refinery's hot hash (`latest:{region}`) stores positional arrays to keep the
wire cheap: [ts, lat, lon, sog, cog, state, sym]. The map frame wants heading
before speed, so the one job here is the transpose to
[mmsi, lat, lon, cog, sog, state, sym] (plus a bbox cull, so a harbour view
doesn't ship the whole North Sea). `sym` is the sprite token; fields written
before it existed are still six long and read as an unknown silhouette.

Unlike /status.json this one does NOT degrade to an empty payload when Redis is
gone: an empty sea reads as "no ships out there", which is a lie. No snapshot is
a 503 and the map says so.
"""

import json
import logging
import os
import time
from collections.abc import Iterator
from typing import Any, Protocol

from .limits import MAX_VESSEL_AGE_S

logger = logging.getLogger("map")

REGION = os.environ.get("REGION_SLUG", "north-sea")
UNKNOWN_SYM = "unknown2"  # a field from before the sym token existed
# [ts, lat, lon, sog, cog, state] (+ sym, appended later). live.py reads the same
# schema off the pub/sub and imports this, so the two readers cannot drift apart.
FRAME_FIELDS = (6, 7)


class RedisClient(Protocol):
    def hgetall(self, name: str) -> Any: ...


class SnapshotUnavailable(Exception):
    """Redis is missing or unreachable — the caller turns this into a 503."""


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """minLon,minLat,maxLon,maxLat. Raises ValueError on anything else — including
    an inverted box, which would otherwise cull everything and lie 'empty sea'."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("bbox needs four numbers: minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError("bbox corners are inverted: expected minLon<maxLon, minLat<maxLat")
    if not (-180.0 <= min_lon and max_lon <= 180.0 and -90.0 <= min_lat and max_lat <= 90.0):
        raise ValueError("bbox out of range: lon in [-180, 180], lat in [-90, 90]")
    return min_lon, min_lat, max_lon, max_lat


def inside(bbox: tuple[float, float, float, float], lon: float, lat: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


async def _rows(client: RedisClient | None) -> Iterator[list[Any]]:
    """ONE HGETALL of the hot hash, decoded to [mmsi, lat, lon, cog, sog, state, sym].

    Every reader of the hash goes through here (snapshot + the region counts), so
    the skip-the-junk rules, the age cut and the field transpose live in exactly
    one place.
    """
    if client is None:
        raise SnapshotUnavailable("no redis connection")
    try:
        raw = await client.hgetall(f"latest:{REGION}")
    except Exception as exc:
        logger.warning("snapshot unavailable: %s: %s", type(exc).__name__, exc)
        raise SnapshotUnavailable(str(exc)) from exc

    now = time.time()

    def decode() -> Iterator[list[Any]]:
        for mmsi, field in raw.items():
            try:
                decoded = json.loads(field)
                key = int(mmsi)
            except (ValueError, TypeError):
                continue  # a half-written or future-shaped field/key is skipped, not fatal
            # a dict would unpack into its key names, so demand the wire's own shape
            if not isinstance(decoded, list) or len(decoded) not in FRAME_FIELDS:
                continue
            ts, lat, lon, sog, cog, state, *rest = decoded
            # lat/lon are compared against the bbox by every caller; text there would
            # blow up a count or a cull. Same guard live.py applies to its frames.
            if not isinstance(lat, int | float) or not isinstance(lon, int | float):
                continue
            # the refinery never expires a field, so a ship that stopped reporting days
            # ago is still in the hash. Past the cut it is neither drawn nor counted —
            # "live" has to mean live. A text ts is unreadable, so it goes too.
            # ponytail: read-side cut only, the hash itself still grows without bound.
            # The HDEL sweep is refinery hygiene and deliberately not in this task.
            if not isinstance(ts, int | float) or now - ts > MAX_VESSEL_AGE_S:
                continue
            yield [key, lat, lon, cog, sog, state, rest[0] if rest else UNKNOWN_SYM]

    return decode()


async def counts_for(
    client: RedisClient | None,
    boxes: list[tuple[float, float, float, float] | None],
) -> list[int | None]:
    """Ships per bbox from a single hash read. A None box (not live yet) stays None."""
    rows = await _rows(client)
    counts = [None if box is None else 0 for box in boxes]
    for row in rows:
        for i, box in enumerate(boxes):
            if box is not None and inside(box, row[2], row[1]):
                counts[i] += 1  # type: ignore[operator]
    return counts


async def snapshot_payload(
    client: RedisClient | None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    vessels = [
        row for row in await _rows(client) if bbox is None or inside(bbox, row[2], row[1])
    ]
    return {
        "region": REGION,
        "ts": int(time.time()),
        "count": len(vessels),
        "vessels": vessels,
    }
