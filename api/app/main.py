"""M0 steel thread: bare /debug page listing live ships as text. No design."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .consumer import LatestShips, consume_forever

ships = LatestShips()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(
        consume_forever(
            ships,
            bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092"),
            topic=os.environ.get("RAW_TOPIC", "ais.raw"),
        )
    )
    yield
    task.cancel()


app = FastAPI(title="ais-lake api", lifespan=lifespan)


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
