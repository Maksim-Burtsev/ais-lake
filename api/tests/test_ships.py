"""/v1/ships/{key} against fake stores — no ClickHouse, no Redis, no network."""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ships import CardUnavailable, ShipNotFound, card_for
from tests.test_map import FakeRedis, field

MMSI = 249118000  # MID 249 = Malta
IMO = 9327545
# Relative to now, because silence is judged against the wall clock at read time
# (card_for, mirroring map.py): a fixed calendar date would age into "silent" and
# quietly rewrite every sentence assertion below.
FIX = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=8)

# vessel_latest: ts, lat, lon, sog, cog, heading, nav_status, state, sentence.
# The sentence is a fixture string on purpose — refinery/state.py::sentence_for
# would never produce it, so re-rendering it in the api fails the test below.
STORED_SENTENCE = "Waited at anchor — 14 hours"
LATEST = (FIX, 51.95, 4.05, 9.8, 87.0, 88, 0, "underway", STORED_SENTENCE)
# vessels_static: imo, name, callsign, dim_a, dim_b, draught, destination, eta
STATIC = (IMO, "Gas Khios", "9HA4321", 90, 30, 8.4, "ROTTERDAM", "08-29 06:00")

HOT = {str(MMSI): field(int(time.time()) - 8, 51.95, 4.05, 9.8, 87.0, "underway", "tanker4")}


class HotHash(FakeRedis):
    """The snapshot's fake hash, plus the single-field read row_for does."""

    async def hget(self, name: str, key: str) -> str | None:
        return (await self.hgetall(name)).get(key)


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    """Answers each of the module's three queries from its own fixture."""

    def __init__(
        self,
        latest: tuple[Any, ...] | None = None,
        static: tuple[Any, ...] | None = None,
        imo_of: int | None = None,
        mmsi: int = MMSI,
    ) -> None:
        self.latest, self.static, self.imo_of, self.mmsi = latest, static, imo_of, mmsi

    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        if "%(imo)s" in query:
            hit = self.imo_of is not None and parameters["imo"] == self.imo_of
            return FakeResult([(self.mmsi,)] if hit else [])
        row = self.latest if "vessel_latest" in query else self.static
        return FakeResult([row] if row is not None and parameters["mmsi"] == self.mmsi else [])


class DeadClickHouse:
    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        raise ConnectionError("clickhouse is down")


FULL = FakeClickHouse(LATEST, STATIC, imo_of=IMO)


async def test_the_mmsi_path_carries_class_flag_size_and_the_sentence() -> None:
    card = await card_for(FULL, HotHash(HOT), str(MMSI))
    assert card["mmsi"] == MMSI
    assert card["sentence"] == STORED_SENTENCE
    assert card["identity"] == {
        "imo": IMO,
        "name": "Gas Khios",
        "callsign": "9HA4321",
        "flag": "Malta",
        "class": "Tanker",  # from the sym token, never re-derived from ship_type
        "sym": "tanker4",
        "size_m": 120,
        "draught_m": 8.4,
        "destination": "ROTTERDAM",
        "eta": "08-29 06:00",
    }
    assert card["latest"]["state"] == "underway"
    assert card["latest"]["sog"] == 9.8


async def test_the_sentence_is_the_stored_one_verbatim() -> None:
    """F8: one source of truth. Anything the api renders itself would read like
    sentence_for's own output and fail here."""
    card = await card_for(FULL, HotHash(HOT), str(MMSI))
    assert card["sentence"] == STORED_SENTENCE
    assert "9.8 kn" not in str(card["sentence"])


def _aged(seconds: int) -> tuple[Any, ...]:
    """The same ship, last heard `seconds` ago."""
    return (datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds), *LATEST[1:])


async def test_a_ship_past_the_silence_cut_says_so_instead_of_her_last_sentence() -> None:
    """The map rings her coral at silent_after; the card said "Waited at anchor"
    about the same hull. One ship, one answer."""
    from app.limits import SILENT_AFTER_S

    assert 26 * 3600 > SILENT_AFTER_S  # 24 h in limits.json; the wording below follows it
    ch = FakeClickHouse(_aged(26 * 3600), STATIC)
    card = await card_for(ch, HotHash(HOT), str(MMSI))
    assert card["latest"]["state"] == "silent"
    assert card["sentence"] == "Went silent — 26 hours ago"
    assert card["identity"]["name"] == "Gas Khios"  # identity does not decay


async def test_just_inside_the_cut_is_still_her_own_sentence() -> None:
    from app.limits import SILENT_AFTER_S

    ch = FakeClickHouse(_aged(SILENT_AFTER_S - 60), STATIC)
    card = await card_for(ch, HotHash(HOT), str(MMSI))
    assert card["latest"]["state"] == "underway"
    assert card["sentence"] == STORED_SENTENCE


async def test_an_imo_resolves_to_the_mmsi_and_takes_the_same_path() -> None:
    by_imo = await card_for(FULL, HotHash(HOT), str(IMO))
    by_mmsi = await card_for(FULL, HotHash(HOT), str(MMSI))
    assert by_imo == by_mmsi


