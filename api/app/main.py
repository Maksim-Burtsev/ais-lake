"""The api process: the M0 /debug eyeball and /status.json.

Environment names match the pipeline's Settings on purpose (CLICKHOUSE_HOST,
REDIS_URL, …) — one box, one set of names.
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import clickhouse_connect
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import HTMLResponse

from .consumer import LatestShips, consume_forever
from .limits import clamp_interval
from .live import Deltas, live_socket, subscribe_forever
from .map import SnapshotUnavailable, parse_bbox, snapshot_payload
from .ports import PortsUnavailable, port_payload, ports_geojson
from .regions import regions_payload
from .search import search_payload
from .ships import CardUnavailable, ShipNotFound, card_for
from .status import build_status

logger = logging.getLogger("api")

ships = LatestShips()
deltas = Deltas()


class Runtime:
    """Process-wide handles. Missing stores stay None; /status.json copes."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.clickhouse: Any = None
        self.redis: redis.Redis | None = None
        self.postgres: asyncpg.Pool | None = None

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.started_at


runtime = Runtime()


async def open_clickhouse() -> Any:
    try:
        return await clickhouse_connect.get_async_client(
            host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
            port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            username=os.environ.get("CLICKHOUSE_USER", "ais"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", "ais-dev"),
            database=os.environ.get("CLICKHOUSE_DATABASE", "ais"),
        )
    except Exception as exc:
        logger.warning("clickhouse unavailable: %s: %s", type(exc).__name__, exc)
        return None


async def open_redis() -> redis.Redis | None:
    try:
        client = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
        )
        await client.ping()
    except Exception as exc:
        logger.warning("redis unavailable: %s: %s", type(exc).__name__, exc)
        return None
    return client


