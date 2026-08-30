"""GET /ship/{slug}-{mmsi} — the vessel page, rendered on the server (F12, F31).

The story is the product's shareable artefact, so it has to exist as HTML before
any JavaScript runs: a crawler, a preview card and a reader on a dead train all
get the same words. The SPA then mounts into the very markup this module wrote
and takes the page over — React clears #root, so what follows is not a hydration
contract, only a first paint that happens to be readable.

ONE source of prose: story.py::story_payload writes every sentence, exactly as it
does for /v1. Nothing here composes a sentence, and the same payload is handed to
the client verbatim inside <script id="story-data"> so the takeover is silent.

The URL carries a slug for humans and the MMSI for us; only the trailing nine
digits are read, and a wrong or stale slug 301s to the canonical path so a
renamed ship does not fork into two shareable URLs.
"""

import json
import os
import re
import time
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import asyncpg
import jinja2
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .limits import STORY_LIMIT_LINE
from .map import RedisClient
from .ogpng import card as og_card
from .ships import CardUnavailable, ClickHouseClient, ShipNotFound, card_for
from .story import StoryUnavailable, story_payload
from .track import track_payload

# The built SPA, when there is one. Deliberately opt-in and NOT "web/dist exists":
# `npm run check` builds on every lint pass, so a present dist says nothing about
# whether this process is serving a build or sitting behind `vite dev`.
DIST = Path(os.environ["WEB_DIST"]) if os.environ.get("WEB_DIST") else None
DASH = "—"
MMSI_TAIL = re.compile(r"-(\d{9})$")

env = jinja2.Environment(
    # Inside the package, not beside it: the image copies app/ and nothing else.
    loader=jinja2.FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
)

# Timeline dot colours, keyed by event kind (frame 6a). The SPA carries the same
# five hexes; both read them off the palette in docs/design/tokens.json.
DOTS = {
    "port_call": "#FFB454",
    "load_delta": "#FFB454",
    "anchorage": "#8FB8CC",
    "gap": "#FF6A52",
    "departure": "#5FDCC9",
}


def slugify(name: str | None) -> str:
    """"Gas Khios" -> "gas-khios". A ship with no name yet is simply "ship"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "ship"


def canonical(name: str | None, mmsi: int) -> str:
    return f"/ship/{slugify(name)}-{mmsi}"


@cache
def asset_tags() -> str:
    """The SPA's own script tags: the hashed build when WEB_DIST points at one,
    the Vite dev server otherwise. Read once — a build that lands under a running
    api is a restart either way."""
    manifest = DIST / ".vite" / "manifest.json" if DIST else None
    if manifest is None or not manifest.is_file():
        return (
            '<script type="module">'
            'import R from "http://localhost:5173/@react-refresh";'
            "R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};"
            "window.$RefreshSig$=()=>(t)=>t;"
            "window.__vite_plugin_react_preamble_installed__=true;"
            "</script>"
            '<script type="module" src="http://localhost:5173/@vite/client"></script>'
            '<script type="module" src="http://localhost:5173/src/main.tsx"></script>'
        )
    entry = json.loads(manifest.read_text())["index.html"]
    css = "".join(f'<link rel="stylesheet" href="/{href}">' for href in entry.get("css", []))
    return f'{css}<script type="module" src="/{entry["file"]}"></script>'


def _stamp(start: int, end: int | None) -> str:
    """"14 AUG · 02:10 – 11:40" — the frame's mono timestamp, in UTC."""
    first = datetime.fromtimestamp(start, UTC)
    text = first.strftime("%d %b · %H:%M").upper()
    if end is None or end <= start:
        return text
    return f"{text} – {datetime.fromtimestamp(end, UTC).strftime('%H:%M')}"


def _grouped(mmsi: int) -> str:
    return re.sub(r"(\d{3})(?=\d)", r"\1 ", str(mmsi))


