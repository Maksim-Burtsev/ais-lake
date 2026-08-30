"""The gap classifier: what the numbers say about why a ship went quiet.

The three cells below are the three populations measured on the live lake
(coverage.py's table): a port approach heard almost every bucket, an open-sea
cell visited once in a while, and a middling one that should make the model
decline to answer rather than guess.
"""

import asyncio

from ais_pipeline.detectors.coverage import (
    CLASS_COVERAGE,
    CLASS_UNKNOWN,
    CLASS_UNUSUAL,
    build,
    load_coverage,
)

# (lat, lon, median interval s, occupancy, ships per bucket)
DENSE = (52.0, 4.0, 240.0, 0.95, 6.4)
SPARSE = (54.0, 3.0, 900.0, 0.02, 1.0)
MIDDLING = (53.0, 5.0, 450.0, 0.40, 1.4)
MODEL = build([DENSE, SPARSE, MIDDLING])


def test_silence_in_a_busy_cell_with_neighbours_still_talking_is_unusual() -> None:
    verdict = MODEL.classify(DENSE[0], DENSE[1], neighbours_online=5)
    assert verdict.classification == CLASS_UNUSUAL
    assert verdict.confidence > 0.9
    # The numbers live in meta so the expander can show them; the page gets words.
    assert verdict.stats == {"cell_interval_s": 240, "cell_occupancy": 0.95,
                             "cell_ships": 6.4, "neighbors_online": 5}


def test_a_busy_cell_that_went_quiet_all_over_is_not_her_fault() -> None:
    """Same cell, no neighbours left: the receiver went, not the ship."""
    assert MODEL.classify(*DENSE[:2], neighbours_online=0).classification != CLASS_UNUSUAL


def test_silence_in_an_empty_corner_of_the_sea_is_the_coverage() -> None:
    verdict = MODEL.classify(SPARSE[0], SPARSE[1], neighbours_online=0)
    assert verdict.classification == CLASS_COVERAGE
    assert verdict.confidence > 0.9


def test_the_middle_of_the_range_declines_to_answer() -> None:
    verdict = MODEL.classify(MIDDLING[0], MIDDLING[1], neighbours_online=3)
    assert verdict.classification == CLASS_UNKNOWN
    assert verdict.confidence == 0.0  # no claim, no confidence in one


def test_a_point_far_from_every_cell_we_know_is_unknown() -> None:
    verdict = MODEL.classify(10.0, 10.0, neighbours_online=4)
    assert (verdict.classification, verdict.confidence) == (CLASS_UNKNOWN, 0.0)
    assert verdict.stats == {"neighbors_online": 4}


def test_a_point_snaps_to_the_nearest_centre_and_only_a_near_one() -> None:
    # 0.02° off the dense centre — inside the hex, same stats.
    assert MODEL.cell_of(52.02, 4.01) == DENSE[:2]
    # 0.5° away is nobody's cell, even though a centre exists in that direction.
    assert MODEL.cell_of(52.5, 4.0) is None
    # …including across a grid-bucket edge, which a 3×3 search has to cover.
    assert MODEL.cell_of(51.98, 3.99) == DENSE[:2]


def test_the_loader_indexes_whatever_the_lake_hands_back() -> None:
    seen: list[str] = []

    async def fetch(query: str) -> list[tuple[float, ...]]:
        seen.append(query)
        return [DENSE, SPARSE]

    model = asyncio.run(load_coverage(fetch))
    assert model.stats_at(*DENSE[:2]) is not None
    assert "density_h3" in seen[0] and "h3ToParent" in seen[0]
