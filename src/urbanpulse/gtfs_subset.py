"""Build the GTFS subset that the Wednesday-morning routing run is allowed to see.

Routes come from the Phase 1 register. A departure that is earlier than the
arrival by at most 60 seconds is set equal to the arrival. A larger reversal
is removed rather than repaired.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from urbanpulse.gtfs_time import gtfs_time_to_seconds
from urbanpulse.paths import raw_gtfs_dir, route_register_path

WINDOW_START_SECONDS = 7 * 3600
WINDOW_END_SECONDS = 9 * 3600
SERVICE_ID = "service_1_MTWTFss"


def _read_table(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(directory / name, dtype=str, keep_default_na=False)


def included_route_ids(register_path: Path | None = None) -> set[str]:
    frame = pd.read_csv(register_path or route_register_path(), dtype=str, keep_default_na=False)
    included = frame["include_in_routing"].str.lower().eq("true")
    return set(frame.loc[included, "route_id"])


def repair_stop_times(stop_times: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Clamp small reversals and quarantine large ones. The input is not modified."""
    frame = stop_times.copy()
    arrival = frame["arrival_time"].map(gtfs_time_to_seconds)
    departure = frame["departure_time"].map(gtfs_time_to_seconds)
    reversal = departure - arrival
    small = reversal.notna() & (reversal < 0) & (reversal >= -60)
    large = reversal.notna() & (reversal < -60)
    frame.loc[small, "departure_time"] = frame.loc[small, "arrival_time"]
    kept = frame.loc[~large].copy()
    return kept, {"clamped_within_60s": int(small.sum()), "quarantined_over_60s": int(large.sum())}


def departures_in_window(stop_times: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Count included Wednesday departures from 07:00 inclusive to 09:00 exclusive."""
    service_trips = trips.loc[trips["service_id"] == SERVICE_ID, ["trip_id", "route_id"]]
    merged = stop_times.merge(service_trips, on="trip_id", how="inner")
    seconds = merged["departure_time"].map(gtfs_time_to_seconds)
    window = seconds.notna() & (seconds >= WINDOW_START_SECONDS) & (seconds < WINDOW_END_SECONDS)
    selected = merged.loc[window]
    if selected.empty:
        return pd.DataFrame(columns=["stop_id", "departures_in_window", "departures_per_hour", "distinct_routes"])
    grouped = selected.groupby("stop_id", as_index=False).agg(
        departures_in_window=("trip_id", "size"),
        distinct_routes=("route_id", "nunique"),
    )
    grouped["departures_per_hour"] = grouped["departures_in_window"] / 2
    return grouped


def write_routing_gtfs(target_zip: Path, notes_path: Path) -> dict:
    """Write the included-route feed and return the cleaning notes."""
    directory = raw_gtfs_dir()
    route_ids = included_route_ids()
    routes = _read_table(directory, "routes.txt")
    routes = routes[routes["route_id"].isin(route_ids)]
    trips = _read_table(directory, "trips.txt")
    trips = trips[trips["route_id"].isin(set(routes["route_id"]))]
    stop_times = _read_table(directory, "stop_times.txt")
    stop_times = stop_times[stop_times["trip_id"].isin(set(trips["trip_id"]))]
    stop_times, repair = repair_stop_times(stop_times)
    counts = stop_times.groupby("trip_id").size()
    kept_trips = set(counts[counts >= 2].index)
    dropped_trips = int((~trips["trip_id"].isin(kept_trips)).sum())
    stop_times = stop_times[stop_times["trip_id"].isin(kept_trips)]
    trips = trips[trips["trip_id"].isin(kept_trips)]
    stops = _read_table(directory, "stops.txt")
    stops = stops[stops["stop_id"].isin(set(stop_times["stop_id"]))]
    shapes = _read_table(directory, "shapes.txt")
    shapes = shapes[shapes["shape_id"].isin(set(trips["shape_id"]))]
    agencies = _read_table(directory, "agency.txt")
    agencies = agencies[agencies["agency_id"].isin(set(routes["agency_id"]))]
    calendar = _read_table(directory, "calendar.txt")
    tables = {
        "agency.txt": agencies,
        "calendar.txt": calendar,
        "routes.txt": routes,
        "trips.txt": trips,
        "stops.txt": stops,
        "stop_times.txt": stop_times,
        "shapes.txt": shapes,
    }
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, frame in tables.items():
            archive.writestr(name, frame.to_csv(index=False, lineterminator="\n"))
    frequency = departures_in_window(stop_times, trips)
    notes = {
        "service_id": SERVICE_ID,
        "window": "07:00:00 inclusive to 09:00:00 exclusive",
        "included_routes": int(routes["route_id"].nunique()),
        "trips_dropped_under_2_stops": dropped_trips,
        **repair,
        "access_stops_with_a_departure": int(len(frequency)),
    }
    notes_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")
    frequency.to_csv(target_zip.with_name("access_stop_frequency.csv"), index=False)
    return notes
