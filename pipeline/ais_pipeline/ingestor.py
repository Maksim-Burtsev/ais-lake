"""aisstream.io WebSocket ingestor: one connection, raw passthrough to ais.raw.

Reconnects forever with exponential backoff + full jitter; connects and
disconnects are logged as structured incidents. msg/s is logged every
metrics_interval_s.
"""

import asyncio
import json
import logging
import time

import websockets
from websockets.asyncio.client import ClientConnection

from .backoff import Backoff
from .config import LAUNCH_BBOX, Settings, subscribe_message
from .log import kv, setup
from .producer import RawProducer

logger = logging.getLogger("ingestor")


class Metrics:
    def __init__(self) -> None:
        self.messages = 0

    async def report_forever(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            rate = self.messages / interval_s
            logger.info(kv("throughput", msg_per_s=f"{rate:.1f}", window_s=interval_s))
            self.messages = 0


async def consume_connection(
    ws: ClientConnection,
    producer: RawProducer,
    metrics: Metrics,
) -> None:
    async for message in ws:
        recv_ts_ms = int(time.time() * 1000)
        payload = message if isinstance(message, bytes) else message.encode()
        await producer.send(payload, recv_ts_ms)
        metrics.messages += 1


async def run(settings: Settings) -> None:
    if not settings.aisstream_api_key:
        raise SystemExit("AISSTREAM_API_KEY is not set")

    producer = RawProducer(settings)
    await producer.start()
    metrics = Metrics()
    reporter = asyncio.create_task(metrics.report_forever(settings.metrics_interval_s))
    backoff = Backoff(settings.backoff_base_s, settings.backoff_cap_s)

    try:
        while True:
            connected_at: float | None = None
            try:
                async with websockets.connect(settings.aisstream_url) as ws:
                    await ws.send(json.dumps(subscribe_message(settings)))
                    connected_at = time.monotonic()
                    logger.info(kv("ws_connect", url=settings.aisstream_url,
                                   bbox=LAUNCH_BBOX.as_aisstream()))
                    await consume_connection(ws, producer, metrics)
                    logger.warning(kv("ws_disconnect", reason="stream_ended"))
            except (websockets.WebSocketException, OSError) as exc:
                logger.warning(
                    kv("ws_disconnect", reason=type(exc).__name__, detail=str(exc)[:200])
                )

            if connected_at is not None:
                lived_s = time.monotonic() - connected_at
                if lived_s >= settings.stable_connection_s:
                    backoff.reset()
            delay = backoff.next_delay()
            logger.info(kv("ws_reconnect_wait", delay_s=f"{delay:.1f}", attempt=backoff.attempt))
            await asyncio.sleep(delay)
    finally:
        reporter.cancel()
        await producer.stop()


def main() -> None:
    setup()
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
