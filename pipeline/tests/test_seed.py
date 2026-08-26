"""DMA CSV mapping and the seed replay — pure, offline, nothing is downloaded."""

import csv
import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ais_pipeline.config import PIPELINE_ROOT, Settings
from ais_pipeline.refinery.models import HEADING_NA
from ais_pipeline.refinery.service import Refinery
from ais_pipeline.seed.dma import nav_status_code, parse_row, ship_type_code
from ais_pipeline.seed.download import candidate_days, dump_url
from ais_pipeline.seed.service import StaticSeen, iter_rows, replay
from tests.test_refinery_service import FakeLake, FakeLive

HEADER = (
    "# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,"
    "Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,"
    "Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D"
).split(",")


def dma_row(
    ts: str = "03/09/2026 08:15:22",
    mobile: str = "Class A",
    mmsi: str = "244660000",
    lat: str = "52.0",
    lon: str = "4.0",
    nav: str = "Under way using engine",
    sog: str = "8.7",
    cog: str = "91.4",
    heading: str = "90",
    imo: str = "9123456",
    callsign: str = "PBEE",
    name: str = "EENDRACHT",
    ship_type: str = "Cargo",
    draught: str = "6.4",
    destination: str = "ROTTERDAM",
    eta: str = "04/09/2026 18:30:00",
) -> list[str]:
    row = [""] * len(HEADER)
    row[0], row[1], row[2] = ts, mobile, mmsi
    row[3], row[4], row[5] = lat, lon, nav
    row[6], row[7], row[8], row[9] = "0.0", sog, cog, heading
    row[10], row[11], row[12], row[13] = imo, callsign, name, ship_type
    row[14], row[15], row[16], row[17] = "Undefined", "20", "120", "GPS"
    row[18], row[19], row[20], row[21] = draught, destination, eta, "AIS"
    row[22], row[23], row[24], row[25] = "80", "40", "10", "10"
    return row


def test_row_maps_to_position_and_static() -> None:
    parsed = parse_row(dma_row())
    assert parsed is not None and parsed.position is not None and parsed.static is not None
    pos, static = parsed.position, parsed.static
    assert pos.ts == datetime(2026, 9, 3, 8, 15, 22, tzinfo=UTC)
    assert (pos.mmsi, pos.lat, pos.lon, pos.sog, pos.cog) == (244660000, 52.0, 4.0, 8.7, 91.4)
    assert (pos.heading, pos.nav_status, pos.msg_type, pos.src) == (90, 0, 1, "dma")
    assert (static.name, static.imo, static.callsign) == ("EENDRACHT", 9123456, "PBEE")
    assert (static.ship_type, static.draught, static.destination) == (70, 6.4, "ROTTERDAM")
    assert (static.dim_a, static.dim_b, static.dim_c, static.dim_d) == (80, 40, 10, 10)
    assert static.eta == "09-04 18:30"


@pytest.mark.parametrize(
    "text,code",
    [("Under way using engine", 0), ("At anchor", 1), ("Moored", 5), ("Engaged in fishing", 7),
     ("Unknown value", 15), ("something the dump invented", 15), ("", 15)],
)
def test_nav_status_text_to_ais_code(text: str, code: int) -> None:
    assert nav_status_code(text) == code


@pytest.mark.parametrize(
    "text,code", [("Cargo", 70), ("Tanker", 80), ("Passenger", 60), ("Undefined", 0), ("", 0)]
)
def test_ship_type_text_to_ais_code(text: str, code: int) -> None:
    assert ship_type_code(text) == code


def test_class_b_kept_other_mobile_types_skipped() -> None:
    assert parse_row(dma_row(mobile="Class B")) is not None
    for mobile in ("Base Station", "AtoN", "SAR Airborne", ""):
        assert parse_row(dma_row(mobile=mobile)) is None


def test_unusable_rows_are_skipped() -> None:
    assert parse_row(HEADER) is None                       # the header line itself
    assert parse_row(dma_row(ts="not a timestamp")) is None
    assert parse_row(dma_row(mmsi="")) is None
    assert parse_row(["too", "short"]) is None


def test_missing_identity_yields_no_static() -> None:
    parsed = parse_row(dma_row(name="", imo="Unknown"))
    assert parsed is not None and parsed.static is None
    assert parsed.position is not None


