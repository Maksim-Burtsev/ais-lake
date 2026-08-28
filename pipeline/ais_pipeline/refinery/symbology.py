"""AIS ship_type + dimensions → one sprite token, e.g. "tanker4".

Shape is class, size is one of five length steps (SYMBOLOGY.md §1). Both are
decided here, once, so the wire carries a single string and the browser draws it
without a lookup table of its own.

Empty cells of the class × step matrix are honest: no 280 m fishing vessel
exists, so a length is clamped into the class's own allowed range rather than
rendered as a silhouette nobody has ever seen.
"""

from bisect import bisect_left

# CMETA.steps from docs/design/"ais-lake Vessel Symbology v2.dc.html".
CLASS_STEPS: dict[str, tuple[int, ...]] = {
    "tanker": (2, 3, 4, 5),
    "cargo": (2, 3, 4, 5),
    "ferry": (1, 2, 3, 4),
    "fishing": (1, 2),
    "tug": (1, 2),
    "hsc": (1, 2, 3),
    "pleasure": (1,),
    "unknown": (1, 2, 3, 4, 5),
}

# LOA (m) upper bound of steps 1..4; anything longer is step 5.
STEP_MAX_M = (50, 100, 160, 230)

UNKNOWN_SYM = "unknown2"  # a ship we have no static data for


def class_of(ship_type: int) -> str:
    """AIS ship_type code → class key. Codes outside the families are `unknown`."""
    if ship_type == 30:
        return "fishing"
    if 31 <= ship_type <= 35:  # towing, dredging, diving, military
        return "tug"
    if ship_type in (36, 37):  # sailing, pleasure
        return "pleasure"
    if 40 <= ship_type <= 49:
        return "hsc"
    if 50 <= ship_type <= 59:  # pilot, SAR, tug, tender, law enforcement…
        return "tug"
    if 60 <= ship_type <= 69:
        return "ferry"
    if 70 <= ship_type <= 79:
        return "cargo"
    if 80 <= ship_type <= 89:
        return "tanker"
    return "unknown"


def sym(ship_type: int, dim_a: int = 0, dim_b: int = 0) -> str:
    """`{class}{step}` — the token both Redis payloads carry as their last element."""
    cls = class_of(ship_type)
    steps = CLASS_STEPS[cls]
    loa = dim_a + dim_b
    if loa <= 0:  # no dimensions reported: the smallest silhouette the class allows
        return f"{cls}{steps[0]}"
    step = bisect_left(STEP_MAX_M, loa) + 1
    return f"{cls}{min(max(step, steps[0]), steps[-1])}"
