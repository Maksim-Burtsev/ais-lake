"""Raw-topic producer: creates ais.raw if missing, ships bytes untouched.

The payload is passed through byte-for-byte; receive time is stamped as the
Kafka record timestamp plus a recv_ts_ms header — the wire stays raw.
"""

import logging

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from .config import Settings
from .log import kv

logger = logging.getLogger("ingestor")


async def ensure_topic(settings: Settings) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap)
    await admin.start()
    try:
        topic = NewTopic(
            name=settings.raw_topic,
            num_partitions=settings.raw_topic_partitions,
            replication_factor=1,
            topic_configs={"retention.ms": str(settings.raw_retention_ms)},
        )
        try:
            await admin.create_topics([topic])
            logger.info(kv("topic_created", topic=settings.raw_topic,
                           retention_ms=settings.raw_retention_ms))
        except TopicAlreadyExistsError:
            logger.info(kv("topic_exists", topic=settings.raw_topic))
    finally:
        await admin.close()


class RawProducer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        await ensure_topic(self._settings)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap,
            linger_ms=20,
            compression_type="zstd",
        )
        await self._producer.start()

    async def send(self, payload: bytes, recv_ts_ms: int) -> None:
        assert self._producer is not None, "start() first"
        await self._producer.send(
            self._settings.raw_topic,
            value=payload,
            timestamp_ms=recv_ts_ms,
            headers=[("recv_ts_ms", str(recv_ts_ms).encode())],
        )

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
