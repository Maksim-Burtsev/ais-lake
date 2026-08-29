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

from .limits import MAX_VESSEL_AGE_S, SILENT_AFTER_S

logger = logging.getLogger("map")

REGION = os.environ.get("REGION_SLUG", "north-sea")
UNKNOWN_SYM = "unknown2"  # a field from before the sym token existed
# [ts, lat, lon, sog, cog, state] (+ sym, appended later). live.py reads the same
# schema off the pub/sub and imports this, so the two readers cannot drift apart.
FRAME_FIELDS = (6, 7)

# ponytail: a process-local memo of the decoded hash, upgrade path is per-region
# counters the refinery maintains (M3), which is where a count belongs anyway.
# One HGETALL of ~23k fields plus a json.loads each is ~110 ms of SYNCHRONOUS
# work after the await returns — loop-blocking latency for every other request in
# the process — and three endpoints now pay it: the snapshot, /v1/regions, and
# /v1/search's sea counts. The search box debounces at 120 ms, so typing "north"
# fires several of them back to back.
# 2 s because that is under the fastest cadence a client may ask for
# (limits.json map_refresh_s.floor.free = 5 s): nobody can be served a frame
# staler than the refresh they chose, and the refinery rewrites the hash
# continuously, so reusing a two-second-old read is honest rather than cheap.
MEMO_S = 2.0
_memo: tuple[Any, float, list[list[Any]]] | None = None


class RedisClient(Protocol):
    def hgetall(self, name: str) -> Any: ...
    def hget(self, name: str, key: str) -> Any: ...


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


async def _rows(client: RedisClient | None) -> list[list[Any]]:
    """ONE HGETALL of the hot hash, decoded to [mmsi, lat, lon, cog, sog, state, sym].

    Every reader of the hash goes through here (snapshot + the region counts), so
    the skip-the-junk rules, the age cut and the field transpose live in exactly
    one place. Memoised for MEMO_S — see the note on it.
    """
    global _memo
    if client is None:
        raise SnapshotUnavailable("no redis connection")
    # The memo keeps a strong reference to the client it was read from and
    # compares by identity, so a reconnect (or a test's fake) can never be handed
    # another connection's fleet.
    if _memo is not None and _memo[0] is client and time.time() - _memo[1] < MEMO_S:
        return _memo[2]
    try:
        raw = await client.hgetall(f"latest:{REGION}")
    except Exception as exc:
        logger.warning("snapshot unavailable: %s: %s", type(exc).__name__, exc)
        raise SnapshotUnavailable(str(exc)) from exc

    # Read here, never carried across a memo hit: the cut measures against the
    # wall clock at decode time, so a reused row is at worst MEMO_S past its own
    # cut — two seconds inside a 24 h window.
    now = time.time()
    rows = [
        row
        for mmsi, field in raw.items()
        if (row := _decode(mmsi, field, now, MAX_VESSEL_AGE_S)) is not None
    ]
    _memo = (client, now, rows)
    return rows


def _decode(mmsi: Any, field: Any, now: float, max_age: float | None) -> list[Any] | None:
    """One hash field -> [mmsi, lat, lon, cog, sog, state, sym], or None if it is
    junk or from before the sym token.

    `max_age` is the liveness cut in seconds, or None for no cut at all. It is the
    caller's call because it is a property of the QUESTION, not of the field: the
    map and the counts mean live when they say live, while the card only wants the
    sym token, which encodes class and size from static data and does not decay.
    """
    try:
        decoded = json.loads(field)
        key = int(mmsi)
    except (ValueError, TypeError):
        return None  # a half-written or future-shaped field/key is skipped, not fatal
    # a dict would unpack into its key names, so demand the wire's own shape
    if not isinstance(decoded, list) or len(decoded) not in FRAME_FIELDS:
        return None
    ts, lat, lon, sog, cog, state, *rest = decoded
    # lat/lon are compared against the bbox by every caller; text there would
    # blow up a count or a cull. Same guard live.py applies to its frames.
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    # the refinery never expires a field, so a ship that stopped reporting days
    # ago is still in the hash. Past the cut it is neither drawn nor counted —
    # "live" has to mean live. A text ts is unreadable, so it goes too.
    # ponytail: read-side cut only, the hash itself still grows without bound.
    # The HDEL sweep is refinery hygiene and deliberately not in this task.
    if not isinstance(ts, int | float):
        return None
    if max_age is not None and now - ts > max_age:
        return None
    # Silence is not a fact the refinery can write: it is the ABSENCE of one, and
    # nothing arrives to record it. The detector knows who has gone quiet, but it
    # would have to write into a key the refinery owns to say so. The timestamp
    # already in this field answers it without a second writer — silent means we
    # have not heard from her since `silent_after`, which is what the map paints
    # coral and what F7's chip counts. She stays visible for another day (see
    # limits.json), which is the whole point of the two windows being different.
    if now - ts > SILENT_AFTER_S:
        state = "silent"
    return [key, lat, lon, cog, sog, state, rest[0] if rest else UNKNOWN_SYM]


async def row_for(client: RedisClient | None, mmsi: int) -> list[Any] | None:
    """ONE HGET for a single ship, through the same decoder as the snapshot.

    The card wants the `sym` token that already rides the wire; a second
    json.loads site is exactly how the two readers would drift apart. Unlike the
    snapshot this one does not raise when Redis is missing — a card without a
    sprite token only loses its class, and a card is still worth drawing.

    No age cut, deliberately: the token carries class and size off the static
    message, and neither of those decays. A ship 30 h silent still gets her
    latest fix, her state and her sentence on the card — every field that DOES
    decay — so blanking the one that does not would be backwards.
    """
    if client is None:
        return None
    try:
        field = await client.hget(f"latest:{REGION}", str(mmsi))
    except Exception as exc:
        logger.warning("hot hash unavailable: %s: %s", type(exc).__name__, exc)
        return None
    return None if field is None else _decode(mmsi, field, time.time(), None)


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
