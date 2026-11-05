from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from urbanpulse.gtfs_load import OPTIONAL_TABLES, REQUIRED_TABLES, require_columns
from urbanpulse.gtfs_time import gtfs_time_to_seconds
from urbanpulse.jsonutil import json_safe

REQUIRED_FIELDS = {
    "agency.txt": ("agency_id", "agency_name", "agency_timezone"),
    "stops.txt": ("stop_id", "stop_name", "stop_lat", "stop_lon"),
    "routes.txt": ("route_id", "route_type"),
    "trips.txt": ("trip_id", "route_id", "service_id"),
    "stop_times.txt": ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"),
    "calendar.txt": ("service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"),
    "shapes.txt": ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"),
}


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    count: int
    samples: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return json_safe(
            {
                "severity": self.severity,
                "code": self.code,
                "message": self.message,
                "count": self.count,
                "samples": self.samples,
            }
        )


def _samples(frame: pd.DataFrame, columns: list[str], limit: int = 5) -> list[dict]:
    if frame.empty:
        return []
    present = [column for column in columns if column in frame.columns]
    return json_safe(frame[present].head(limit).to_dict(orient="records"))


def check_feed(tables: dict[str, pd.DataFrame | None]) -> list[Issue]:
    issues: list[Issue] = []
    for name in REQUIRED_TABLES:
        if tables.get(name) is None:
            issues.append(Issue("error", "missing_table", f"Required GTFS file {name} is absent.", 1))
    for name in OPTIONAL_TABLES:
        if tables.get(name) is None:
            issues.append(
                Issue(
                    "warning",
                    "missing_optional_table",
                    f"{name} is absent. Route geometry cannot be drawn from this feed.",
                    1,
                )
            )

    for name, columns in REQUIRED_FIELDS.items():
        frame = tables.get(name)
        if frame is None:
            continue
        missing = require_columns(frame, columns)
        if missing:
            issues.append(
                Issue(
                    "error",
                    "missing_fields",
                    f"{name} is missing required fields: {', '.join(missing)}.",
                    len(missing),
                    [{"field": field} for field in missing],
                )
            )

    issues.extend(_check_stops(tables.get("stops.txt")))
    issues.extend(_check_references(tables))
    issues.extend(_check_times(tables.get("stop_times.txt")))
    issues.extend(_check_calendar(tables.get("calendar.txt"), tables.get("trips.txt")))
    issues.extend(_check_known_absences(tables))
    return issues


def _check_stops(stops: pd.DataFrame | None) -> list[Issue]:
    if stops is None or any(column not in stops.columns for column in ("stop_id", "stop_lat", "stop_lon")):
        return []
    issues: list[Issue] = []
    duplicated = stops[stops["stop_id"].duplicated(keep=False)]
    if not duplicated.empty:
        issues.append(
            Issue(
                "error",
                "duplicate_stop_id",
                "stop_id values are duplicated.",
                int(duplicated["stop_id"].nunique()),
                _samples(duplicated, ["stop_id", "stop_name"]),
            )
        )

    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
    missing = stops[lat.isna() | lon.isna() | (stops["stop_lat"].str.strip() == "") | (stops["stop_lon"].str.strip() == "")]
    if not missing.empty:
        issues.append(
            Issue(
                "error",
                "missing_coordinates",
                "Stops have missing or non-numeric coordinates.",
                int(len(missing)),
                _samples(missing, ["stop_id", "stop_name", "stop_lat", "stop_lon"]),
            )
        )

    valid = stops[lat.notna() & lon.notna()].copy()
    valid["_lat"] = lat[lat.notna() & lon.notna()]
    valid["_lon"] = lon[lat.notna() & lon.notna()]
    invalid = valid[(valid["_lat"].abs() > 90) | (valid["_lon"].abs() > 180) | ((valid["_lat"] == 0) & (valid["_lon"] == 0))]
    if not invalid.empty:
        issues.append(
            Issue(
                "error",
                "invalid_coordinates",
                "Stops have coordinates outside the valid range, or at 0,0.",
                int(len(invalid)),
                _samples(invalid, ["stop_id", "stop_name", "stop_lat", "stop_lon"]),
            )
        )
    return issues


