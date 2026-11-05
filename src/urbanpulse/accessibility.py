"""One Wednesday-morning hospital measurement.

Travel times are written only after r5py returns them. A missing time stays
missing. The timetable spot-check decides whether those times may be read as
an accessibility result; it does not invent a replacement value.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.prepared import prep

from urbanpulse.gtfs_subset import SERVICE_ID, write_routing_gtfs
from urbanpulse.paths import (
    destinations_path,
    project_root,
    raw_gtfs_dir,
    results_dir,
    scenario_dir,
    study_area_geojson_path,
    study_osm_path,
    timetable_report_json_path,
)
from urbanpulse.timetable_check import interpretation_allowed

SCENARIO_ID = "wed_morning_20261021"
DEPARTURE = datetime(2026, 10, 21, 7, 0)
WINDOW = timedelta(hours=2)
WALK_SPEED_KMH = 4.8
MAX_JOURNEY = timedelta(minutes=120)
MAX_WALK = timedelta(minutes=30)
H3_RESOLUTION = 9


def origin_cells(geometry, resolution: int = H3_RESOLUTION) -> list[str]:
    """H3 cells whose centres lie inside the study-area polygon."""
    import h3

    raw = h3.geo_to_cells(geometry.__geo_interface__, resolution)
    prepared = prep(geometry)
    kept = []
    for cell in raw:
        lat, lon = h3.cell_to_latlng(cell)
        if prepared.covers(Point(lon, lat)):
            kept.append(cell)
    return sorted(kept)


def _study_geometry():
    collection = json.loads(study_area_geojson_path().read_text(encoding="utf-8"))
    return shape(collection["features"][0]["geometry"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_wednesday_service() -> None:
    calendar = pd.read_csv(raw_gtfs_dir() / "calendar.txt", dtype=str, keep_default_na=False)
    row = calendar.loc[calendar["service_id"] == SERVICE_ID]
    if row.empty:
        raise ValueError(f"The pinned feed has no {SERVICE_ID}.")
    service = row.iloc[0]
    if service["wednesday"] != "1":
        raise ValueError(f"{SERVICE_ID} does not run on Wednesday.")
    stamp = DEPARTURE.strftime("%Y%m%d")
    if not (service["start_date"] <= stamp <= service["end_date"]):
        raise ValueError(f"{stamp} is outside the stamped calendar window.")
    if DEPARTURE.date() != date(2026, 10, 21) or DEPARTURE.date().isoweekday() != 3:
        raise ValueError("The scenario date is not Wednesday 21 October 2026.")


def prepare_scenario() -> dict:
    """Write the routing inputs. This does not calculate travel times."""
    if not timetable_report_json_path().exists():
        raise FileNotFoundError("Run the timetable spot-check before the hospital measurement.")
    if not destinations_path().exists():
        raise FileNotFoundError("Write config/destinations.csv before the hospital measurement.")
    report = json.loads(timetable_report_json_path().read_text(encoding="utf-8"))
    wednesday = [row for row in report["patterns"] if row.get("day_code") == 3]
    allowed, reason = interpretation_allowed(wednesday)
    _require_wednesday_service()
    folder = scenario_dir()
    folder.mkdir(parents=True, exist_ok=True)
    notes = write_routing_gtfs(folder / "gtfs.zip", folder / "gtfs_notes.json")
    geometry = _study_geometry()
    cells = origin_cells(geometry)
    if not 200 <= len(cells) <= 10000:
        raise ValueError(f"Origin cell count {len(cells)} is outside the expected range for resolution 9.")
    _write_origins(folder / "origins.geojson", cells)
    _write_access_stops(folder)
    _write_hospitals(folder / "hospitals.geojson")
    scenario = {
        "scenario_id": SCENARIO_ID,
        "departure": DEPARTURE.isoformat(),
        "departure_time_window_minutes": int(WINDOW.total_seconds() // 60),
        "weekday_service_id": SERVICE_ID,
        "speed_walking_kmh": WALK_SPEED_KMH,
        "max_time_minutes": int(MAX_JOURNEY.total_seconds() // 60),
        "max_time_walking_minutes": int(MAX_WALK.total_seconds() // 60),
        "percentiles": [50],
        "transport_modes": ["TRANSIT"],
        "access_modes": ["WALK"],
        "egress_modes": ["WALK"],
        "snap_to_network": True,
        "walk_departure_time_window_minutes": 5,
        "walk_window_note": (
            "r5py warns when a departure window is under 5 minutes. "
            "The walk-to-stop search uses 5 minutes. Walking time is not a timetable lookup."
        ),
        "h3_resolution": H3_RESOLUTION,
        "origins": len(cells),
        "gtfs_sha256": _sha256(folder / "gtfs.zip"),
        "destinations_sha256": _sha256(destinations_path()),
        "osm_sha256": _sha256(study_osm_path()),
        "timetable_interpretation_allowed": allowed,
        "timetable_interpretation_reason": reason,
        "gtfs_notes": notes,
        "time_basis": "scheduled_modelled",
    }
    (folder / "scenario.json").write_text(json.dumps(scenario, indent=2), encoding="utf-8")
    return scenario


def _write_origins(path: Path, cells: list[str]) -> None:
    import h3

    features = []
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"id": cell},
            }
        )
    _write_collection(path, features)


def _write_access_stops(folder: Path) -> None:
    frequency = pd.read_csv(folder / "access_stop_frequency.csv", dtype={"stop_id": str})
    stops = pd.read_csv(raw_gtfs_dir() / "stops.txt", dtype=str, keep_default_na=False)
    merged = frequency.merge(stops[["stop_id", "stop_name", "stop_lon", "stop_lat"]], on="stop_id", how="left")
    features = []
    for row in merged.itertuples(index=False):
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row.stop_lon), float(row.stop_lat)]},
                "properties": {"id": row.stop_id, "stop_name": row.stop_name},
            }
        )
    _write_collection(folder / "access_stops.geojson", features)


def _write_hospitals(path: Path) -> None:
    frame = pd.read_csv(destinations_path(), dtype=str, keep_default_na=False)
    included = frame[frame["include_in_routing"].str.lower().eq("true")]
    features = []
    for row in included.itertuples(index=False):
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row.longitude), float(row.latitude)]},
                "properties": {"id": row.destination_id, "name": row.name},
            }
        )
    if not features:
        raise ValueError("No included hospital has a routing point.")
    _write_collection(path, features)


def _write_collection(path: Path, features: list[dict]) -> None:
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )


def _points(path: Path):
    import geopandas as gpd

    collection = json.loads(path.read_text(encoding="utf-8"))
    ids = []
    longitudes = []
    latitudes = []
    labels = []
    label_column = None
    for feature in collection["features"]:
        longitude, latitude = feature["geometry"]["coordinates"]
        properties = feature["properties"]
        ids.append(str(properties["id"]))
        longitudes.append(float(longitude))
        latitudes.append(float(latitude))
        if "name" in properties:
            label_column = "name"
            labels.append(properties["name"])
        elif "stop_name" in properties:
            label_column = "stop_name"
            labels.append(properties["stop_name"])
    frame = gpd.GeoDataFrame(
        {"id": ids},
        geometry=gpd.points_from_xy(longitudes, latitudes),
        crs="EPSG:4326",
    )
    if label_column:
        frame[label_column] = labels
    return frame


def _time_column(frame: pd.DataFrame) -> str:
    for name in ("travel_time_p50", "travel_time"):
        if name in frame.columns:
            return name
    raise RuntimeError(f"r5py returned no travel-time column: {list(frame.columns)}")


def _as_minutes(value, unit: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, pd.Timedelta):
        if pd.isna(value):
            return None
        return value.total_seconds() / 60
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    number = float(value)
    if math.isnan(number) or number >= 1_000_000:
        return None
    if unit == "seconds":
        return number / 60
    return number


def _finite_numbers(series: pd.Series) -> tuple[bool, list[float]]:
    numbers = []
    saw_timedelta = False
    for value in series:
        if isinstance(value, pd.Timedelta):
            saw_timedelta = True
            if not pd.isna(value):
                numbers.append(value.total_seconds() / 60)
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            continue
        number = float(value)
        # R5 uses a large sentinel for unreachable pairs. Those are not times.
        if math.isnan(number) or number >= 1_000_000:
            continue
        numbers.append(number)
    return saw_timedelta, numbers


def _detect_unit(series: pd.Series, cap_minutes: float, label: str) -> tuple[str, dict]:
    """Decide whether r5py returned minutes or seconds.

    A seconds column is about sixty times the requested cap. Values merely
    above the cap stay minutes: the cap is not a hard ceiling on every pair.
    """
    saw_timedelta, numbers = _finite_numbers(series)
    if not numbers:
        raise RuntimeError(f"The {label} matrix has no travel times, so the unit cannot be checked.")
    ordered = sorted(numbers)
    audit = {
        "finite_pairs": len(ordered),
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "max": ordered[-1],
    }
    if saw_timedelta:
        return "timedelta", audit
    if ordered[-1] > cap_minutes * 30:
        return "seconds", audit
    return "minutes", audit


def compute_scenario() -> dict:
    """Run r5py for the prepared scenario and store the returned times."""
    import r5py

    folder = scenario_dir()
    scenario = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
    origins = _points(folder / "origins.geojson")
    stops = _points(folder / "access_stops.geojson")
    hospitals = _points(folder / "hospitals.geojson")
    print("Building the walk and transit network", flush=True)
    network = r5py.TransportNetwork(study_osm_path(), [folder / "gtfs.zip"])
    shared = {
        "departure": DEPARTURE,
        "speed_walking": WALK_SPEED_KMH,
    }
    print(f"Walk matrix: {len(origins)} origins, {len(stops)} stops", flush=True)
    walk_matrix = r5py.TravelTimeMatrix(
        network,
        origins=origins.copy(),
        destinations=stops.copy(),
        snap_to_network=True,
        departure_time_window=timedelta(minutes=5),
        transport_modes=[r5py.TransportMode.WALK],
        max_time=MAX_WALK,
        max_time_walking=MAX_WALK,
        **shared,
    )
    print(f"Hospital matrix: {len(origins)} origins, {len(hospitals)} hospitals", flush=True)
    hospital_matrix = r5py.TravelTimeMatrix(
        network,
        origins=origins.copy(),
        destinations=hospitals.copy(),
        snap_to_network=True,
        departure_time_window=WINDOW,
        percentiles=[50],
        transport_modes=[r5py.TransportMode.TRANSIT],
        access_modes=[r5py.TransportMode.WALK],
        egress_modes=[r5py.TransportMode.WALK],
        max_time=MAX_JOURNEY,
        max_time_walking=MAX_WALK,
        **shared,
    )
    walk_column = _time_column(walk_matrix)
    hospital_column = _time_column(hospital_matrix)
    walk_unit, walk_audit = _detect_unit(walk_matrix[walk_column], 30, "walk")
    hospital_unit, hospital_audit = _detect_unit(hospital_matrix[hospital_column], 120, "hospital")
    result = _assemble(origins, stops, hospitals, walk_matrix, hospital_matrix, walk_column, hospital_column, walk_unit, hospital_unit, scenario)
    result["summary"]["units"]["walk_raw"] = walk_audit
    result["summary"]["units"]["hospital_raw"] = hospital_audit
    results_dir().mkdir(parents=True, exist_ok=True)
    output = results_dir() / f"{SCENARIO_ID}.geojson"
    summary_path = results_dir() / f"{SCENARIO_ID}.summary.json"
    output.write_text(json.dumps(result["collection"], ensure_ascii=False, allow_nan=False), encoding="utf-8")
    summary_path.write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result["summary"]


def _cell_feature(cell: str, properties: dict) -> dict:
    import h3

    boundary = h3.cell_to_boundary(cell)
    ring = [(lon, lat) for lat, lon in boundary]
    ring.append(ring[0])
    return {"type": "Feature", "geometry": mapping(Polygon(ring)), "properties": properties}


def _assemble(origins, stops, hospitals, walk_matrix, hospital_matrix, walk_column, hospital_column, walk_unit, hospital_unit, scenario) -> dict:
    import h3

    frequency = pd.read_csv(scenario_dir() / "access_stop_frequency.csv", dtype={"stop_id": str})
    frequency["stop_id"] = frequency["stop_id"].astype(str)
    names = dict(zip(hospitals["id"].astype(str), hospitals["name"].astype(str), strict=True))
    stop_names = dict(zip(stops["id"].astype(str), stops["stop_name"].astype(str), strict=True))
    walk = walk_matrix.copy()
    walk["from_id"] = walk["from_id"].astype(str)
    walk["to_id"] = walk["to_id"].astype(str)
    walk["minutes"] = walk[walk_column].map(lambda value: _as_minutes(value, walk_unit))
    walk = walk.dropna(subset=["minutes"]).merge(
        frequency[["stop_id", "departures_in_window", "departures_per_hour", "distinct_routes"]],
        left_on="to_id",
        right_on="stop_id",
        how="left",
    )
    walk = walk.sort_values(
        ["from_id", "minutes", "departures_in_window", "to_id"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    nearest_stop = walk.drop_duplicates("from_id", keep="first") if not walk.empty else pd.DataFrame()
    transit = hospital_matrix.copy()
    transit["from_id"] = transit["from_id"].astype(str)
    transit["to_id"] = transit["to_id"].astype(str)
    transit["minutes"] = transit[hospital_column].map(lambda value: _as_minutes(value, hospital_unit))
    present_transit = set(transit["from_id"])
    present_walk = set(walk_matrix["from_id"].astype(str))
    reached = transit.dropna(subset=["minutes"]).sort_values(
        ["from_id", "minutes", "to_id"],
        kind="mergesort",
    )
    nearest_hospital = reached.drop_duplicates("from_id", keep="first") if not reached.empty else pd.DataFrame()
    within = (
        reached.assign(
            within_30=reached["minutes"] <= 30,
            within_45=reached["minutes"] <= 45,
            within_60=reached["minutes"] <= 60,
        )
        .groupby("from_id")[["within_30", "within_45", "within_60"]]
        .sum()
        if not reached.empty
        else pd.DataFrame()
    )
    stop_lookup = nearest_stop.set_index("from_id") if not nearest_stop.empty else pd.DataFrame()
    hospital_lookup = nearest_hospital.set_index("from_id") if not nearest_hospital.empty else pd.DataFrame()
    features = []
    for cell in origins["id"].astype(str):
        properties = {
            "h3": cell,
            "scenario_id": SCENARIO_ID,
            "time_basis": "scheduled_modelled",
            "interpretation_allowed": bool(scenario["timetable_interpretation_allowed"]),
            "walk_minutes": None,
            "access_stop_id": None,
            "access_stop_name": None,
            "departures_in_window": None,
            "departures_per_hour": None,
            "distinct_routes": None,
            "hospital_minutes_p50": None,
            "nearest_hospital_id": None,
            "nearest_hospital_name": None,
            "hospitals_within_30": None,
            "hospitals_within_45": None,
            "hospitals_within_60": None,
            "walk_row": cell in present_walk,
            "transit_row": cell in present_transit,
        }
        if cell in stop_lookup.index:
            row = stop_lookup.loc[cell]
            properties.update(
                {
                    "walk_minutes": round(float(row["minutes"]), 2),
                    "access_stop_id": str(row["to_id"]),
                    "access_stop_name": stop_names.get(str(row["to_id"])),
                    "departures_in_window": int(row["departures_in_window"]),
                    "departures_per_hour": float(row["departures_per_hour"]),
                    "distinct_routes": int(row["distinct_routes"]),
                }
            )
        if cell in present_transit:
            properties["hospitals_within_30"] = 0
            properties["hospitals_within_45"] = 0
            properties["hospitals_within_60"] = 0
        if cell in hospital_lookup.index:
            row = hospital_lookup.loc[cell]
            hospital_id = str(row["to_id"])
            properties.update(
                {
                    "hospital_minutes_p50": round(float(row["minutes"]), 2),
                    "nearest_hospital_id": hospital_id,
                    "nearest_hospital_name": names.get(hospital_id),
                }
            )
        if cell in within.index:
            counts = within.loc[cell]
            properties["hospitals_within_30"] = int(counts["within_30"])
            properties["hospitals_within_45"] = int(counts["within_45"])
            properties["hospitals_within_60"] = int(counts["within_60"])
        features.append(_cell_feature(cell, properties))
    collection = {"type": "FeatureCollection", "features": features}
    kaleici = h3.latlng_to_cell(36.8841, 30.7056, H3_RESOLUTION)
    example = next((feature["properties"] for feature in features if feature["properties"]["h3"] == kaleici), None)
    with_hospital = sum(1 for feature in features if feature["properties"]["hospital_minutes_p50"] is not None)
    summary = {
        "scenario_id": SCENARIO_ID,
        "time_basis": "scheduled_modelled",
        "computed_at": datetime.now().astimezone().isoformat(),
        "parameters": scenario,
        "units": {"walk": walk_unit, "hospital": hospital_unit, "walk_column": walk_column, "hospital_column": hospital_column},
        "origins": len(features),
        "with_walk_stop": sum(1 for feature in features if feature["properties"]["walk_minutes"] is not None),
        "with_hospital_time": with_hospital,
        "hospital_within_30": sum(1 for feature in features if (feature["properties"]["hospitals_within_30"] or 0) > 0),
        "hospital_within_45": sum(1 for feature in features if (feature["properties"]["hospitals_within_45"] or 0) > 0),
        "hospital_within_60": sum(1 for feature in features if (feature["properties"]["hospitals_within_60"] or 0) > 0),
        "walk_matrix_rows_missing": sum(1 for feature in features if not feature["properties"]["walk_row"]),
        "transit_matrix_rows_missing": sum(1 for feature in features if not feature["properties"]["transit_row"]),
        "interpretation_allowed": bool(scenario["timetable_interpretation_allowed"]),
        "interpretation_reason": scenario["timetable_interpretation_reason"],
        "kaleici_cell": kaleici,
        "kaleici_example": example,
        "null_means": "No journey was returned within the stated cutoff. The cutoff is not stored as a travel time.",
    }
    return {"collection": collection, "summary": summary}


def result_summary_path() -> Path:
    return results_dir() / f"{SCENARIO_ID}.summary.json"


def result_geojson_path() -> Path:
    return results_dir() / f"{SCENARIO_ID}.geojson"


def run_accessibility() -> dict:
    scenario = prepare_scenario()
    if _r5_is_local():
        return compute_scenario()
    _run_compute_in_docker()
    return json.loads(result_summary_path().read_text(encoding="utf-8")) | {"prepared": scenario["scenario_id"]}


def _r5_is_local() -> bool:
    import importlib.util

    return importlib.util.find_spec("r5py") is not None and shutil.which("java") is not None


def _run_compute_in_docker() -> None:
    root = project_root()
    command = [
        "docker",
        "run",
        "--rm",
        "-e",
        "URBANPULSE_ROOT=/work",
        "-e",
        "PYTHONPATH=/work/src",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-e",
        "JAVA_TOOL_OPTIONS=-Xmx4g",
        "-v",
        f"{root}:/work",
        "-v",
        "urbanpulse-r5-cache:/root/.cache",
        "urbanpulse-r5",
        "python",
        "-m",
        "urbanpulse.accessibility",
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"The r5py container exited with status {completed.returncode}.")


if __name__ == "__main__":
    compute_scenario()
