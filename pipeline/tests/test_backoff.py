from ais_pipeline.backoff import Backoff


def test_ceiling_grows_exponentially_to_cap() -> None:
    b = Backoff(base_s=1.0, cap_s=60.0, rng=lambda: 1.0)  # jitter pinned to max
    delays = [b.next_delay() for _ in range(8)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_full_jitter_stays_within_ceiling() -> None:
    import random

    rng = random.Random(42)
    b = Backoff(base_s=1.0, cap_s=60.0, rng=rng.random)
    for expected_cap in [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]:
        d = b.next_delay()
        assert 0.0 <= d <= expected_cap


def test_reset_starts_over() -> None:
    b = Backoff(base_s=1.0, cap_s=60.0, rng=lambda: 1.0)
    for _ in range(5):
        b.next_delay()
    b.reset()
    assert b.next_delay() == 1.0
    assert b.attempt == 1
