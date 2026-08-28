"""/v1/live — the WS gateway (F1 live layer, F29 delta protocol).

The refinery publishes one compact array per accepted row to Redis pub/sub
`live:{region}` — thousands per two-second flush. ONE process-wide subscriber
drains that channel into `Deltas`, where the last frame per MMSI wins. That
collapse is what keeps an idle viewport under 2 KB/s however loud the sea is:
a client ticking every 10 s pays for ships that moved, not for messages.

Each socket then ticks on its own cadence (5/10/30 s): every tick ships the
frames newer than that client's cursor and inside its bbox. Empty ticks are
sent too (~40 B) — without them a client cannot tell "nothing moved" from
"socket dead", which is exactly what the LIVE dot has to show.

The wire, both ways:
  server -> {"ts": …, "interval": 10, "vessels": [[mmsi, lat, lon, cog, sog, state, sym], …]}
  client -> {"bbox": "minLon,minLat,maxLon,maxLat", "interval": 30}

The client message is a patch applied in place — no reconnect when the viewport
or the cadence changes. A bad bbox is answered with {"error": …} and the
previous one is kept; the socket stays open.
"""

import asyncio
import json
import logging
import time
from typing import Any, Protocol

from starlette.websockets import WebSocketDisconnect

from .limits import clamp_interval
from .map import REGION, parse_bbox

logger = logging.getLogger("live")

BBox = tuple[float, float, float, float]
Frame = list[Any]

FRAME_FIELDS = (6, 7)  # [mmsi, lat, lon, cog, sog, state] (+ sym, appended later)
MAX_SHIPS = 50_000  # the launch region holds ~10k; the cap is a memory floor, not a policy
RESUBSCRIBE_S = 2.0


class Socket(Protocol):
    """The slice of starlette's WebSocket this module uses (and tests fake)."""

    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class Deltas:
    """Latest frame per MMSI, with a monotonic sequence so each socket can ask
    "what changed since I last looked" without keeping a queue of its own."""

    def __init__(self, max_ships: int = MAX_SHIPS) -> None:
        self._rows: dict[int, tuple[int, Frame]] = {}
        self._seq = 0
        self._max_ships = max_ships

    @property
    def cursor(self) -> int:
        return self._seq

    def apply(self, raw: str | bytes) -> None:
        """One published delta. Last one per ship wins — that IS the dedup."""
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            return  # a half-written or future-shaped payload is skipped, not fatal
        if not isinstance(frame, list) or len(frame) not in FRAME_FIELDS:
            return
        # lat/lon are compared against the bbox in since(); text there would blow up
        if not isinstance(frame[1], int | float) or not isinstance(frame[2], int | float):
            return
        try:
            mmsi = int(frame[0])
        except (TypeError, ValueError):
            return
        self._seq += 1
        self._rows[mmsi] = (self._seq, frame)

    def since(self, cursor: int, bbox: BBox | None = None) -> tuple[int, list[Frame]]:
        """Frames newer than `cursor` and inside `bbox`, plus the new cursor.

        Culled ships still advance the cursor: a client that pans gets the ships
        it missed from a fresh snapshot (F6), not from this backlog.
        """
        out: list[Frame] = []
        for seq, frame in self._rows.values():
            if seq <= cursor:
                continue
            if bbox is not None:
                min_lon, min_lat, max_lon, max_lat = bbox
                if not (min_lon <= frame[2] <= max_lon and min_lat <= frame[1] <= max_lat):
                    continue
            out.append(frame)
        return self._seq, out

    def trim(self) -> None:
        """Bound the memory: drop the ships that have been quiet longest."""
        excess = len(self._rows) - self._max_ships
        if excess <= 0:
            return
        oldest = sorted(self._rows.items(), key=lambda item: item[1][0])[:excess]
        for mmsi, _ in oldest:
            del self._rows[mmsi]

    def __len__(self) -> int:
        return len(self._rows)


async def subscribe_forever(deltas: Deltas, client: Any, region: str = REGION) -> None:
    """Drain live:{region} into `deltas`. One task per process, not per socket:
    the refinery publishes per row, and every extra subscriber pays for all of them."""
    if client is None:
        return
    while True:
        try:
            pubsub = client.pubsub()
            await pubsub.subscribe(f"live:{region}")
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                deltas.apply(message["data"])
                deltas.trim()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("live: pubsub dropped: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(RESUBSCRIBE_S)


async def live_socket(
    ws: Socket,
    deltas: Deltas,
    *,
    available: bool,
    bbox: BBox | None = None,
    interval: float = 10,
) -> None:
    """One client. The receive timeout is both the tick and the command channel,
    so a connection costs one task, not two."""
    await ws.accept()
    if not available:
        # Same principle as the snapshot: a silently-open empty socket would read
        # as "no ships out there", which is a lie. Say the feed is gone instead.
        await ws.close(1011, "live feed unavailable")
        return

    cursor = deltas.cursor  # everything older is already in the client's snapshot
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=interval)
            except TimeoutError:
                cursor, vessels = deltas.since(cursor, bbox)
                await ws.send_text(
                    json.dumps(
                        {"ts": int(time.time()), "interval": interval, "vessels": vessels},
                        separators=(",", ":"),
                    )
                )
                continue
            try:
                command = json.loads(raw)
                if not isinstance(command, dict):
                    raise ValueError("expected a JSON object")
                if "bbox" in command:
                    bbox = parse_bbox(str(command["bbox"]))
                if "interval" in command:
                    interval = clamp_interval(command["interval"])
            except (ValueError, TypeError) as exc:
                await ws.send_text(json.dumps({"error": str(exc)}))
    except (WebSocketDisconnect, RuntimeError):
        return  # the client went away mid-receive or mid-send; nothing to clean up
