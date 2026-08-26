"""Validation: MMSI range, bbox, teleports. Pure — thresholds come from Settings.

A rejected row never reaches the lake; every rejection is counted by reason so
/status can report honestly what the refinery threw away.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ..config import BBox
from .models import PositionRow

EARTH_RADIUS_NM = 3440.065  # nautical miles


class Reject(StrEnum):
    MMSI = "rejected_mmsi"
    BBOX = "rejected_bbox"
    TELEPORT = "rejected_teleport"


@dataclass(frozen=True, slots=True)
class Fix:
    """A ship's last accepted position — the teleport check's reference point."""

    ts: datetime
    lat: float
    lon: float


class LastPositionStore(Protocol):
    def last_fix(self, mmsi: int) -> Fix | None: ...


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def implied_speed_kn(prev: Fix, row: PositionRow) -> float | None:
    """Speed a ship would need to get from `prev` to `row`. None when time didn't move."""
    hours = (row.ts - prev.ts).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return haversine_nm(prev.lat, prev.lon, row.lat, row.lon) / hours


class Validator:
    def __init__(self, bbox: BBox, mmsi_min: int, mmsi_max: int, teleport_max_kn: float) -> None:
        self._bbox = bbox
        self._mmsi_min = mmsi_min
        self._mmsi_max = mmsi_max
        self._teleport_max_kn = teleport_max_kn

    def in_bbox(self, lat: float, lon: float) -> bool:
        b = self._bbox
        return b.lat_sw <= lat <= b.lat_ne and b.lon_sw <= lon <= b.lon_ne

    def check(self, row: PositionRow, store: LastPositionStore) -> Reject | None:
        """None when the row is good, otherwise the reason it was thrown away."""
        if not self._mmsi_min <= row.mmsi <= self._mmsi_max:
            return Reject.MMSI
        if math.isnan(row.lat) or math.isnan(row.lon) or not self.in_bbox(row.lat, row.lon):
            return Reject.BBOX
        prev = store.last_fix(row.mmsi)
        if prev is not None:
            speed = implied_speed_kn(prev, row)
            if speed is not None and speed > self._teleport_max_kn:
                return Reject.TELEPORT
        return None
