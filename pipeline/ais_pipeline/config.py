"""Pipeline configuration. Limits and regions live here, never hardcoded in code."""

from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# The package lives at <pipeline>/ais_pipeline/, so this is the `pipeline/` dir.
# Relative paths in Settings are resolved against it, whatever the cwd.
PIPELINE_ROOT = Path(__file__).resolve().parent.parent


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

    # --- detectors ---
    detectors_group_id: str = "detectors"

    # A ship has to hold a speed before we believe it, because nav_status flaps
    # (detectors/machine.py argues it with numbers). 20 min to call her stopped
    # because a ferry turns round in 30-45 and must still register; 10 to call
    # her gone, since leaving is the less ambiguous half; 30 before a stop is a
    # wait rather than a manoeuvre. The silence threshold is not here — it is a
    # product limit and lives in limits.json (F27).
    stop_dwell_s: int = 20 * 60
    go_dwell_s: int = 10 * 60
    anchor_min_s: int = 30 * 60
    # AIS reports draught to 0.1 m, so 0.3 clears the reporting noise.
    draught_min_delta_m: float = 0.3
    snapshot_interval_s: float = 30.0

    # --- offline seed (Danish Maritime Authority daily dumps) ---
    seed_base_url: str = "http://aisdata.ais.dk"
    seed_days: int = 7
    seed_lookback_extra_days: int = 7  # scan this far past seed_days for published dumps
    seed_stride: int = 1        # keep every Nth CSV row (1 = all of them)
    seed_cache_dir: str = "../ops/seed/cache"  # relative paths resolve against PIPELINE_ROOT

    metrics_interval_s: float = 10.0
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 60.0
    # a connection alive longer than this is "stable" — backoff resets
    stable_connection_s: float = 30.0

    def seed_cache_path(self) -> Path:
        """seed_cache_dir as an absolute path — relative values hang off PIPELINE_ROOT."""
        path = Path(self.seed_cache_dir).expanduser()
        return path if path.is_absolute() else (PIPELINE_ROOT / path).resolve()


def subscribe_message(settings: Settings, bbox: BBox = LAUNCH_BBOX) -> dict[str, object]:
    """The aisstream.io subscription payload."""
    return {
        "APIKey": settings.aisstream_api_key,
        "BoundingBoxes": [bbox.as_aisstream()],
        "FilterMessageTypes": settings.filter_message_types,
    }