async def open_postgres() -> asyncpg.Pool | None:
    try:
        return await asyncpg.create_pool(
            os.environ.get("POSTGRES_URL", "postgresql://ais:ais-dev@localhost:5432/ais")
        )
    except Exception as exc:
        logger.warning("postgres unavailable: %s: %s", type(exc).__name__, exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime.started_at = time.monotonic()
    runtime.clickhouse = await open_clickhouse()
    runtime.redis = await open_redis()
    runtime.postgres = await open_postgres()
    task = asyncio.create_task(
        consume_forever(
            ships,
            bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092"),
            topic=os.environ.get("RAW_TOPIC", "ais.raw"),
        )
    )
    # One subscriber for the whole process; every /v1/live socket reads from it.
    live_task = asyncio.create_task(subscribe_forever(deltas, runtime.redis))
    yield
    live_task.cancel()
    task.cancel()
    if runtime.redis is not None:
        await runtime.redis.aclose()
    if runtime.clickhouse is not None:
        await runtime.clickhouse.close()
    if runtime.postgres is not None:
        await runtime.postgres.close()


app = FastAPI(title="ais-lake api", lifespan=lifespan)


@app.get("/status.json")
async def status_json() -> dict[str, Any]:
    """The honest numbers. Never 500s — an unreachable store becomes a null."""
    return await build_status(runtime.clickhouse, runtime.redis, runtime.uptime_s)


@app.get("/v1/map/snapshot")
async def map_snapshot(bbox: str | None = None, zoom: float | None = None) -> dict[str, Any]:
    """Every vessel the refinery currently knows, culled to bbox. `zoom` is
    accepted so the client can send it, and ignored until LOD lands server-side."""
    try:
        box = parse_bbox(bbox) if bbox else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await snapshot_payload(runtime.redis, box)
    except SnapshotUnavailable as exc:
        raise HTTPException(503, "live snapshot unavailable") from exc


@app.get("/v1/map/ports")
async def map_ports() -> dict[str, Any]:
    """The port and anchorage polygons, as one FeatureCollection. Static data —
    read once per process, so this is a dict lookup after the first call."""
    try:
        return await ports_geojson(runtime.postgres)
    except PortsUnavailable as exc:
        raise HTTPException(503, "port polygons unavailable") from exc


@app.get("/v1/ports/{locode}")
async def port(locode: str) -> dict[str, Any]:
    """F14's panel skeleton: the port's identity, with the queue numbers still
    null until the detector fills them. An unknown locode 404s."""
    try:
        payload = await port_payload(runtime.postgres, locode)
    except PortsUnavailable as exc:
        raise HTTPException(503, "port unavailable") from exc
    if payload is None:
        raise HTTPException(404, "no such port")
    return payload


@app.get("/v1/regions")
async def regions() -> dict[str, Any]:
    """F6: the picker's seas and straits with a live count each. Never 503s —
    Redis down means every count is null and the panel shows "—"."""
    return await regions_payload(runtime.redis)


@app.get("/v1/search")
async def search(q: str = "") -> dict[str, Any]:
    """F5: ships, ports and seas for the top bar's dropdown. Never 503s — a store
    that is gone costs its own group and the panel says what it still knows."""
    return await search_payload(runtime.clickhouse, runtime.redis, q)


@app.get("/v1/ships/{key}")
async def ship_card(key: str) -> dict[str, Any]:
    """F8: identity, the server-rendered sentence and the latest fix, for the card
    behind a ship tap. `key` is a 9-digit MMSI or a 7-digit IMO; a key of any other
    shape 404s alongside the ships we simply do not hold (see ships.py)."""
    try:
        return await card_for(runtime.clickhouse, runtime.redis, key)
    except ShipNotFound as exc:
        raise HTTPException(404, "no such ship") from exc
    except CardUnavailable as exc:
        raise HTTPException(503, "ship card unavailable") from exc


@app.websocket("/v1/live")
async def live_feed(ws: WebSocket, bbox: str | None = None, interval: int | None = None) -> None:
    """F1/F29: delta frames on the client's own cadence. A junk bbox is refused
    here (1008) — the client has one to send; a junk bbox in a later command is
    answered on the open socket instead."""
    try:
        box = parse_bbox(bbox) if bbox else None
    except ValueError as exc:
        await ws.close(1008, str(exc))
        return
    await live_socket(
        ws,
        deltas,
        available=runtime.redis is not None,
        bbox=box,
        interval=clamp_interval(interval),
    )


@app.get("/debug/ships")
async def debug_ships() -> dict[str, Any]:
    now = time.time()
    return {
        "ships_seen": len(ships),
        "top": [
            {
                "mmsi": s.mmsi,
                "name": s.name,
                "lat": round(s.lat, 5),
                "lon": round(s.lon, 5),
                "sog": s.sog,
                "age_s": round(now - s.ts, 1),
            }
            for s in ships.top(20)
        ],
    }


DEBUG_HTML = """<!doctype html>
<title>ais-lake · steel thread</title>
<style>body{font:14px monospace;padding:2rem}td,th{padding:2px 12px;text-align:left}</style>
<h1>the sea, as text</h1>
<p id=meta></p>
<table><thead><tr><th>MMSI</th><th>name</th><th>lat</th><th>lon</th>
<th>sog</th><th>age s</th></tr></thead>
<tbody id=rows></tbody></table>
<script>
async function tick(){
  const r = await fetch('/debug/ships'); const d = await r.json();
  document.getElementById('meta').textContent = d.ships_seen + ' ships seen';
  document.getElementById('rows').innerHTML = d.top.map(s =>
    `<tr><td>${s.mmsi}</td><td>${s.name}</td><td>${s.lat}</td><td>${s.lon}` +
    `</td><td>${s.sog ?? '—'}</td><td>${s.age_s}</td></tr>`).join('');
}
tick(); setInterval(tick, 5000);
</script>"""


@app.get("/debug")
async def debug_page() -> HTMLResponse:
    return HTMLResponse(DEBUG_HTML)