def particulars(mmsi: int, identity: dict[str, Any]) -> list[tuple[str, str]]:
    """The right rail. Every unknown is a dash — never a plausible substitute (F15).

    BEAM has no column in vessels_static (only dim_a + dim_b reach the card as
    size_m), so the beam half is honestly empty rather than guessed from the LOA.
    """
    loa = identity.get("size_m")
    return [
        ("MMSI", _grouped(mmsi)),
        ("IMO", str(identity.get("imo") or DASH)),
        ("CLASS", str(identity.get("class") or DASH)),
        ("FLAG", str(identity.get("flag") or DASH)),
        ("LOA × BEAM", f"{loa} × {DASH} m" if loa else DASH),
        ("DRAUGHT", f"{identity['draught_m']} m" if identity.get("draught_m") else DASH),
        ("CALLSIGN", str(identity.get("callsign") or DASH)),
        ("BOUND FOR", str(identity.get("destination") or DASH)),
        ("ETA", str(identity.get("eta") or DASH)),
    ]


def render(card: dict[str, Any], story: dict[str, Any]) -> str:
    """The whole page as one string. Unique title and description per ship (F31)."""
    identity = card["identity"]
    mmsi = int(card["mmsi"])
    name = identity.get("name") or "Unknown vessel"
    subtitle = " · ".join(
        str(v or DASH)
        for v in (identity.get("class"), identity.get("flag"), f"{identity['size_m']} m"
                  if identity.get("size_m") else None)
    )
    events = story["events"]
    standfirst = card.get("sentence") or (
        f"No movements recorded for her in the last {story['window_d']} days."
    )
    return env.get_template("ship.html.j2").render(
        name=name,
        mmsi=mmsi,
        subtitle=subtitle,
        standfirst=standfirst,
        title=f"{name} — where she has been | ais·lake",
        # The sentence is the refinery's and carries no full stop of its own.
        description=(
            f"{name} ({subtitle}): {standfirst.rstrip('.')}. "
            f"{len(events)} movements in the last {story['window_d']} days."
        ),
        canonical=canonical(identity.get("name"), mmsi),
        limit_line=STORY_LIMIT_LINE,
        particulars=particulars(mmsi, identity),
        entries=[
            {
                "dot": DOTS.get(str(e["kind"]), "#6E8798"),
                "prose": e["prose"],
                "stamp": _stamp(int(e["t_start"]), e["t_end"]),
                # The only line beside the prose: the gap detector's label. The
                # port is already IN the sentence, so repeating it says nothing.
                "note": (e.get("flag") or {}).get("label"),
                "silent": e["kind"] == "gap",
                # F13: the door into the opened silence, in the markup before any
                # script runs — the SPA takes the same href over.
                "gap": f"?gap={e['event_id']}" if e["kind"] == "gap" else None,
            }
            for e in events
        ],
        # "</script>" inside a JSON string would close the tag it lives in.
        payload=json.dumps({"card": card, "story": story}).replace("<", "\\u003c"),
        assets=asset_tags(),
    )


def _mmsi_of(path: str) -> str:
    found = MMSI_TAIL.search(path)
    if not found:
        raise HTTPException(404, "no such ship")
    return found.group(1)


async def ship_page(
    ch: ClickHouseClient | None,
    redis_client: RedisClient | None,
    pool: asyncpg.Pool | None,
    path: str,
) -> Response:
    key = _mmsi_of(path)
    try:
        card = await card_for(ch, redis_client, key)
        story = await story_payload(ch, pool, key)
    except ShipNotFound as exc:
        raise HTTPException(404, "no such ship") from exc
    except (CardUnavailable, StoryUnavailable) as exc:
        raise HTTPException(503, "vessel page unavailable") from exc

    want = canonical(card["identity"].get("name"), int(card["mmsi"]))
    if f"/ship/{path}" != want:
        return RedirectResponse(want, status_code=301)
    return HTMLResponse(render(card, story))


# ponytail: a plain dict, one entry per ship per day, never evicted below 512.
# The cards are ~20 KB each and the crawlers ask for the same handful; swap in a
# real LRU (or a file cache) the day this holds more than a demo's worth.
_og_cache: dict[tuple[str, str], bytes] = {}


async def og_image(ch: ClickHouseClient | None, path: str) -> Response:
    key = _mmsi_of(path)
    day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
    png = _og_cache.get((key, day))
    if png is None:
        try:
            track = await track_payload(ch, key)
            coordinates = track["geometry"]["coordinates"]
        except (ShipNotFound, StoryUnavailable):
            coordinates = []  # a ship we cannot read still gets the plain plate
        png = og_card(coordinates)
        if len(_og_cache) > 512:
            _og_cache.clear()
        _og_cache[(key, day)] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


__all__ = ["canonical", "og_image", "render", "ship_page", "slugify"]
