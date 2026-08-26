"""Pipeline configuration. Limits and regions live here, never hardcoded in code."""

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class BBox(BaseModel):
    """Geographic bounding box, aisstream order: [[lat_sw, lon_sw], [lat_ne, lon_ne]]."""

    lat_sw: float
    lon_sw: float
    lat_ne: float
    lon_ne: float

    def as_aisstream(self) -> list[list[float]]:
        return [[self.lat_sw, self.lon_sw], [self.lat_ne, self.lon_ne]]


# Launch region: North Sea + English Channel (one box, per MVP spec §00).
# Western Approaches to the Skaw: Brest..Dover..Rotterdam..Hamburg..Kattegat.
LAUNCH_BBOX = BBox(lat_sw=49.0, lon_sw=-6.5, lat_ne=61.5, lon_ne=13.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aisstream_api_key: str = ""
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    filter_message_types: list[str] = ["PositionReport", "ShipStaticData"]

    kafka_bootstrap: str = "localhost:19092"
    raw_topic: str = "ais.raw"
    raw_topic_partitions: int = 1
    raw_retention_ms: int = 24 * 60 * 60 * 1000  # 24h — everything after the bus is replayable

    # --- refinery ---
    region_slug: str = "north-sea"
    refinery_group_id: str = "refinery"

    # validation thresholds — never inline these in code
    mmsi_min: int = 200_000_000
    mmsi_max: int = 799_999_999
    teleport_max_kn: float = 100.0

    dedup_ttl_s: float = 60.0
    dedup_max_keys: int = 500_000

    flush_interval_s: float = 2.0
    flush_max_rows: int = 50_000

    redis_url: str = "redis://localhost:6379/0"

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "ais"
    clickhouse_password: str = "ais-dev"
    clickhouse_database: str = "ais"

    metrics_interval_s: float = 10.0
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 60.0
    # a connection alive longer than this is "stable" — backoff resets
    stable_connection_s: float = 30.0


def subscribe_message(settings: Settings, bbox: BBox = LAUNCH_BBOX) -> dict[str, object]:
    """The aisstream.io subscription payload."""
    return {
        "APIKey": settings.aisstream_api_key,
        "BoundingBoxes": [bbox.as_aisstream()],
        "FilterMessageTypes": settings.filter_message_types,
    }
