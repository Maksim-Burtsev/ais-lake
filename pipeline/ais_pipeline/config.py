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


# Launch region: Black Sea + Bosphorus + Sea of Marmara (one box, per MVP spec).
LAUNCH_BBOX = BBox(lat_sw=40.2, lon_sw=26.0, lat_ne=47.4, lon_ne=42.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aisstream_api_key: str = ""
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    filter_message_types: list[str] = ["PositionReport", "ShipStaticData"]

    kafka_bootstrap: str = "localhost:19092"
    raw_topic: str = "ais.raw"
    raw_topic_partitions: int = 1
    raw_retention_ms: int = 24 * 60 * 60 * 1000  # 24h — everything after the bus is replayable

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