def test_blank_and_bad_numbers_fall_back() -> None:
    parsed = parse_row(dma_row(heading="", sog="", draught="Unknown", eta=""))
    assert parsed is not None and parsed.position is not None and parsed.static is not None
    assert parsed.position.heading == HEADING_NA
    assert parsed.position.sog == 0.0
    assert parsed.static.draught == 0.0 and parsed.static.eta == ""


def test_sentinel_coordinates_are_rejected_downstream() -> None:
    """91/181 mean "no fix" — the seed passes them on, the refinery's bbox check kills them."""
    parsed = parse_row(dma_row(lat="91.0", lon="181.0"))
    assert parsed is not None and parsed.position is not None
    assert (parsed.position.lat, parsed.position.lon) == (91.0, 181.0)

    refinery = Refinery(Settings(_env_file=None), clock=lambda: 0.0)
    refinery.handle_parsed(parsed)
    assert refinery.counters.rejected_bbox == 1
    assert refinery.pending_rows == 0


def test_static_emitted_once_until_it_changes() -> None:
    seen = StaticSeen()
    first = parse_row(dma_row())
    same = parse_row(dma_row(ts="03/09/2026 08:15:32"))
    changed = parse_row(dma_row(ts="03/09/2026 08:15:42", destination="ANTWERP"))
    assert first is not None and same is not None and changed is not None
    assert first.static is not None and same.static is not None and changed.static is not None
    assert seen.changed(first.static) is True
    assert seen.changed(same.static) is False   # identical content, later ts
    assert seen.changed(changed.static) is True


def test_candidate_days_start_two_days_back_newest_first() -> None:
    days = candidate_days(date(2026, 9, 10), 3, 2)
    assert days[0] == date(2026, 9, 8)          # newest candidate = today-2
    assert len(days) == 5                        # requested + lookback extra
    assert days == sorted(days, reverse=True)    # newest first
    assert dump_url("http://aisdata.ais.dk/", days[0]) == (
        "http://aisdata.ais.dk/aisdk-2026-09-08.zip"
    )


def write_dump(path: Path, rows: list[list[str]]) -> Path:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    zip_path = path / "aisdk-2026-09-03.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("aisdk-2026-09-03.csv", buffer.getvalue())
    return zip_path


def test_iter_rows_streams_with_stride(tmp_path: Path) -> None:
    zip_path = write_dump(tmp_path, [dma_row(mmsi=str(244660000 + i)) for i in range(5)])
    assert len(list(iter_rows(zip_path, 1))) == 6            # header + 5
    assert len(list(iter_rows(zip_path, 2))) == 3            # header, row 1, row 3


async def test_replay_writes_positions_statics_and_latest(tmp_path: Path) -> None:
    rows = [
        dma_row(),                                              # good
        dma_row(ts="03/09/2026 08:15:32", lat="52.001"),        # good, same ship moved
        dma_row(ts="03/09/2026 08:15:42", mobile="Base Station"),  # skipped
        dma_row(ts="03/09/2026 08:15:52", lat="91.0", lon="181.0"),  # bbox reject
    ]
    zip_path = write_dump(tmp_path, rows)
    settings = Settings(_env_file=None, seed_stride=1)
    refinery = Refinery(settings, clock=lambda: 0.0)
    lake, live = FakeLake(), FakeLive()

    read, out = await replay([zip_path], refinery, lake, live, settings)

    assert (read, out) == (5, 2)  # header + 4 rows read; two survive
    assert len(lake.positions) == 2
    assert [r.name for r in lake.statics] == ["EENDRACHT"]  # identity emitted once
    assert [r.mmsi for r in lake.latest] == [244660000]
    assert [r.mmsi for r in live.published] == [244660000]
    assert refinery.counters.rejected_bbox == 1


def test_seed_cache_dir_resolves_against_the_pipeline_root() -> None:
    settings = Settings(_env_file=None)
    assert settings.seed_cache_path() == (PIPELINE_ROOT.parent / "ops/seed/cache").resolve()
    absolute = Settings(_env_file=None, seed_cache_dir="/var/tmp/seed")
    assert str(absolute.seed_cache_path()) == "/var/tmp/seed"
