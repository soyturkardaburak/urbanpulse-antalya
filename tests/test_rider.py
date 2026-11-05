from urbanpulse.rider import arrival_estimate, clock_label, gtfs_seconds, parse_bus, vehicles_from_payload


def test_gtfs_clock_rolls_past_midnight():
    assert gtfs_seconds("07:30:10") == 7 * 3600 + 30 * 60 + 10
    assert clock_label("24:05:00") == "00:05 after midnight"
    assert gtfs_seconds("bad") is None


def test_parse_bus_keeps_a_reported_speed_and_drops_a_position_without_coordinates():
    bus = parse_bus({"lat": "36.9", "lng": "30.7", "plateNumber": "07 ABC 01", "speed": "28", "bearing": "90"})
    assert bus is not None
    assert bus["plate"] == "07 ABC 01"
    assert bus["speed_kmh"] == 28
    assert parse_bus({"plateNumber": "07 ABC 01"}) is None
    assert parse_bus({"lat": "36.9", "lng": "30.7", "speed": "400"})["speed_kmh"] is None


def test_vehicles_come_from_the_live_list_only():
    payload = {"pathList": [{"busList": [{"lat": "36.8", "lng": "30.6", "busId": "1"}]}]}
    vehicles = vehicles_from_payload(payload, 0)
    assert len(vehicles) == 1
    assert vehicles[0]["direction_id"] == 0
    assert vehicles_from_payload({"pathList": [{"busList": []}]}, 1) == []


def test_rank_keeps_the_earliest_upcoming_ride_for_each_line():
    from urbanpulse.planner import rank_direct, ride_minutes, walk_minutes

    assert walk_minutes(30) == 0
    assert walk_minutes(160) == 2
    assert ride_minutes("07:00:00", "07:19:00") == 19
    options = rank_direct(
        [
            _option("998", "08:00:00", True),
            _option("998", "07:09:00", True),
            _option("600", "07:20:00", True),
        ]
    )
    assert [item["departure_gtfs"] for item in options] == ["07:09:00", "07:20:00"]


def _option(route_id: str, departure: str, upcoming: bool) -> dict:
    return {
        "route_id": route_id,
        "direction_id": 0,
        "board_stop_id": "a",
        "alight_stop_id": "b",
        "departure_gtfs": departure,
        "upcoming": upcoming,
        "walk_to_minutes": 4,
        "walk_from_minutes": 3,
        "ride_minutes": 12,
    }


def test_transfer_rank_prefers_a_short_wait_and_one_pair_of_lines():
    from urbanpulse.planner import _best_transfers

    picked = _best_transfers(
        [
            _transfer("A", "B", 40, 20, "06:00:00"),
            _transfer("A", "B", 12, 40, "06:10:00"),
            _transfer("C", "D", 8, 30, "06:20:00"),
        ]
    )
    assert [(item["route_id"], item["wait_minutes"]) for item in picked] == [("C", 8), ("A", 12)]


def _transfer(route_id: str, second: str, wait: int, ride: int, departure: str) -> dict:
    return {
        "route_id": route_id,
        "second_route_id": second,
        "wait_minutes": wait,
        "ride_minutes": ride,
        "departure_gtfs": departure,
    }


def test_arrival_uses_scheduled_running_time_and_does_not_invent_a_zero():
    stops = [
        {"sequence": 1, "stop_id": "a", "name": "A", "lat": 36.90, "lon": 30.70, "running_seconds": 0},
        {"sequence": 2, "stop_id": "b", "name": "B", "lat": 36.91, "lon": 30.71, "running_seconds": 0},
        {"sequence": 3, "stop_id": "c", "name": "C", "lat": 36.93, "lon": 30.73, "running_seconds": 420},
    ]
    near_start = arrival_estimate(stops, 36.90, 30.70, "c")
    assert near_start is not None
    assert near_start["stops_away"] == 2
    assert near_start["eta_minutes"] == 7
    flat = arrival_estimate(stops, 36.90, 30.70, "b")
    assert flat is not None
    assert flat["stops_away"] == 1
    assert flat["eta_minutes"] is None
    assert arrival_estimate(stops, 37.2, 30.7, "c") is None
