"""GET /v1/ships/{id}/story — one ship's voyage as ordered prose (S2, F16).

The events are already in the lake; this module only puts them into words, once,
server-side, exactly as the ship card does with its sentence (F8's "one source of
truth"). The client renders strings it is handed and never assembles a sentence
of its own, so the voice lives in ONE place: prose_for() below.

Voice rules (CLAUDE.md): plain human sentences, one " — " that separates what she
did from how long it took, and no jargon on the page. The gap detector's
classification, confidence and cell statistics never reach the prose — an
"unusual" gap gets a `flag` object beside its sentence, and the numbers live in
there for the expander to show. A reader who has never heard of AIS reads the
timeline aloud without meeting a single score.

The window is clamped here and not trusted from the query string: F16 says the
server enforces it, so ?from=2019 comes back as the last 30 days rather than as
an error, and `window_d` on the wire says what was actually read.
"""

import json
import logging
import time
from typing import Any

from .limits import STORY_LIMIT_LINE, STORY_WINDOW_D
from .ports import Pool
from .ships import (
    MMSI_FOR_IMO_QUERY,
    ClickHouseClient,
    ShipNotFound,
    _epoch,
    _resolve,
    humanize_duration,
)

logger = logging.getLogger("story")

DAY_S = 86_400

EVENTS_QUERY = """
SELECT event_id, kind, t_start, t_end, port, toString(meta)
FROM events
WHERE mmsi = %(mmsi)s AND t_start >= toDateTime(%(from)s) AND t_start < toDateTime(%(to)s)
ORDER BY t_start
"""
# meta is read whole and parsed in python rather than as toString(meta.x) per
# field: the story wants every key of five different shapes, and one JSON column
# beats nine casts — the aggregate reads in ports.py cast because they must.
PORT_NAMES_QUERY = "SELECT locode, name FROM ports WHERE locode = ANY($1)"


class StoryUnavailable(Exception):
    """ClickHouse is gone, so there is no story to tell — a 503."""


def clamp_window(
    from_: Any, to: Any, now: float, window_d: int = STORY_WINDOW_D
) -> tuple[int, int]:
    """The asked-for window, pulled back inside what this tier may read (F16).

    Junk or missing bounds mean "the whole window ending now", which is what a
    reader who typed no dates wants anyway.
    """
    def epoch(value: Any, fallback: int) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return fallback

    to_ts = epoch(to, int(now))
    from_ts = epoch(from_, to_ts - window_d * DAY_S)
    floor = int(now) - window_d * DAY_S
    from_ts = max(from_ts, floor)
    return from_ts, max(to_ts, from_ts)


async def resolve_mmsi(ch: ClickHouseClient | None, key: str) -> int:
    """The MMSI behind an MMSI or an IMO — the card's resolution, reused so the
    story 404s on exactly the keys the card 404s on."""
    kind, value = _resolve(key)
    if ch is None:
        raise StoryUnavailable("no clickhouse connection")
    if kind == "mmsi":
        return value
    rows = (await _query(ch, MMSI_FOR_IMO_QUERY, {"imo": value})).result_rows
    if not rows:
        raise ShipNotFound(key)
    return int(rows[0][0])


async def _query(ch: ClickHouseClient, query: str, params: dict[str, Any]) -> Any:
    try:
        return await ch.query(query, parameters=params)
    except Exception as exc:
        logger.warning("story: query failed: %s: %s", type(exc).__name__, exc)
        raise StoryUnavailable(str(exc)) from exc


async def port_names(pool: Pool | None, locodes: list[str]) -> dict[str, str]:
    """One round trip for every port the story mentions. A Postgres that cannot
    answer costs the names, not the story: "Moored — 2 days" is still true."""
    if pool is None or not locodes:
        return {}
    try:
        rows = await pool.fetch(PORT_NAMES_QUERY, locodes)
    except Exception as exc:
        logger.warning("story: port names unavailable: %s: %s", type(exc).__name__, exc)
        return {}
    return {str(r["locode"]): str(r["name"]) for r in rows}