def _check_references(tables: dict[str, pd.DataFrame | None]) -> list[Issue]:
    issues: list[Issue] = []
    routes = tables.get("routes.txt")
    trips = tables.get("trips.txt")
    stops = tables.get("stops.txt")
    stop_times = tables.get("stop_times.txt")
    calendar = tables.get("calendar.txt")
    if trips is not None and "trip_id" in trips.columns:
        duplicated = trips[trips["trip_id"].duplicated(keep=False)]
        if not duplicated.empty:
            issues.append(
                Issue(
                    "error",
                    "duplicate_trip_id",
                    "trip_id values are duplicated.",
                    int(duplicated["trip_id"].nunique()),
                    _samples(duplicated, ["trip_id", "route_id"]),
                )
            )
    if trips is not None and routes is not None and {"trip_id", "route_id"}.issubset(trips.columns):
        missing_routes = trips[~trips["route_id"].isin(set(routes["route_id"]))]
        if not missing_routes.empty:
            issues.append(
                Issue(
                    "error",
                    "trip_missing_route",
                    "Trips reference a route_id that is not in routes.txt.",
                    int(len(missing_routes)),
                    _samples(missing_routes, ["trip_id", "route_id"]),
                )
            )
    if stop_times is not None and trips is not None and "trip_id" in stop_times.columns:
        trips_without_times = set(trips["trip_id"]) - set(stop_times["trip_id"])
        if trips_without_times:
            issues.append(
                Issue(
                    "error",
                    "trip_without_stop_times",
                    "Trips have no stop_times rows.",
                    len(trips_without_times),
                    [{"trip_id": trip_id} for trip_id in sorted(trips_without_times)[:5]],
                )
            )
        orphan_times = stop_times[~stop_times["trip_id"].isin(set(trips["trip_id"]))]
        if not orphan_times.empty:
            issues.append(
                Issue(
                    "error",
                    "stop_time_missing_trip",
                    "stop_times rows reference a trip_id that is not in trips.txt.",
                    int(orphan_times["trip_id"].nunique()),
                    _samples(orphan_times, ["trip_id", "stop_id", "stop_sequence"]),
                )
            )
    if stop_times is not None and stops is not None and "stop_id" in stop_times.columns:
        missing_stops = stop_times[~stop_times["stop_id"].isin(set(stops["stop_id"]))]
        if not missing_stops.empty:
            issues.append(
                Issue(
                    "error",
                    "stop_time_missing_stop",
                    "stop_times rows reference a stop_id that is not in stops.txt.",
                    int(missing_stops["stop_id"].nunique()),
                    _samples(missing_stops, ["trip_id", "stop_id", "stop_sequence"]),
                )
            )
    if trips is not None and calendar is not None and "service_id" in trips.columns:
        missing_services = sorted(set(trips["service_id"]) - set(calendar["service_id"]))
        if missing_services:
            issues.append(
                Issue(
                    "warning",
                    "trip_missing_calendar",
                    "Trips use a service_id that is not in calendar.txt.",
                    len(missing_services),
                    [{"service_id": service_id} for service_id in missing_services[:5]],
                )
            )
    if stop_times is not None and {"trip_id", "stop_sequence"}.issubset(stop_times.columns):
        duplicated = stop_times[stop_times.duplicated(["trip_id", "stop_sequence"], keep=False)]
        if not duplicated.empty:
            issues.append(
                Issue(
                    "error",
                    "duplicate_stop_sequence",
                    "The same trip_id and stop_sequence appear more than once.",
                    int(len(duplicated)),
                    _samples(duplicated, ["trip_id", "stop_id", "stop_sequence"]),
                )
            )
    return issues


