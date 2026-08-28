"""MMSI -> flag state, through the ITU MID table.

mid.json sits next to this module rather than at the repo root: unlike
limits.json and regions.json it is not a knob anyone tunes per deployment, it is
public reference data the image needs, so it ships inside the ./api build
context instead of being mounted by compose.

A MID we do not recognise returns None and the card writes "unknown flag" —
never a guessed country. MMSIs that carry their MID somewhere other than the
first three digits (AtoN 99MIDxxxx, SAR aircraft 111MIDxxx, craft associated
with a parent ship 98MIDxxxx) therefore come back flagless, which is honest:
those are not the ships this card is about.
"""

import json
from pathlib import Path

MIDS: dict[str, str] = json.loads((Path(__file__).parent / "mid.json").read_text())


def flag_for(mmsi: int) -> str | None:
    """The country of an MMSI's first three digits, or None."""
    return MIDS.get(str(mmsi)[:3]) if 100_000_000 <= mmsi <= 999_999_999 else None