def _duration(meta: dict[str, Any], t_start: int, t_end: int | None) -> str | None:
    """The detector's own duration when it wrote one, else the span on the row."""
    seconds = meta.get("duration_s")
    if seconds is None and t_end is not None:
        seconds = t_end - t_start
    try:
        return humanize_duration(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _draught(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _tail(head: str, duration: str | None) -> str:
    return f"{head} — {duration}" if duration else head


def prose_for(kind: str, name: str | None, meta: dict[str, Any], dur: str | None) -> str:
    """The whole voice of the timeline, in one table's worth of branches."""
    if kind == "port_call":
        return _tail(f"Moored in {name}" if name else "Moored", dur)
    if kind == "departure":
        return f"Left {name}" if name else "Under way again"
    if kind == "anchorage":
        return _tail(f"Waited off {name}" if name else "Waited at anchor", dur)
    if kind == "gap":
        return _tail("Went silent", dur)
    if kind == "load_delta":
        before, after = _draught(meta.get("from")), _draught(meta.get("to"))
        if before is None or after is None:
            return "Draught changed"
        if after >= before:
            return f"Loaded — draught {after} m, up from {before}"
        return f"Discharged — draught {after} m, down from {before}"
    return kind.replace("_", " ").capitalize()  # a kind added to the enum, not yet to the voice


# The numbers behind a gap: shown in the opened-silence view, never in the sentence.
FLAG_KEYS = (
    "confidence",
    "cell_interval_s",
    "cell_occupancy",
    "cell_ships",
    "neighbors_online",
)
NUMBER_KEYS = ("classification", *FLAG_KEYS)


def _flag(kind: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    if kind != "gap" or meta.get("classification") != "unusual":
        return None
    flag: dict[str, Any] = {"label": "Unusual for this area"}
    flag.update({k: meta[k] for k in FLAG_KEYS if k in meta})
    return flag


def _numbers(kind: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    """Every gap's evidence, flagged or not (F13): the gap view IS the expander,
    so an ordinary silence must be able to show why it is ordinary."""
    if kind != "gap":
        return None
    return {k: meta[k] for k in NUMBER_KEYS if k in meta}


async def story_payload(
    ch: ClickHouseClient | None,
    pool: Pool | None,
    key: str,
    from_: Any = None,
    to: Any = None,
    now: float | None = None,
    window_d: int = STORY_WINDOW_D,
) -> dict[str, Any]:
    mmsi = await resolve_mmsi(ch, key)
    assert ch is not None  # resolve_mmsi raised otherwise
    from_ts, to_ts = clamp_window(from_, to, now if now is not None else time.time(), window_d)

    rows = (
        await _query(ch, EVENTS_QUERY, {"mmsi": mmsi, "from": from_ts, "to": to_ts})
    ).result_rows
    parsed = [(row, json.loads(row[5]) if row[5] else {}) for row in rows]
    names = await port_names(pool, sorted({str(r[4]) for r, _ in parsed if r[4]}))

    events = []
    for row, meta in parsed:
        event_id, kind, t_start, t_end, port = row[0], str(row[1]), row[2], row[3], str(row[4])
        start, end = _epoch(t_start), None if t_end is None else _epoch(t_end)
        name = names.get(port)
        events.append(
            {
                "event_id": str(event_id),
                "kind": kind,
                "t_start": start,
                "t_end": end,
                "prose": prose_for(kind, name, meta, _duration(meta, start, end)),
                "port": {"locode": port, "name": name} if port else None,
                **({"flag": flag} if (flag := _flag(kind, meta)) else {}),
                **({"numbers": numbers} if (numbers := _numbers(kind, meta)) is not None else {}),
            }
        )

    return {
        "mmsi": mmsi,
        "from": from_ts,
        "to": to_ts,
        "window_d": window_d,
        "limit_line": STORY_LIMIT_LINE,
        "events": events,
        "track": f"/v1/ships/{mmsi}/track?from={from_ts}&to={to_ts}",
    }
