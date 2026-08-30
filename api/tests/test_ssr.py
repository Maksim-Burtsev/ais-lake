"""The server-rendered vessel page and its share card (F12, F15, F31).

No TestClient: httpx is not a dependency of this service, so the route handlers
are awaited directly — they are plain coroutines that take their clients.
"""

import json
import re
import time
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from app import ogpng
from app.ssr import canonical, og_image, render, ship_page, slugify

MMSI = 249118000
NOW = time.time()
T0 = datetime.fromtimestamp(NOW - 3 * 86_400, UTC).replace(tzinfo=None, microsecond=0)


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeLake:
    """One ClickHouse for every query the page makes, keyed on the query text."""

    def __init__(self, name: str = "Gas Khios", events: bool = True) -> None:
        self.name, self.events = name, events

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        if "vessel_latest" in query:
            row = (T0, 44.7, 37.8, 9.8, 45.0, 90, 0, "underway", "Under way at 9.8 kn")
            return FakeResult([row])
        if "vessels_static" in query:
            return FakeResult([(9410102, self.name, "9HA4622", 100, 20, 8.4, "ROTTERDAM", "08-29")])
        if "FROM events" in query and "kind = 'gap'" in query:
            return FakeResult([])
        if "FROM events" in query:
            rows = [
                ("ev-1", "port_call", T0, T0 + timedelta(hours=9), "RUNVS", "{}"),
                (
                    "ev-2",
                    "gap",
                    T0 + timedelta(hours=20),
                    T0 + timedelta(hours=23),
                    "",
                    json.dumps({"classification": "unusual", "confidence": 0.8}),
                ),
            ]
            return FakeResult(rows if self.events else [])
        if "FROM positions" in query:
            return FakeResult([(T0 + timedelta(minutes=i), 44.0 + i * 0.02, 37.0 + i * 0.03)
                               for i in range(30)])
        return FakeResult([])


async def page(path: str, lake: FakeLake | None = None) -> Any:
    return await ship_page(lake or FakeLake(), None, None, path)


def test_slug() -> None:
    assert slugify("Gas Khios") == "gas-khios"
    assert slugify("MSC  Éclair!! II") == "msc-clair-ii"
    assert slugify(None) == "ship" and slugify("   ") == "ship"
    assert canonical("Gas Khios", MMSI) == f"/ship/gas-khios-{MMSI}"


async def test_stale_slug_redirects_to_canonical() -> None:
    response = await page(f"her-old-name-{MMSI}")
    assert response.status_code == 301
    assert response.headers["location"] == f"/ship/gas-khios-{MMSI}"


async def test_path_without_an_mmsi_404s() -> None:
    with pytest.raises(HTTPException) as raised:
        await page("gas-khios")
    assert raised.value.status_code == 404


async def test_page_is_readable_html_before_any_script() -> None:
    body = bytes((await page(f"gas-khios-{MMSI}")).body).decode()
    assert "<title>Gas Khios — where she has been | ais·lake</title>" in body
    assert f'<link rel="canonical" href="/ship/gas-khios-{MMSI}">' in body
    og = re.search(r'property="og:image" content="(.*?)"', body).group(1)  # type: ignore[union-attr]
    # Preview crawlers drop a relative one, so it must be absolute.
    assert og.startswith("http") and og.endswith("/ship/gas-khios-249118000/og.png")
    assert f'property="og:url" content="{og[: -len("/og.png")]}"' in body
    # the prose is story.py's, rendered into the markup rather than fetched
    assert "Moored in RUNVS" in body or "Moored" in body
    assert "Went silent" in body
    assert "Unusual for this area" in body  # the flag's label, never its confidence
    # the confidence rides along in the payload, exactly as it does on /v1 — but
    # it must never reach the words on the page (CLAUDE.md: no scores on a page).
    assert "0.8" not in body.split('id="story-data"')[0]
    assert "249 118 000" in body and "9HA4622" in body


async def test_title_and_description_are_unique_per_ship() -> None:
    first = bytes((await page(f"gas-khios-{MMSI}")).body).decode()
    second = bytes((await page(f"kerch-{MMSI}", FakeLake(name="Kerch"))).body).decode()
    titles = [re.search(r"<title>(.*?)</title>", b).group(1) for b in (first, second)]  # type: ignore[union-attr]
    assert titles[0] != titles[1]


async def test_unknown_ship_shows_dashes_and_never_invents() -> None:
    body = bytes((await page(f"ship-{MMSI}", FakeLake(name="", events=False))).body).decode()
    assert "Unknown vessel" in body
    assert body.count("—") >= 3


async def test_embedded_payload_parses_and_carries_the_story() -> None:
    body = bytes((await page(f"gas-khios-{MMSI}")).body).decode()
    raw = re.search(r'id="story-data">(.*?)</script>', body, re.S).group(1)  # type: ignore[union-attr]
    payload = json.loads(raw)
    assert payload["card"]["mmsi"] == MMSI
    assert len(payload["story"]["events"]) == 2
    assert payload["story"]["events"][0]["prose"].startswith("Moored")


async def test_og_png_is_a_real_png_of_the_right_size() -> None:
    response = await og_image(FakeLake(), f"gas-khios-{MMSI}")
    body = bytes(response.body)
    assert response.media_type == "image/png"
    assert body.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = int.from_bytes(body[16:20]), int.from_bytes(body[20:24])
    assert (width, height) == (ogpng.WIDTH, ogpng.HEIGHT)
    # IDAT decompresses to one filter byte + one RGB triple per pixel, per row
    start = body.index(b"IDAT") + 4
    raw = zlib.decompressobj().decompress(body[start:])
    assert len(raw) == height * (1 + width * 3)
    assert bytes(ogpng.INK) in raw  # the track actually got drawn


async def test_og_png_without_a_track_is_still_a_card() -> None:
    class Trackless(FakeLake):
        async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
            if "FROM positions" in query:
                return FakeResult([])
            return await super().query(query, parameters)

    body = bytes((await og_image(Trackless(), f"gas-khios-{MMSI}")).body)
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_is_pure_enough_to_call_twice() -> None:
    card = {
        "mmsi": MMSI,
        "identity": {k: None for k in
                     ("imo", "name", "callsign", "flag", "class", "sym", "size_m",
                      "draught_m", "destination", "eta")},
        "sentence": None,
    }
    story = {"events": [], "window_d": 30}
    assert render(card, story) == render(card, story)


def test_stamp_names_the_second_day_when_the_span_crosses_midnight() -> None:
    from app.ssr import _stamp

    start = int(datetime(2025, 8, 14, 23, 50, tzinfo=UTC).timestamp())
    assert _stamp(start, start + 1200) == "14 AUG · 23:50 – 15 AUG 00:10"
    assert _stamp(start, start + 300) == "14 AUG · 23:50 – 23:55"
