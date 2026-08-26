"""Exponential backoff with full jitter for reconnect loops."""

import random
from collections.abc import Callable


class Backoff:
    """delay = uniform(0, min(cap, base * 2**attempt)); reset() after a stable connection."""

    def __init__(
        self,
        base_s: float = 1.0,
        cap_s: float = 60.0,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._base = base_s
        self._cap = cap_s
        self._rng = rng
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt

    def next_delay(self) -> float:
        ceiling = min(self._cap, self._base * float(2**self._attempt))
        self._attempt += 1
        return float(self._rng()) * ceiling

    def reset(self) -> None:
        self._attempt = 0
