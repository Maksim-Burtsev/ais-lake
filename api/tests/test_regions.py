"""/v1/regions against a fake hash — no Redis, no network."""

from app.map import parse_bbox
from app.regions import REGIONS, regions_payload
from tests.test_map import FRESH, STALE, DeadRedis, FakeRedis, field

# One ship in the Kattegat, one off the Dutch coast (inside neither strait).
FLEET = {
    "244660000": field(FRESH, 56.8, 11.5, 12.4, 87.0, "underway"),
    "205344000": field(FRESH, 51.9, 4.1, 0.0, 210.0, "moored"),
}


def by_name(payload: dict) -> dict[str, int | None]:
    return {r["name"]: r["count"] for r in [*payload["regions"], *payload["straits"]]}


async def test_counts_are_per_bbox_and_boxes_overlap() -> None:
    counts = by_name(await regions_payload(FakeRedis(FLEET)))
    assert counts["North Sea"] == 2  # both ships are in the launch box
    assert counts["Kattegat"] == 1  # ... and one of them also in the Kattegat
    assert counts["Dover Strait"] == 0  # a real zero, not a null


async def test_a_stale_fix_does_not_inflate_a_region_count() -> None:
    """Same cut as the map: a ship last heard days ago is not "here" in the picker."""
    ghost = {**FLEET, "219000001": field(STALE, 56.9, 11.6, 0.0, 0.0, "moored")}
    counts = by_name(await regions_payload(FakeRedis(ghost)))
    assert counts["North Sea"] == 2
    assert counts["Kattegat"] == 1


async def test_coming_soon_regions_never_get_a_count() -> None:
    counts = by_name(await regions_payload(FakeRedis(FLEET)))
    assert counts["Baltic"] is None
    assert counts["Bay of Biscay"] is None


async def test_dead_redis_keeps_the_picker_working_with_null_counts() -> None:
    payload = await regions_payload(DeadRedis())
    assert set(by_name(payload).values()) == {None}
    assert payload["regions"] and payload["straits"]
    assert all("bbox" in r and "slug" in r for r in payload["regions"])


async def test_a_live_region_with_redis_down_is_live_with_no_number() -> None:
    """`live` and the count are two facts, and null cannot carry both. Read as
    coming-soon, an unreachable Redis would disable every row in the picker and
    strand the user in the region they were trying to leave."""
    payload = await regions_payload(DeadRedis())
    rows = {r["name"]: r for r in [*payload["regions"], *payload["straits"]]}
    assert rows["North Sea"]["live"] is True and rows["North Sea"]["count"] is None
    assert rows["Kattegat"]["live"] is True and rows["Kattegat"]["count"] is None
    assert rows["Baltic"]["live"] is False  # this one really is coming soon

    healthy = {r["name"]: r for r in (await regions_payload(FakeRedis(FLEET)))["regions"]}
    assert healthy["North Sea"] == {**healthy["North Sea"], "live": True, "count": 2}


async def test_route_never_500s_when_redis_is_down() -> None:
    from app.main import regions, runtime

    original = runtime.redis
    try:
        runtime.redis = DeadRedis()  # type: ignore[assignment]
        payload = await regions()
    finally:
        runtime.redis = original
    assert payload["regions"] and payload["straits"]
    assert set(by_name(payload).values()) == {None}


def test_every_configured_bbox_is_one_the_api_would_accept() -> None:
    entries = [*REGIONS["seas"], *REGIONS["straits"]]
    assert REGIONS["straits"], "launch straits (Dover + Kattegat) must be configured"
    for entry in entries:
        w, s, e, n = entry["bbox"]
        assert parse_bbox(f"{w},{s},{e},{n}") == (w, s, e, n), entry["slug"]
