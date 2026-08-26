"""/status.json — what the lake actually is right now, in numbers we can defend.

Nothing here is cached or estimated: the message rate and the table sizes are
read from ClickHouse on the spot, the refinery's window counters and the
incident log come from the same Redis keys the pipeline writes.

Every source is optional. A dead ClickHouse or a dead Redis costs you that part
of the payload (null, or an empty list) and nothing else — a status page that
500s when something breaks is the one thing a status page must never do.
"""

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("status")

DATABASE = "ais"
POSITIONS_TABLE = "positions"
RATE_WINDOW_S = 60
INCIDENTS_KEY = "incidents"
INCIDENTS_SCAN = 100  # how far back we look for the last ws_connect
INCIDENTS_SHOWN = 10
REFINERY_STATUS_KEY = "status:refinery"
BYTES_PER_GB = 1024**3

RATE_QUERY = (
    f"SELECT count() / {RATE_WINDOW_S} FROM {POSITIONS_TABLE} "
    f"WHERE ts > now() - {RATE_WINDOW_S}"
)
PARTS_QUERY = (
    "SELECT table, sum(rows), sum(bytes_on_disk) FROM system.parts "
    f"WHERE database = '{DATABASE}' AND active GROUP BY table ORDER BY table"
)


class ClickHouseClient(Protocol):
    async def query(self, query: str) -> Any: ...


class RedisClient(Protocol):
    def hgetall(self, name: str) -> Any: ...
    def lrange(self, name: str, start: int, end: int) -> Any: ...


def _number(value: str) -> float | int | str:
    """Redis stores everything as text; give the JSON its numbers back."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


async def message_rate(ch: ClickHouseClient | None) -> float | None:
    """Rows landed in positions over the last minute, per second."""
    if ch is None:
        return None
    try:
        result = await ch.query(RATE_QUERY)
        return round(float(result.result_rows[0][0]), 2)
    except Exception as exc:
        logger.warning("status: message rate unavailable: %s: %s", type(exc).__name__, exc)
        return None


async def lake_sizes(ch: ClickHouseClient | None) -> dict[str, Any]:
    """Per-table rows and bytes. bytes_per_row on positions is the headline number."""
    empty: dict[str, Any] = {"tables": [], "total_gb": None}
    if ch is None:
        return empty
    try:
        result = await ch.query(PARTS_QUERY)
    except Exception as exc:
        logger.warning("status: lake sizes unavailable: %s: %s", type(exc).__name__, exc)
        return empty

    tables: list[dict[str, Any]] = []
    total_bytes = 0
    for table, rows, byte_count in result.result_rows:
        rows, byte_count = int(rows), int(byte_count)
        total_bytes += byte_count
        tables.append({
            "table": str(table),
            "rows": rows,
            "bytes": byte_count,
            "bytes_per_row": round(byte_count / rows, 2) if rows else None,
        })
    return {"tables": tables, "total_gb": round(total_bytes / BYTES_PER_GB, 3)}


async def refinery_counters(client: RedisClient | None) -> dict[str, Any] | None:
    """The refinery's last reporting window, as it left it in Redis."""
    if client is None:
        return None
    try:
        raw = await client.hgetall(REFINERY_STATUS_KEY)
    except Exception as exc:
        logger.warning("status: refinery counters unavailable: %s: %s", type(exc).__name__, exc)
        return None
    if not raw:
        return None
    return {str(k): _number(str(v)) for k, v in raw.items()}


async def incident_log(client: RedisClient | None) -> list[dict[str, Any]]:
    """The incident list, newest first, already decoded."""
    if client is None:
        return []
    try:
        raw = await client.lrange(INCIDENTS_KEY, 0, INCIDENTS_SCAN - 1)
    except Exception as exc:
        logger.warning("status: incidents unavailable: %s: %s", type(exc).__name__, exc)
        return []

    entries: list[dict[str, Any]] = []
    for item in raw:
        try:
            entry = json.loads(item)
        except (ValueError, TypeError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def last_connect(incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest ws_connect in the log — when the stream last came up."""
    return next((e for e in incidents if e.get("event") == "ws_connect"), None)


async def build_status(
    ch: ClickHouseClient | None,
    client: RedisClient | None,
    uptime_s: float,
) -> dict[str, Any]:
    incidents = await incident_log(client)
    return {
        "uptime_s": round(uptime_s, 1),
        "msg_per_s_1m": await message_rate(ch),
        "refinery": await refinery_counters(client),
        "lake": await lake_sizes(ch),
        "last_ws_connect": last_connect(incidents),
        "incidents": incidents[:INCIDENTS_SHOWN],
    }
