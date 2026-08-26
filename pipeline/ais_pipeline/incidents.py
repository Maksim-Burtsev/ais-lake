"""The incident log: a short, honest list of what went wrong, in Redis.

One capped list, newest first — LPUSH + LTRIM. /status.json reads the head of
it, so the entries stay compact and machine-parseable, exactly like the kv()
log lines they mirror.

Recording an incident is best effort by design: a service must never fall over
because Redis blinked.
"""

import json
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("incidents")

INCIDENTS_KEY = "incidents"
INCIDENTS_MAX = 100  # keep the last N; /status shows a handful of them


class IncidentSink(Protocol):
    """The slice of redis.asyncio.Redis we use — keeps this testable without a server."""

    # not declared async: redis-py types these as returning an awaitable, and a
    # sync-looking signature matches both that and a plain test double.
    def lpush(self, name: str, *values: Any) -> Any: ...
    def ltrim(self, name: str, start: int, end: int) -> Any: ...


async def record_incident(redis: IncidentSink | None, event: str, **fields: Any) -> None:
    """Push one incident onto the capped list. Never raises."""
    if redis is None:
        return
    entry = json.dumps({"ts": int(time.time()), "event": event, **fields},
                       separators=(",", ":"), default=str)
    try:
        await redis.lpush(INCIDENTS_KEY, entry)
        await redis.ltrim(INCIDENTS_KEY, 0, INCIDENTS_MAX - 1)
    except Exception as exc:  # Redis down must not take the service with it
        logger.debug("incident not recorded: %s: %s", type(exc).__name__, exc)