def _check_times(stop_times: pd.DataFrame | None) -> list[Issue]:
    if stop_times is None or not {"trip_id", "arrival_time", "departure_time", "stop_sequence"}.issubset(stop_times.columns):
        return []
    frame = stop_times.copy()
    frame["arr_s"] = frame["arrival_time"].map(gtfs_time_to_seconds)
    frame["dep_s"] = frame["departure_time"].map(gtfs_time_to_seconds)
    issues: list[Issue] = []
    unparseable = frame[frame["arr_s"].isna() | frame["dep_s"].isna()]
    if not unparseable.empty:
        issues.append(
            Issue(
                "error",
                "unparseable_time",
                "stop_times contain empty or malformed arrival or departure times.",
                int(len(unparseable)),
                _samples(unparseable, ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"]),
            )
        )

    comparable = frame[frame["arr_s"].notna() & frame["dep_s"].notna()].copy()
    early_departure = comparable[comparable["dep_s"] < comparable["arr_s"]].copy()
    if not early_departure.empty:
        early_departure["seconds"] = early_departure["arr_s"] - early_departure["dep_s"]
        within_minute = int((early_departure["seconds"] <= 60).sum())
        issues.append(
            Issue(
                "warning",
                "departure_before_arrival",
                (
                    "Departure time is earlier than arrival time. "
                    f"{within_minute} of these differ by 60 seconds or less. "
                    "Phase 1 records them and does not rewrite the feed."
                ),
                int(len(early_departure)),
                _samples(early_departure, ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"]),
            )
        )

    comparable["stop_sequence_n"] = pd.to_numeric(comparable["stop_sequence"], errors="coerce")
    ordered = comparable.dropna(subset=["stop_sequence_n"]).sort_values(["trip_id", "stop_sequence_n"])
    ordered["prev_arr"] = ordered.groupby("trip_id")["arr_s"].shift(1)
    backwards = ordered[ordered["arr_s"] < ordered["prev_arr"]]
    if not backwards.empty:
        issues.append(
            Issue(
                "warning",
                "time_goes_backwards",
                "Arrival time decreases as stop_sequence increases.",
                int(backwards["trip_id"].nunique()),
                _samples(backwards, ["trip_id", "stop_id", "arrival_time", "stop_sequence"]),
            )
        )
    return issues


def _check_calendar(calendar: pd.DataFrame | None, trips: pd.DataFrame | None) -> list[Issue]:
    if calendar is None or "service_id" not in calendar.columns:
        return []
    issues: list[Issue] = []
    day_columns = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if set(day_columns).issubset(calendar.columns):
        days = calendar[day_columns].apply(pd.to_numeric, errors="coerce").fillna(0)
        inactive = calendar[days.sum(axis=1) == 0]
        if not inactive.empty:
            issues.append(
                Issue(
                    "error",
                    "calendar_without_days",
                    "A calendar service is inactive on every day of the week.",
                    int(len(inactive)),
                    _samples(inactive, ["service_id", *day_columns]),
                )
            )
    if {"start_date", "end_date"}.issubset(calendar.columns) and len(calendar) > 0:
        starts = calendar["start_date"].astype(str)
        ends = calendar["end_date"].astype(str)
        inverted = calendar[starts > ends]
        if not inverted.empty:
            issues.append(
                Issue(
                    "error",
                    "calendar_dates_inverted",
                    "calendar.txt has a start_date after end_date.",
                    int(len(inverted)),
                    _samples(inverted, ["service_id", "start_date", "end_date"]),
                )
            )
        if starts.nunique() == 1 and ends.nunique() == 1:
            start = str(starts.iloc[0])
            end = str(ends.iloc[0])
            try:
                span = (pd.to_datetime(end, format="%Y%m%d") - pd.to_datetime(start, format="%Y%m%d")).days
            except ValueError:
                span = None
            if span is not None and span <= 62:
                issues.append(
                    Issue(
                        "warning",
                        "calendar_window_looks_like_generator_default",
                        (
                            f"Every service shares the window {start} to {end} ({span} days). "
                            "The KentKart converter writes the generation date and a date 60 days later. "
                            "Treat this as a feed stamp, not as the operator's published validity period, "
                            "until it is compared with an official timetable."
                        ),
                        int(len(calendar)),
                        [{"start_date": start, "end_date": end, "span_days": int(span)}],
                    )
                )
    if trips is not None and calendar is not None:
        # Presence of distinct weekday and weekend services is informational, not a defect.
        pass
    return issues


def _check_known_absences(tables: dict[str, pd.DataFrame | None]) -> list[Issue]:
    issues: list[Issue] = []
    if tables.get("calendar.txt") is not None:
        issues.append(
            Issue(
                "warning",
                "holiday_exceptions_absent",
                "calendar_dates.txt is not in this feed. Public holidays inside the stamped window are not encoded.",
                1,
            )
        )
    issues.append(
        Issue(
            "info",
            "transfers_table_absent",
            "transfers.txt is not in this feed. Phase 1 does not infer transfer rules.",
            1,
        )
    )
    issues.append(
        Issue(
            "info",
            "wheelchair_fields_absent",
            "This feed has no wheelchair_boarding or wheelchair_accessible fields. Step-free access cannot be assessed.",
            1,
        )
    )
    return issues
