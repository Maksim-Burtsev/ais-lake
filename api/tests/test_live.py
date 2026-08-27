"""/v1/live against a fake socket and a fake channel — no Redis, no server."""

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import suppress
from typing import Any

from starlette.websockets import WebSocketDisconnect

from app.limits import REFRESH, clamp_interval
from app.live import Deltas, live_socket

TICK = 0.01  # the tick the fake socket runs at; the wire cadence is clamp_interval's job


def delta(mmsi: int, lat: float, lon: float, cog: float = 90.0, sog: float = 12.0) -> str:
    """Exactly what refinery/redis_sink.live_delta publishes: cog BEFORE sog."""
    return json.dumps([mmsi, lat, lon, cog, sog, "underway"])


Step = str | None | Callable[[], None]


class FakeWebSocket:
    """Scripted client. `None` in the script blocks (so the tick fires); a string
    is a client command; a callable runs (the sea moving mid-connection) and then
    blocks; the script running out is the client hanging up."""

    def __init__(self, *script: Step) -> None:
        self._script: Iterator[Step] = iter(script)
        self.sent: list[Any] = []
        self.accepted = False
        self.closed: tuple[int, str | None] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        try:
            item = next(self._script)
        except StopIteration:
            raise WebSocketDisconnect(1000) from None
        if callable(item):
            item()
            item = None
        if item is None:
            await asyncio.sleep(3600)  # the wait_for timeout is the tick
            raise AssertionError("unreachable")
        assert isinstance(item, str)
        return item

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


def test_last_frame_per_ship_wins() -> None:
    deltas = Deltas()
    deltas.apply(delta(244660000, 55.0, 3.0))
    deltas.apply(delta(244660000, 55.2, 3.1))
    deltas.apply(delta(205344000, 51.9, 4.1))
    _, frames = deltas.since(0)
    assert len(deltas) == 3 - 1
    assert sorted(f[0] for f in frames) == [205344000, 244660000]
    assert next(f for f in frames if f[0] == 244660000)[1:3] == [55.2, 3.1]


def test_junk_payloads_are_skipped_not_fatal() -> None:
    deltas = Deltas()
    for junk in ("not json", "[1,2]", '{"mmsi":1}', '["x",55.0,3.0,90,12,"underway"]',
                 '[1,"north",3.0,90,12,"underway"]'):
        deltas.apply(junk)
    assert len(deltas) == 0


def test_since_culls_by_bbox_and_advances_the_cursor() -> None:
    deltas = Deltas()
    deltas.apply(delta(244660000, 55.0, 3.0))  # inside
    deltas.apply(delta(205344000, 43.0, 9.0))  # outside
    cursor, frames = deltas.since(0, (2.0, 54.0, 5.0, 57.0))
    assert [f[0] for f in frames] == [244660000]
    assert deltas.since(cursor, (2.0, 54.0, 5.0, 57.0)) == (cursor, [])  # nothing new
    deltas.apply(delta(244660000, 55.1, 3.0))
    _, frames = deltas.since(cursor, (2.0, 54.0, 5.0, 57.0))
    assert [f[0] for f in frames] == [244660000]


def test_trim_bounds_memory_by_dropping_the_quietest() -> None:
    deltas = Deltas(max_ships=2)
    for mmsi in (1, 2, 3):
        deltas.apply(delta(200000000 + mmsi, 55.0, 3.0))
    deltas.trim()
    _, frames = deltas.since(0)
    assert sorted(f[0] for f in frames) == [200000002, 200000003]


def test_interval_is_clamped_to_the_tier_floor() -> None:
    assert clamp_interval(1) == 10  # below the anon floor -> the default cadence
    assert clamp_interval(30) == 30
    assert clamp_interval(10) == 10
    assert clamp_interval("fast") == 10
    assert clamp_interval(None) == 10
    assert clamp_interval(5) == 10  # 5 s is a free-account floor…
    assert clamp_interval(5, tier="free") == 5  # …and only that
    assert REFRESH["floor"]["anon"] == 10  # the spec's limits table, §03


async def test_no_redis_closes_instead_of_showing_an_empty_sea() -> None:
    ws = FakeWebSocket(None)
    await live_socket(ws, Deltas(), available=False, interval=TICK)
    assert ws.accepted and ws.sent == []
    assert ws.closed is not None and ws.closed[0] == 1011


async def test_every_tick_sends_a_frame_even_when_nothing_moved() -> None:
    deltas = Deltas()
    deltas.apply(delta(244660000, 55.0, 3.0))  # already in the client's snapshot
    ws = FakeWebSocket(lambda: deltas.apply(delta(244660000, 55.2, 3.0)), None, None)
    await live_socket(ws, deltas, available=True, interval=TICK)
    assert [f["vessels"] for f in ws.sent] == [
        [[244660000, 55.2, 3.0, 90.0, 12.0, "underway"]],  # what moved since the snapshot
        [],  # the heartbeat: nothing moved, and the client can tell it is alive
        [],
    ]
    assert ws.sent[1]["interval"] == TICK and ws.sent[1]["ts"] > 0


async def test_interval_command_changes_the_next_tick() -> None:
    """Switching cadence takes effect on the very next cycle, no reconnect (F1)."""
    ws = FakeWebSocket(json.dumps({"interval": 30}), None)
    task = asyncio.create_task(live_socket(ws, Deltas(), available=True, interval=TICK))
    await asyncio.sleep(TICK * 20)
    assert ws.sent == []  # the 10 ms tick is gone; the next one is 30 s out
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_bad_bbox_command_is_answered_and_the_socket_stays_open() -> None:
    deltas = Deltas()

    def move() -> None:
        deltas.apply(delta(244660000, 55.0, 3.0))

    ws = FakeWebSocket(json.dumps({"bbox": "north sea"}), "not json", move)
    await live_socket(ws, deltas, available=True, bbox=(2.0, 54.0, 5.0, 57.0), interval=TICK)
    assert "error" in ws.sent[0] and "error" in ws.sent[1]
    assert ws.closed is None
    assert ws.sent[2]["vessels"][0][0] == 244660000  # the old bbox still culls


async def test_bbox_command_replaces_the_previous_one() -> None:
    deltas = Deltas()

    def move() -> None:
        deltas.apply(delta(244660000, 55.0, 3.0))

    ws = FakeWebSocket(json.dumps({"bbox": "-6,43,-4,45"}), move)
    await live_socket(ws, deltas, available=True, bbox=(2.0, 54.0, 5.0, 57.0), interval=TICK)
    assert ws.sent[0]["vessels"] == []  # the ship is no longer in view
