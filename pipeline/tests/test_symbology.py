"""ship_type + dimensions → sprite token. Pure, no I/O."""

import pytest

from ais_pipeline.refinery.symbology import UNKNOWN_SYM, class_of, sym


@pytest.mark.parametrize(
    "code,cls",
    [
        (30, "fishing"),
        (31, "tug"), (33, "tug"), (35, "tug"),
        (36, "pleasure"), (37, "pleasure"),
        (38, "unknown"), (39, "unknown"),  # reserved, not pleasure craft
        (40, "hsc"), (49, "hsc"),
        (50, "tug"), (52, "tug"), (59, "tug"),
        (60, "ferry"), (69, "ferry"),
        (70, "cargo"), (79, "cargo"),
        (80, "tanker"), (89, "tanker"),
        (0, "unknown"), (20, "unknown"), (90, "unknown"), (99, "unknown"), (-1, "unknown"),
    ],
)
def test_code_families(code: int, cls: str) -> None:
    assert class_of(code) == cls


@pytest.mark.parametrize(
    "loa,step",
    [(1, 1), (50, 1), (51, 2), (100, 2), (101, 3), (160, 3), (161, 4), (230, 4), (231, 5)],
)
def test_length_steps(loa: int, step: int) -> None:
    # `unknown` allows every step, so it shows the raw ladder
    assert sym(0, loa, 0) == f"unknown{step}"


def test_step_is_clamped_into_the_class_matrix() -> None:
    assert sym(30, 200, 80) == "fishing2"  # no 280 m fishing vessel exists
    assert sym(37, 20, 8) == "pleasure1"  # pleasure has exactly one step
    assert sym(80, 20, 8) == "tanker2"  # …and a tanker never goes below step 2
    assert sym(70, 200, 90) == "cargo5"


def test_missing_dimensions_take_the_smallest_allowed_step() -> None:
    assert sym(80) == "tanker2"
    assert sym(30, 0, 0) == "fishing1"
    assert sym(0) == "unknown1"  # the class ladder's floor, not the wire default
    assert UNKNOWN_SYM == "unknown2"  # …which is what a ship with NO static draws as
