"""In-memory dedup: the same fix arrives more than once (replays, overlapping feeds).

An LRU bounded by dedup_max_keys with a dedup_ttl_s time-to-live; the clock is
injected so tests never sleep.
"""

from collections import OrderedDict
from collections.abc import Callable

from .models import PositionRow

COORD_PRECISION = 4  # ~11 m — finer than any AIS jitter we care to keep

DedupKey = tuple[int, int, float, float]


def key_of(row: PositionRow) -> DedupKey:
    return (
        row.mmsi,
        int(row.ts.timestamp()),
        round(row.lat, COORD_PRECISION),
        round(row.lon, COORD_PRECISION),
    )


class Dedup:
    def __init__(
        self,
        ttl_s: float,
        max_keys: int,
        clock: Callable[[], float],
    ) -> None:
        self._ttl_s = ttl_s
        self._max_keys = max_keys
        self._clock = clock
        self._seen: OrderedDict[DedupKey, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._seen)

    def is_duplicate(self, row: PositionRow) -> bool:
        """True when this exact fix was seen within the TTL. Records it either way."""
        now = self._clock()
        key = key_of(row)
        self._evict(now)
        seen_at = self._seen.get(key)
        if seen_at is not None and now - seen_at < self._ttl_s:
            return True  # not refreshed: a key lives ttl_s from its first sighting, no longer
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self._max_keys:
            self._seen.popitem(last=False)
        return False

    def _evict(self, now: float) -> None:
        # oldest first: insertion order is time order, so stop at the first live key
        while self._seen:
            key, seen_at = next(iter(self._seen.items()))
            if now - seen_at < self._ttl_s:
                return
            del self._seen[key]
