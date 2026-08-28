"""/v1/regions against a fake hash — no Redis, no network."""

from app.map import parse_bbox
from app.regions import REGIONS, regions_payload
from tests.test_map import DeadRedis, FakeRedis, field

# One ship in the Kattegat, one off the Dutch coast (inside neither strait).
FLEET = {
    "244660000": field(1789000000, 56.8, 11.5, 12.4, 87.0, "underway"),
    "205344000": field(1789000001, 51.9, 4.1, 0.0, 210.0, "moored"),
}


def by_name(payload: dict) -> dict[str, int | None]:
    return {r["name"]: r["count"] for r in [*payload["regions"], *payload["straits"]]}


async def test_counts_are_per_bbox_and_boxes_overlap() -> None:
    counts = by_name(await regions_payload(FakeRedis(FLEET)))
    assert counts["North Sea"] == 2  # both ships are in the launch box
    assert counts["Kattegat"] == 1  # ... and one of them also in the Kattegat
    assert counts["Dover Strait"] == 0  # a real zero, not a null


async def test_coming_soon_regions_never_get_a_count() -> None:
    counts = by_name(await regions_payload(FakeRedis(FLEET)))
    assert counts["Baltic"] is None
    assert counts["Bay of Biscay"] is None


async def test_dead_redis_keeps_the_picker_working_with_null_counts() -> None:
    payload = await regions_payload(DeadRedis())
    assert set(by_name(payload).values()) == {None}
    assert payload["regions"] and payload["straits"]
    assert all("bbox" in r and "slug" in r for r in payload["regions"])


def test_every_configured_bbox_is_one_the_api_would_accept() -> None:
    entries = [*REGIONS["seas"], *REGIONS["straits"]]
    assert REGIONS["straits"], "launch straits (Dover + Kattegat) must be configured"
    for entry in entries:
        w, s, e, n = entry["bbox"]
        assert parse_bbox(f"{w},{s},{e},{n}") == (w, s, e, n), entry["slug"]
