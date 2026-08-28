"""/v1/search against fake stores — no ClickHouse, no Redis, no network.

The fake ClickHouse does not parse SQL: it holds a fleet and re-implements the
module's WHERE and ORDER BY over it, so a test can assert what the real query is
asked to produce (tiers, distance, live-before-archive) without a server.
"""

import time
from datetime import datetime
from typing import Any

from app.search import NGRAM_MAX, RESULT_CAP, search_payload
from tests.test_map import field
from tests.test_ships import HotHash

FRESH = datetime.fromtimestamp(time.time() - 600)
OLD = datetime.fromtimestamp(time.time() - 9 * 86400)
EPOCH = datetime.fromtimestamp(0)

MMSI = 249118000  # MID 249 = Malta
IMO = 9327545


class Ship:
    """One vessels_static row plus its vessel_latest row, if she has one."""

    def __init__(
        self,
        mmsi: int,
        name: str,
        imo: int = 0,
        state: str = "underway",
        sentence: str = "Under way at 9.8 kn",
        sog: float = 9.8,
        last_ts: datetime = FRESH,
    ) -> None:
        self.mmsi, self.name, self.imo = mmsi, name, imo
        self.state, self.sentence, self.sog, self.last_ts = state, sentence, sog, last_ts


def ngram(name: str, q: str) -> float:
    """A stand-in for ClickHouse's ngramDistanceCaseInsensitiveUTF8: 0 identical,
    1 nothing in common. Same direction, which is the property under test."""
    a = {name.lower()[i : i + 3] for i in range(max(1, len(name) - 2))}
    b = {q.lower()[i : i + 3] for i in range(max(1, len(q) - 2))}
    return 1.0 if not b else 1.0 - len(a & b) / len(b)


class FakeClickHouse:
    def __init__(self, fleet: list[Ship], seen_30d: int = 41880) -> None:
        self.fleet, self.seen_30d = fleet, seen_30d

    async def query(self, query: str, parameters: dict[str, Any]) -> Any:
        if "INTERVAL 30 DAY" in query:
            return FakeResult([(self.seen_30d,)])
        q = str(parameters["q"])
        if "s.mmsi = " in query:
            rows = [s for s in self.fleet if s.mmsi == parameters["id"]]
        elif "s.imo = " in query:
            rows = [s for s in self.fleet if s.imo and s.imo == parameters["id"]]
        else:
            rows = [s for s in self.fleet if s.name]
        now = time.time()

        def tier(s: Ship) -> int:
            low, ql = s.name.lower(), q.lower()
            return 0 if low.startswith(ql) else 1 if ql in low else 2

        def key(s: Ship) -> tuple[Any, ...]:
            t = tier(s)
            live = s.last_ts.timestamp() > now - parameters["age"]
            return (t, 0.0 if t < 2 else round(ngram(s.name, q), 1), not live, s.name)

        return FakeResult(
            [
                (
                    s.mmsi, s.name, s.state, s.sentence, s.sog, 87.0, 55.0, 3.0, s.last_ts,
                    tier(s), ngram(s.name, q), s.last_ts.timestamp() > now - parameters["age"],
                )
                for s in sorted(rows, key=key)[: parameters["cap"]]
            ]
        )


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class DeadClickHouse:
    async def query(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        raise ConnectionError("clickhouse is down")


# One ship inside the North Sea launch box, so the region count is a real 1.
HOT = {str(MMSI): field(int(time.time()) - 8, 55.0, 3.0, 9.8, 87.0, "underway", "tanker4")}
GAS_KHIOS = Ship(MMSI, "GAS KHIOS", imo=IMO)
FLEET = [
    GAS_KHIOS,
    Ship(249118001, "GAS KALYMNOS", state="anchored", sentence="At anchor", sog=0.0),
    Ship(249118002, "NORDIC BREEZE"),
]


async def test_a_nine_digit_query_is_an_mmsi_lookup() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), str(MMSI))
    assert [s["mmsi"] for s in body["ships"]] == [MMSI]
    ship = body["ships"][0]
    assert ship["name"] == "GAS KHIOS"
    assert ship["flag"] == "Malta"
    assert ship["class"] == "Tanker"  # from the sym token, never from ship_type
    assert ship["sentence"] == "Under way at 9.8 kn"  # read, never re-rendered


