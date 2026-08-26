from ais_pipeline.config import LAUNCH_BBOX, BBox, Settings, subscribe_message


def make_settings() -> Settings:
    return Settings(aisstream_api_key="test-key", _env_file=None)


def test_launch_bbox_covers_black_sea_and_marmara() -> None:
    box = LAUNCH_BBOX
    assert box.lat_sw < 40.3 and box.lat_ne > 47.0  # Marmara south to Azov north
    assert box.lon_sw < 26.5 and box.lon_ne > 41.5  # Aegean mouth to Caucasus coast
    assert box.lat_sw < box.lat_ne and box.lon_sw < box.lon_ne


def test_bbox_serialises_in_aisstream_order() -> None:
    box = BBox(lat_sw=1.0, lon_sw=2.0, lat_ne=3.0, lon_ne=4.0)
    assert box.as_aisstream() == [[1.0, 2.0], [3.0, 4.0]]


def test_subscribe_message_shape() -> None:
    msg = subscribe_message(make_settings())
    assert msg["APIKey"] == "test-key"
    assert msg["BoundingBoxes"] == [LAUNCH_BBOX.as_aisstream()]
    assert msg["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]


def test_raw_topic_defaults() -> None:
    s = make_settings()
    assert s.raw_topic == "ais.raw"
    assert s.raw_retention_ms == 86_400_000