async def test_a_ship_with_a_fix_but_no_static_row_still_makes_a_card() -> None:
    """A type-5 may never have arrived; unknowns are null and the card draws "—"."""
    card = await card_for(FakeClickHouse(LATEST), HotHash(HOT), str(MMSI))
    assert card["sentence"] == STORED_SENTENCE
    assert card["identity"]["name"] is None
    assert card["identity"]["imo"] is None
    assert card["identity"]["size_m"] is None
    assert card["identity"]["draught_m"] is None
    assert card["identity"]["flag"] == "Malta"  # the MMSI alone knows this much
    assert card["identity"]["class"] == "Tanker"  # ... and the hot hash this much


async def test_an_unknown_ship_is_a_404() -> None:
    with pytest.raises(ShipNotFound):
        await card_for(FakeClickHouse(), HotHash(HOT), "244660000")
    with pytest.raises(ShipNotFound):
        await card_for(FULL, HotHash(HOT), "1234567")  # a well-shaped IMO we don't hold


async def test_a_malformed_key_is_the_same_404_not_a_422() -> None:
    for bad in ("banana", "", "24911800", "2491180000", "-249118000", "٢٤٩١١٨٠٠٠"):
        with pytest.raises(ShipNotFound):
            await card_for(FULL, HotHash(HOT), bad)


async def test_an_unrecognised_mid_yields_a_null_flag_never_a_guess() -> None:
    other = 999118000  # 999 is not an assigned MID
    ch = FakeClickHouse(LATEST, STATIC, mmsi=other)
    card = await card_for(ch, HotHash({}), str(other))
    assert card["identity"]["flag"] is None
    assert card["identity"]["name"] == "Gas Khios"  # the rest of the card is unharmed


async def test_no_redis_costs_the_class_and_nothing_else() -> None:
    """A missing sprite token is a missing field, not an empty sea: still a card."""
    card = await card_for(FULL, None, str(MMSI))
    assert card["identity"]["class"] is None
    assert card["identity"]["sym"] is None
    assert card["identity"]["name"] == "Gas Khios"
    assert card["sentence"] == STORED_SENTENCE


async def test_a_stale_hot_field_still_names_her_class() -> None:
    """The age cut is _rows': it keeps "live" honest for drawing and for counting.
    Identity is not that. The sym token encodes class and size off the static
    message, neither of which decays, so row_for reads it at any age. A ship 30 h
    silent gets her fix, her state and her sentence on the card — every field that
    DOES decay — so blanking the one that does not would be backwards."""
    from app.limits import MAX_VESSEL_AGE_S

    stale = int(time.time()) - MAX_VESSEL_AGE_S - 60
    old = {str(MMSI): field(stale, 51.9, 4.0, 0, 0, "moored", "tanker4")}
    card = await card_for(FULL, HotHash(old), str(MMSI))
    assert card["identity"]["class"] == "Tanker"
    assert card["identity"]["sym"] == "tanker4"


def test_a_class_key_we_have_no_word_for_says_so_once(caplog: Any) -> None:
    """CLASS_NAMES must track refinery/symbology.py and nothing enforces it. The
    day M3 adds a dredger, every one of her rows would draw "—" for a class the
    wire is carrying — so the miss is logged, once, not per row."""
    from app.ships import _UNKNOWN_CLASS_KEYS, class_name

    _UNKNOWN_CLASS_KEYS.discard("dredger")
    with caplog.at_level("WARNING", logger="ships"):
        assert [class_name("dredger3"), class_name("dredger7")] == [None, None]
    assert len([r for r in caplog.records if "dredger" in r.getMessage()]) == 1
    assert class_name("tanker4") == "Tanker" and class_name(None) is None


async def test_a_dead_clickhouse_is_not_an_empty_card() -> None:
    with pytest.raises(CardUnavailable):
        await card_for(DeadClickHouse(), HotHash(HOT), str(MMSI))
    with pytest.raises(CardUnavailable):
        await card_for(None, HotHash(HOT), str(MMSI))


async def test_heading_511_is_null_not_a_bearing() -> None:
    latest = (*LATEST[:5], 511, *LATEST[6:])
    card = await card_for(FakeClickHouse(latest, STATIC), HotHash(HOT), str(MMSI))
    assert card["latest"]["heading"] is None


async def test_route_maps_the_two_failures_to_404_and_503() -> None:
    from fastapi import HTTPException

    from app.main import runtime, ship_card

    original = (runtime.clickhouse, runtime.redis)
    try:
        runtime.clickhouse = FULL
        runtime.redis = HotHash(HOT)  # type: ignore[assignment]
        card = await ship_card(str(MMSI))
        assert card["identity"]["name"] == "Gas Khios"

        with pytest.raises(HTTPException) as err:
            await ship_card("banana")
        assert err.value.status_code == 404

        runtime.clickhouse = None
        with pytest.raises(HTTPException) as err:
            await ship_card(str(MMSI))
        assert err.value.status_code == 503
    finally:
        runtime.clickhouse, runtime.redis = original


def test_the_mid_table_covers_the_whole_itu_range_not_just_europe() -> None:
    from app.flags import MIDS, flag_for

    assert {m[0] for m in MIDS} == {"2", "3", "4", "5", "6", "7"}
    assert len(MIDS) > 250
    for mmsi, expected in (
        (244660000, "Netherlands"), (338000001, "United States"), (431000001, "Japan"),
        (503000001, "Australia"), (636000001, "Liberia"), (710000001, "Brazil"),
    ):
        assert flag_for(mmsi) == expected
    assert flag_for(2442000) is None  # not nine digits: a coast station, not a ship
    assert json.loads(json.dumps(MIDS)) == MIDS
