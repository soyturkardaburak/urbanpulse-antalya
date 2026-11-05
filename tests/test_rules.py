from urbanpulse.gtfs_time import gtfs_time_to_seconds
from urbanpulse.route_register import inclusion_decision


def test_gtfs_time_accepts_hours_past_midnight():
    assert gtfs_time_to_seconds("25:10:00") == 25 * 3600 + 10 * 60


def test_gtfs_time_rejects_invalid_minutes():
    assert gtfs_time_to_seconds("10:60:00") is None
    assert gtfs_time_to_seconds("") is None
    assert gtfs_time_to_seconds("10:00") is None


def test_route_inclusion_uses_eighty_percent_not_a_majority_label():
    include, reason = inclusion_decision(0.8, 10)
    assert include is True
    assert reason == ""

    include, reason = inclusion_decision(0.79, 100)
    assert include is False
    assert reason == "fewer_than_80_percent_of_stops_inside_study_area"

    include, reason = inclusion_decision(1.0, 1)
    assert include is False
    assert reason == "fewer_than_2_stops"
