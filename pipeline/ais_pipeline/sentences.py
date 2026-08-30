"""The status sentence: what a ship is doing, in words a person would use.

One sentence, rendered once, server-side. "Waited at anchor — 14 hours" is the
voice (CLAUDE.md): no scores, no confidence, no nav_status codes, no
"dwell 50400 s". A reader who has never heard of AIS must be able to read it
aloud.

The em dash is a contract, not decoration. Everything before " — " is what she
is doing; everything after is how long she has been doing it. ShipCard.tsx
splits on exactly that to set the leading clause bold, so a sentence must carry
at most one " — " and must never use the dash for anything but the duration.

Time is the stream's own: `now_ts` is passed in, never read off the wall clock,
because a replay of last Tuesday must say what it said last Tuesday.

Pure, and deliberately dependency-free — plain strings and numbers in, one
string out, so both the refinery and the tests call it without building a
ShipState.
"""

__all__ = ["MOTION_ANCHORED", "MOTION_MOORED", "MOTION_STOPPED", "MOTION_UNDERWAY",
           "humanize_duration", "sentence_for"]

# Duplicated from detectors.machine on purpose: importing the detector into a
# pure renderer would drag config, Kafka and the parser in behind it.
MOTION_UNDERWAY = "underway"
MOTION_STOPPED = "stopped"
MOTION_ANCHORED = "anchored"
MOTION_MOORED = "moored"

UNDERWAY_MIN_SOG_KN = 0.5

HOUR_S = 3600
DAY_S = 86_400
DAYS_AFTER_S = 48 * HOUR_S  # under two days we still count in hours


def humanize_duration(seconds: float) -> str:
    """"40 minutes", "1 hour", "14 hours", "2 days" — a rounded, plural-correct span.

    TWIN: api renders "Went silent — 26 hours ago" at read time and cannot import
    pipeline, so it carries a copy of this function. Change one, change both.
    """
    seconds = max(0.0, seconds)
    if seconds < HOUR_S:
        n, unit = round(seconds / 60), "minute"
    elif seconds < DAYS_AFTER_S:
        n, unit = round(seconds / HOUR_S), "hour"
    else:
        n, unit = round(seconds / DAY_S), "day"
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def sentence_for(
    motion: str,
    *,
    sog: float = 0.0,
    now_ts: float | None = None,
    still_since: float | None = None,
    port_name: str | None = None,
    in_anchorage: bool = False,
) -> str:
    """The card's header line for one ship. See the module docstring for the rules."""
    if motion == MOTION_MOORED:
        head = f"Moored in {port_name}" if port_name else "Moored"
    elif motion == MOTION_ANCHORED:
        head = f"Waiting off {port_name}" if in_anchorage and port_name else "At anchor"
    elif motion == MOTION_STOPPED:
        return "Stopped"  # too short to have a length worth stating
    elif sog >= UNDERWAY_MIN_SOG_KN:
        return f"Under way at {sog:.1f} kn"  # v0's formatting, .0 and all
    else:
        return "Under way"

    if still_since is None or now_ts is None:
        return head  # a seeded ship: we see that she is stopped, never for how long
    return f"{head} — {humanize_duration(now_ts - still_since)}"
