"""aisstream.io WebSocket ingestor: one connection, raw passthrough to ais.raw.

Reconnects forever with exponential backoff + full jitter; connects and
disconnects are logged as structured incidents — to stdout and, when Redis is
reachable, to the shared incident log /status.json reads. msg/s is logged every
metrics_interval_s.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as redis
import websockets
from websockets.asyncio.client import ClientConnection

from .backoff import Backoff
from .config import LAUNCH_BBOX, Settings, subscribe_message
from .incidents import record_incident
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


async def incident_client(url: str) -> redis.Redis | None:
    """A Redis connection for the incident log, or None when Redis is not there.

    The ingestor's job is the stream; a missing incident log is a nicety lost,
    never a reason to refuse to start.
    """
    try:
        client = redis.from_url(url, decode_responses=True)
        await client.ping()
    except Exception as exc:
        logger.warning(kv("incidents_unavailable", reason=type(exc).__name__))
        return None
    return client


async def run(settings: Settings) -> None:
    if not settings.aisstream_api_key:
        raise SystemExit("AISSTREAM_API_KEY is not set")

    incidents = await incident_client(settings.redis_url)
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
                    await record_incident(incidents, "ws_connect", url=settings.aisstream_url)
                    await consume_connection(ws, producer, metrics)
                    logger.warning(kv("ws_disconnect", reason="stream_ended"))
                    await record_incident(incidents, "ws_disconnect", reason="stream_ended")
            except (websockets.WebSocketException, OSError) as exc:
                reason, detail = type(exc).__name__, str(exc)[:200]
                logger.warning(kv("ws_disconnect", reason=reason, detail=detail))
                await record_incident(incidents, "ws_disconnect", reason=reason, detail=detail)

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
        if incidents is not None:
            await incidents.aclose()


def main() -> None:
    setup()
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
