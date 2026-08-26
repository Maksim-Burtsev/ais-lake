"""State heuristic, the v0 status sentence, and the in-memory latest-per-ship map.

The sentence is rendered here, server-side, in plain human words — never a
score, never jargon (CLAUDE.md, copy voice).
"""

from .models import LATEST_COLUMNS, LatestRow, PositionRow
from .validate import Fix

__all__ = ["LATEST_COLUMNS", "LatestStore", "sentence_for", "state_of"]

NAV_ANCHORED = 1
NAV_MOORED = 5
UNDERWAY_MIN_SOG_KN = 0.5

STATE_UNDERWAY = "underway"
STATE_ANCHORED = "anchored"
STATE_MOORED = "moored"


def state_of(nav_status: int) -> str:
    """vessel_latest.state — nav status is all we have at v0."""
    if nav_status == NAV_ANCHORED:
        return STATE_ANCHORED
    if nav_status == NAV_MOORED:
        return STATE_MOORED
    return STATE_UNDERWAY


def sentence_for(state: str, sog: float) -> str:
    """The card's header line, v0."""
    if state == STATE_ANCHORED:
        return "At anchor"
    if state == STATE_MOORED:
        return "Moored"
    if sog >= UNDERWAY_MIN_SOG_KN:
        return f"Under way at {sog:.1f} kn"
    return "Under way"


def latest_from(row: PositionRow) -> LatestRow:
    state = state_of(row.nav_status)
    return LatestRow(
        mmsi=row.mmsi,
        ts=row.ts,
        lat=row.lat,
        lon=row.lon,
        sog=row.sog,
        cog=row.cog,
        heading=row.heading,
        nav_status=row.nav_status,
        state=state,
        sentence=sentence_for(state, row.sog),
    )


class LatestStore:
    """Newest accepted fix per MMSI: the teleport reference and the vessel_latest buffer."""

    def __init__(self) -> None:
        self._latest: dict[int, LatestRow] = {}

    def __len__(self) -> int:
        return len(self._latest)

    def last_fix(self, mmsi: int) -> Fix | None:
        row = self._latest.get(mmsi)
        return None if row is None else Fix(ts=row.ts, lat=row.lat, lon=row.lon)

    def apply(self, row: PositionRow) -> LatestRow:
        """Record a fix as the ship's latest. Out-of-order fixes never move it backwards.

        Returns the row that IS the ship's latest after this fix — an out-of-order
        fix returns the newer stored one, so sinks never publish stale state.
        """
        latest = latest_from(row)
        prev = self._latest.get(row.mmsi)
        if prev is None or row.ts >= prev.ts:
            self._latest[row.mmsi] = latest
            return latest
        return prev

    def snapshot(self) -> list[LatestRow]:
        return list(self._latest.values())
