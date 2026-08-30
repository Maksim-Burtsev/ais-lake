"""F17 — the slice as a file: the name on it, the window it may cover, its rows."""

import json
import time
from typing import Any

import pytest

from app.download import download
from app.limits import DOWNLOAD_WINDOW_D
from app.story import DAY_S
from tests.test_ships import HOT, LATEST, STATIC, HotHash
from tests.test_ships import FakeClickHouse as ShipCH
from tests.test_track import GAP, MMSI, NOW, POSITIONS, FakeResult


class FakeLake(ShipCH):
    """The ship's two rows and her positions from one fake, dispatched by table."""

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        if "events" in query:
            return FakeResult([GAP])
        if "positions" in query:
            return FakeResult(POSITIONS)
        return await super().query(query, parameters)


async def get(fmt: str, **kwargs: Any) -> tuple[bytes, str, str]:
    return await download(FakeLake(LATEST, STATIC), HotHash(HOT), str(MMSI), fmt, **kwargs)


@pytest.mark.asyncio
async def test_filename_is_name_imo_from_to() -> None:
    _, _, name = await get("csv")
    # F17 verbatim: {name}-{imo}-{from}-{to}. Dates YYYYMMDD UTC (our choice).
    stem, _, ext = name.rpartition(".")
    head, _, dates = stem.partition("gas-khios-9327545-")
    assert head == "" and ext == "csv"
    assert len(dates) == 17 and dates[8] == "-"  # YYYYMMDD-YYYYMMDD
    _, _, geo = await get("geojson")
    assert geo == f"{stem}.geojson"


@pytest.mark.asyncio
async def test_window_is_clamped_to_the_download_limit() -> None:
    body, _, _ = await get("geojson", from_=int(NOW) - 400 * DAY_S)
    props = json.loads(body)["properties"]
    assert props["window_d"] == DOWNLOAD_WINDOW_D
    assert props["to"] - props["from"] <= DOWNLOAD_WINDOW_D * DAY_S + 1


@pytest.mark.asyncio
async def test_csv_has_the_header_and_one_row_per_point() -> None:
    body, media, _ = await get("csv")
    lines = body.decode().strip().split("\n")
    assert media.startswith("text/csv")
    assert lines[0] == "ts,lat,lon"
    assert len(lines) > 1
    ts, lat, lon = lines[1].split(",")
    assert ts.endswith("Z") and float(lat) == 51.0 and float(lon) == 4.0
    assert int(time.strftime("%Y")) >= 2024  # sanity: the stamp is a real UTC one


@pytest.mark.asyncio
async def test_geojson_is_the_track_feature() -> None:
    body, media, _ = await get("geojson")
    feature = json.loads(body)
    assert media == "application/geo+json"
    assert feature["type"] == "Feature" and feature["geometry"]["type"] == "LineString"
    assert len(feature["geometry"]["coordinates"]) == len(feature["properties"]["times"])
