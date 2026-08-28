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
from typing import Any, Protocol

logger = logging.getLogger("map")

REGION = os.environ.get("REGION_SLUG", "north-sea")
UNKNOWN_SYM = "unknown2"  # a field from before the sym token existed


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


async def snapshot_payload(
    client: RedisClient | None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    if client is None:
        raise SnapshotUnavailable("no redis connection")
    try:
        raw = await client.hgetall(f"latest:{REGION}")
    except Exception as exc:
        logger.warning("snapshot unavailable: %s: %s", type(exc).__name__, exc)
        raise SnapshotUnavailable(str(exc)) from exc

    vessels: list[list[Any]] = []
    for mmsi, field in raw.items():
        try:
            _ts, lat, lon, sog, cog, state, *rest = json.loads(field)
            key = int(mmsi)
        except (ValueError, TypeError):
            continue  # a half-written or future-shaped field/key is skipped, not fatal
        if bbox is not None:
            min_lon, min_lat, max_lon, max_lat = bbox
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
        vessels.append([key, lat, lon, cog, sog, state, rest[0] if rest else UNKNOWN_SYM])

    return {
        "region": REGION,
        "ts": int(time.time()),
        "count": len(vessels),
        "vessels": vessels,
    }
