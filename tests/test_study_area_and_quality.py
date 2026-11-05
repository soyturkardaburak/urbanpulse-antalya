import pandas as pd

from shapely.geometry import Point, box

from urbanpulse.quality import check_feed
from urbanpulse.study_area import delineate_study_area, evaluate_control_points


def test_dense_cluster_excludes_a_distant_outlier():
    longitudes = [30.70 + (index % 5) * 0.0002 for index in range(25)]
    latitudes = [36.89 + (index // 5) * 0.0002 for index in range(25)]
    longitudes.extend([29.64 + index * 0.02 for index in range(5)])
    latitudes.extend([36.20] * 5)

    polygon, metadata, _core = delineate_study_area(longitudes, latitudes)
    assert metadata["computed"]["threshold_stops"] >= 5
    assert polygon.covers(Point(30.70, 36.89))
    assert not polygon.covers(Point(29.64, 36.20))
    assert metadata["computed"]["area_km2"] < 50


def test_control_point_roles_are_explicit():
    polygon = box(30.5, 36.7, 30.9, 37.0)
    results = evaluate_control_points(
        polygon,
        [
            {"id": "in", "name": "Inside", "lon": 30.7, "lat": 36.8, "expect": "inside", "role": "required", "note": ""},
            {"id": "out", "name": "Outside", "lon": 29.6, "lat": 36.2, "expect": "outside", "role": "required", "note": ""},
        ],
    )
    assert all(point["passed"] for point in results)
    assert polygon.contains(Point(30.7, 36.8))


def test_quality_flags_structural_errors_separately_from_warnings():
    stops = pd.DataFrame(
        [
            {"stop_id": "a", "stop_name": "A", "stop_lat": "36.8", "stop_lon": "30.7"},
            {"stop_id": "b", "stop_name": "B", "stop_lat": "", "stop_lon": ""},
        ]
    )
    routes = pd.DataFrame([{"route_id": "r", "route_type": "3"}])
    trips = pd.DataFrame([{"trip_id": "t", "route_id": "r", "service_id": "s", "shape_id": ""}])
    stop_times = pd.DataFrame(
        [
            {
                "trip_id": "t",
                "arrival_time": "08:10:00",
                "departure_time": "08:09:00",
                "stop_id": "a",
                "stop_sequence": "1",
            }
        ]
    )
    calendar = pd.DataFrame(
        [
            {
                "service_id": "s",
                "monday": "1",
                "tuesday": "1",
                "wednesday": "1",
                "thursday": "1",
                "friday": "1",
                "saturday": "0",
                "sunday": "0",
                "start_date": "20260901",
                "end_date": "20261031",
            }
        ]
    )
    agencies = pd.DataFrame([{"agency_id": "1", "agency_name": "Example", "agency_timezone": "Europe/Istanbul"}])
    issues = check_feed(
        {
            "agency.txt": agencies,
            "stops.txt": stops,
            "routes.txt": routes,
            "trips.txt": trips,
            "stop_times.txt": stop_times,
            "calendar.txt": calendar,
            "shapes.txt": None,
        }
    )
    codes = {issue.code for issue in issues}
    assert "missing_coordinates" in codes
    assert "departure_before_arrival" in codes
    assert "calendar_window_looks_like_generator_default" in codes
    assert "holiday_exceptions_absent" in codes
    severities = {issue.code: issue.severity for issue in issues}
    assert severities["missing_coordinates"] == "error"
    assert severities["departure_before_arrival"] == "warning"
