import json
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from urbanpulse.destinations import prefer_area_geometries, review_hospital
from urbanpulse.gtfs_subset import departures_in_window, repair_stop_times
from urbanpulse.timetable_check import compare_pattern, interpretation_allowed, parse_route_options, parse_timetable_rows


def test_timetable_parser_reads_a_broken_table_and_ignores_the_form():
    page = """
    <select id="selRoute"><option value="">Lütfen seçin...</option>
    <option value="9980">998</option></select>
    <table><tbody></div><div>
    <tr><td>998</td><td>ZERDAL&#304;L&#304;K-M&#220;ZE</td><td>M&#220;ZE - ZERDAL&#304;L&#304;K</td>
    <td>07:30:00</td><td>15055</td><td>M&#220;ZE 1</td><td>Gidiş</td></tr>
    </tbody></table>
    """
    assert parse_route_options(page) == {"998": "9980"}
    rows = parse_timetable_rows(page)
    assert rows[0]["stop_id"] == "15055"
    assert rows[0]["stop_name"] == "MÜZE 1"
    assert rows[0]["route_description"] == "ZERDALİLİK-MÜZE"


def _trip(stop_times: list[tuple[str, int, int]]) -> list[dict]:
    return [
        {"stop_id": stop_id, "stop_sequence": sequence, "departure_seconds": seconds}
        for stop_id, sequence, seconds in stop_times
    ]


def test_pattern_passes_when_first_middle_and_last_agree():
    published = [
        {"stop_id": "A", "stop_name": "A", "time": "08:00:00"},
        {"stop_id": "B", "stop_name": "B", "time": "08:10:00"},
        {"stop_id": "C", "stop_name": "C", "time": "08:20:00"},
    ]
    result = compare_pattern(published, {"t1": _trip([("A", 1, 8 * 3600), ("B", 2, 8 * 3600 + 600), ("C", 3, 8 * 3600 + 1200)])})
    assert result["status"] == "pass"
    assert result["anchors"]["middle"]["stop_id"] == "B"
    assert result["anchors"]["last"]["delta_seconds"] == 0


def test_a_trip_that_shares_no_stops_does_not_beat_a_close_trip():
    published = [
        {"stop_id": "A", "stop_name": "A", "time": "08:00:00"},
        {"stop_id": "B", "stop_name": "B", "time": "08:10:00"},
        {"stop_id": "C", "stop_name": "C", "time": "08:20:00"},
    ]
    close = _trip([("A", 1, 8 * 3600 + 5), ("B", 2, 8 * 3600 + 605), ("C", 3, 8 * 3600 + 1205)])
    empty = _trip([("Z", 1, 8 * 3600)])
    result = compare_pattern(published, {"close": close, "empty": empty})
    assert result["status"] == "pass"
    assert result["gtfs_trip_id"] == "close"


def test_pattern_fails_when_the_last_stop_is_more_than_a_minute_off():
    published = [
        {"stop_id": "A", "stop_name": "A", "time": "08:00:00"},
        {"stop_id": "B", "stop_name": "B", "time": "08:10:00"},
        {"stop_id": "C", "stop_name": "C", "time": "08:20:00"},
    ]
    result = compare_pattern(published, {"t1": _trip([("A", 1, 8 * 3600), ("B", 2, 8 * 3600 + 600), ("C", 3, 8 * 3600 + 1500)])})
    assert result["status"] == "fail"
    assert result["anchors"]["last"]["delta_seconds"] == -300


def test_interpretation_requires_eighty_percent_and_a_complete_fetch():
    rows = [{"status": "pass", "day_code": 3} for _ in range(12)]
    rows.extend({"status": "fail", "day_code": 3} for _ in range(3))
    assert interpretation_allowed(rows)[0] is True
    rows.append({"status": "fail", "day_code": 3})
    assert interpretation_allowed(rows)[0] is False
    assert interpretation_allowed([{"status": "fetch_error"}])[0] is False


def test_unknown_hospital_name_is_rejected():
    with pytest.raises(KeyError):
        review_hospital("A hospital that was not reviewed")
    include, reason = review_hospital("Akdeniz Üniversitesi Hastanesi")
    assert include is True
    assert "university" in reason


def test_named_point_is_dropped_when_the_same_name_has_a_polygon():
    features = [
        {"name": "Example", "geometry": Point(0, 0)},
        {"name": "Example", "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])},
        {"name": "Point only", "geometry": Point(1, 1)},
    ]
    kept = prefer_area_geometries(features)
    assert [feature["geometry"].geom_type for feature in kept] == ["Polygon", "Point"]


def test_small_reversal_is_clamped_and_a_large_one_is_removed():
    frame = pd.DataFrame(
        {
            "arrival_time": ["08:00:00", "09:00:00"],
            "departure_time": ["07:59:30", "08:00:00"],
        }
    )
    repaired, notes = repair_stop_times(frame)
    assert list(repaired["departure_time"]) == ["08:00:00"]
    assert notes == {"clamped_within_60s": 1, "quarantined_over_60s": 1}


def test_frequency_counts_only_the_wednesday_window():
    trips = pd.DataFrame(
        {
            "trip_id": ["a", "b", "c"],
            "route_id": ["r1", "r1", "r2"],
            "service_id": ["service_1_MTWTFss", "service_1_MTWTFss", "service_2_mtwtfSs"],
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["a", "b", "c"],
            "stop_id": ["s", "s", "s"],
            "departure_time": ["07:30:00", "09:00:00", "07:30:00"],
        }
    )
    counts = departures_in_window(stop_times, trips)
    assert int(counts.iloc[0]["departures_in_window"]) == 1
    assert float(counts.iloc[0]["departures_per_hour"]) == 0.5
    assert int(counts.iloc[0]["distinct_routes"]) == 1


def test_origin_cells_keep_centres_inside_a_small_polygon():
    from urbanpulse.accessibility import origin_cells

    polygon = Polygon(
        [
            (30.70, 36.88),
            (30.72, 36.88),
            (30.72, 36.90),
            (30.70, 36.90),
            (30.70, 36.88),
        ]
    )
    cells = origin_cells(polygon, resolution=9)
    assert cells
    import h3
    from shapely.prepared import prep

    prepared = prep(polygon)
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        assert prepared.covers(Point(lon, lat))