async def test_a_seven_digit_query_is_an_imo_lookup() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), str(IMO))
    assert [s["mmsi"] for s in body["ships"]] == [MMSI]


async def test_a_name_matches_fuzzily_and_the_prefix_hit_leads() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "gas k")
    names = [s["name"] for s in body["ships"]]
    assert names[0] == "GAS KALYMNOS"  # prefix tier, alphabetical inside it
    assert "GAS KHIOS" in names
    assert "NORDIC BREEZE" not in names  # nothing in common, past the threshold


async def test_a_prefix_hit_outranks_a_loose_ngram_one() -> None:
    loose = Ship(249118003, "KHIOS TRADER")
    body = await search_payload(FakeClickHouse([loose, GAS_KHIOS]), HotHash(HOT), "gas khio")
    assert [s["name"] for s in body["ships"]][0] == "GAS KHIOS"


async def test_a_live_ship_outranks_the_same_name_in_the_archive() -> None:
    archived = Ship(249118004, "GAS KHIOS II", last_ts=OLD)
    live = Ship(249118005, "GAS KHIOS III")
    body = await search_payload(FakeClickHouse([archived, live]), HotHash(HOT), "gas khios")
    assert [s["name"] for s in body["ships"]] == ["GAS KHIOS III", "GAS KHIOS II"]


async def test_a_ship_with_no_fix_still_lists_with_a_null_sentence() -> None:
    ghost = Ship(249118006, "GAS PHANTOM", state="", sentence="", sog=0.0, last_ts=EPOCH)
    body = await search_payload(FakeClickHouse([ghost]), HotHash({}), "gas phantom")
    ship = body["ships"][0]
    assert ship["name"] == "GAS PHANTOM"
    assert (ship["sentence"], ship["state"], ship["sog"], ship["age_h"]) == (None,) * 4
    assert (ship["lat"], ship["lon"]) == (None, None)  # nowhere for ⏎ to fly


async def test_a_sea_matches_by_substring_and_carries_a_live_count() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "north")
    assert [s["slug"] for s in body["seas"]] == ["north-sea"]
    assert body["seas"][0]["count"] == 1
    assert body["seas"][0]["bbox"][0] == -6.5


async def test_a_miss_carries_the_nearest_name_and_the_real_numbers() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "gas khiozz qqq www")
    assert body["ships"] == []
    assert body["near"] is not None and body["near"]["name"] == "GAS KHIOS"
    assert body["searched"] == {"live": 1, "seen_30d": 41880}


async def test_a_miss_with_nothing_in_common_offers_no_nearest() -> None:
    body = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "qqqzzzxxx")
    assert body["ships"] == [] and body["near"] is None


async def test_ports_are_always_empty_because_there_is_no_ports_table() -> None:
    for q in ("rotterdam", "gas k", str(MMSI)):
        assert (await search_payload(FakeClickHouse(FLEET), HotHash(HOT), q))["ports"] == []


async def test_a_blank_or_absurd_query_is_answered_not_a_500() -> None:
    blank = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "   ")
    assert blank["ships"] == [] and blank["seas"] == [] and blank["near"] is None

    long = await search_payload(FakeClickHouse(FLEET), HotHash(HOT), "z" * 5000)
    assert len(long["q"]) <= 64 and long["ships"] == []


async def test_a_dead_lake_still_answers_with_the_seas_it_can_count() -> None:
    body = await search_payload(DeadClickHouse(), HotHash(HOT), "north")
    assert body["ships"] == [] and body["searched"]["seen_30d"] is None
    assert [s["slug"] for s in body["seas"]] == ["north-sea"]
    assert body["searched"]["live"] == 1


async def test_the_result_cap_holds() -> None:
    fleet = [Ship(249118100 + i, f"GAS KHIOS {i}") for i in range(RESULT_CAP + 6)]
    body = await search_payload(FakeClickHouse(fleet), HotHash(HOT), "gas")
    assert len(body["ships"]) == RESULT_CAP


def test_the_threshold_reads_the_right_way_round() -> None:
    """ngram distance is 0 for identical and 1 for nothing in common — a cut that
    kept the LARGEST distances would silently return the worst matches."""
    assert ngram("GAS KHIOS", "gas khios") < NGRAM_MAX < ngram("GAS KHIOS", "qqqzzz")
