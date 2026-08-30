"""Every template of the status sentence, and the seams between its units."""

from ais_pipeline.sentences import humanize_duration, sentence_for

HOUR = 3600
DAY = 86_400
NOW = 1_756_000_000.0


def ago(seconds: float) -> float:
    return NOW - seconds


def test_under_way_states_the_speed_to_one_decimal() -> None:
    assert sentence_for("underway", sog=12.34) == "Under way at 12.3 kn"


def test_under_way_keeps_the_trailing_zero() -> None:
    assert sentence_for("underway", sog=12.0) == "Under way at 12.0 kn"


def test_drifting_slower_than_the_floor_is_just_under_way() -> None:
    assert sentence_for("underway", sog=0.4) == "Under way"


def test_a_stop_too_young_to_name_is_just_stopped() -> None:
    assert sentence_for("stopped", now_ts=NOW, still_since=ago(HOUR)) == "Stopped"


def test_at_anchor_in_open_water_counts_the_hours() -> None:
    assert (
        sentence_for("anchored", now_ts=NOW, still_since=ago(14 * HOUR))
        == "At anchor — 14 hours"
    )


def test_an_anchorage_with_a_port_names_the_port() -> None:
    assert (
        sentence_for(
            "anchored",
            now_ts=NOW,
            still_since=ago(14 * HOUR),
            port_name="Rotterdam",
            in_anchorage=True,
        )
        == "Waiting off Rotterdam — 14 hours"
    )


def test_an_anchorage_without_a_name_falls_back_to_at_anchor() -> None:
    assert (
        sentence_for("anchored", now_ts=NOW, still_since=ago(HOUR), in_anchorage=True)
        == "At anchor — 1 hour"
    )


def test_moored_names_the_port_when_we_have_one() -> None:
    assert (
        sentence_for("moored", now_ts=NOW, still_since=ago(2 * DAY), port_name="Rotterdam")
        == "Moored in Rotterdam — 2 days"
    )


def test_moored_without_a_port_still_counts_the_days() -> None:
    assert sentence_for("moored", now_ts=NOW, still_since=ago(2 * DAY)) == "Moored — 2 days"


def test_a_seeded_ship_has_no_duration_clause() -> None:
    assert sentence_for("moored", now_ts=NOW, port_name="Rotterdam") == "Moored in Rotterdam"
    assert sentence_for("anchored", now_ts=NOW) == "At anchor"


def test_minutes_below_the_hour() -> None:
    assert humanize_duration(40 * 60) == "40 minutes"
    assert humanize_duration(60) == "1 minute"
    assert humanize_duration(0) == "0 minutes"


def test_the_hour_boundary_switches_units() -> None:
    assert humanize_duration(HOUR - 1) == "60 minutes"
    assert humanize_duration(HOUR) == "1 hour"


def test_hours_hold_until_two_days() -> None:
    assert humanize_duration(47 * HOUR) == "47 hours"
    assert humanize_duration(48 * HOUR - 1) == "48 hours"
    assert humanize_duration(48 * HOUR) == "2 days"


def test_a_single_day_is_singular() -> None:
    # Only reachable through rounding: 48 h exactly is the first day-counted span.
    assert humanize_duration(36 * HOUR) == "36 hours"
    assert humanize_duration(9 * DAY) == "9 days"


def test_negative_spans_never_leak_a_minus_sign() -> None:
    assert sentence_for("anchored", now_ts=NOW, still_since=NOW + 5) == "At anchor — 0 minutes"


def test_the_em_dash_appears_at_most_once() -> None:
    # ShipCard.tsx splits on it: a second one would break the bold clause.
    for s in (
        sentence_for("moored", now_ts=NOW, still_since=ago(DAY), port_name="Rotterdam"),
        sentence_for("anchored", now_ts=NOW, still_since=ago(HOUR), port_name="Rotterdam",
                     in_anchorage=True),
        sentence_for("underway", sog=9.5),
    ):
        assert s.count(" — ") <= 1
