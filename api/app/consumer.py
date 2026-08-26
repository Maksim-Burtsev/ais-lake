"""M0 steel-thread consumer: ais.raw → in-memory latest-per-ship dict.

Deliberately naive — no dedup, no validation, no stores. M1 replaces this
with the real refinery; only the steel thread needs it.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from aiokafka import AIOKafkaConsumer


@dataclass
class ShipLatest:
    mmsi: int
    name: str
    lat: float
    lon: float
    sog: float | None
    ts: float  # recv time, epoch seconds


class LatestShips:
    """Latest known state per MMSI, newest-first listing."""

    def __init__(self) -> None:
        self._ships: dict[int, ShipLatest] = {}

    def apply(self, raw: bytes | str, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        meta = msg.get("MetaData")
        if not isinstance(meta, dict):
            return  # SubscriptionConfirmation and friends
        mmsi = meta.get("MMSI")
        lat, lon = meta.get("latitude"), meta.get("longitude")
        if not isinstance(mmsi, int) or lat is None or lon is None:
            return
        name = str(meta.get("ShipName") or "").strip() or self._name_of(mmsi) or f"MMSI {mmsi}"
        sog = self._sog(msg)
        prev = self._ships.get(mmsi)
        if sog is None and prev is not None:
            sog = prev.sog
        self._ships[mmsi] = ShipLatest(
            mmsi=mmsi, name=name, lat=float(lat), lon=float(lon), sog=sog, ts=ts
        )

    def _name_of(self, mmsi: int) -> str | None:
        prev = self._ships.get(mmsi)
        return prev.name if prev else None

    @staticmethod
    def _sog(msg: dict[str, Any]) -> float | None:
        report = msg.get("Message", {}).get("PositionReport")
        if isinstance(report, dict) and isinstance(report.get("Sog"), int | float):
            return float(report["Sog"])
        return None

    def top(self, n: int = 20) -> list[ShipLatest]:
        return sorted(self._ships.values(), key=lambda s: s.ts, reverse=True)[:n]

    def __len__(self) -> int:
        return len(self._ships)


async def consume_forever(ships: LatestShips, bootstrap: str, topic: str) -> None:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id="debug-eyeball",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for record in consumer:
            if record.value is not None:
                ships.apply(record.value, now=record.timestamp / 1000)
    finally:
        await consumer.stop()
