from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from shapely.geometry import Point, mapping
from shapely.prepared import prep

from urbanpulse.geometry import shape_lines
from urbanpulse.gtfs_load import load_gtfs
from urbanpulse.jsonutil import json_safe
from urbanpulse.paths import (
    gtfs_catalog_path,
    processed_dir,
    route_register_path,
    study_area_geojson_path,
    study_area_yml_path,
    validation_html_path,
    validation_json_path,
)
from urbanpulse.quality import Issue, check_feed
from urbanpulse.report_html import render_validation_html
from urbanpulse.route_register import SHARE_THRESHOLD, build_route_register, inclusion_decision
from urbanpulse.study_area import (
    area_is_sane,
    delineate_study_area,
    evaluate_control_points,
    label_detached_pockets,
    load_reference_points,
)


def _valid_stops(stops: pd.DataFrame) -> pd.DataFrame:
    frame = stops.copy()
    frame["lat"] = pd.to_numeric(frame["stop_lat"], errors="coerce")
    frame["lon"] = pd.to_numeric(frame["stop_lon"], errors="coerce")
    keep = frame["lat"].notna() & frame["lon"].notna()
    keep &= frame["lat"].abs().le(90) & frame["lon"].abs().le(180)
    keep &= ~((frame["lat"] == 0) & (frame["lon"] == 0))
    return frame.loc[keep].copy()


def _day_label(row: pd.Series) -> str:
    labels = [
        ("monday", "Monday"),
        ("tuesday", "Tuesday"),
        ("wednesday", "Wednesday"),
        ("thursday", "Thursday"),
        ("friday", "Friday"),
        ("saturday", "Saturday"),
        ("sunday", "Sunday"),
    ]
    active = [label for column, label in labels if str(row[column]) == "1"]
    return ", ".join(active) if active else "no days"


def _write_geojson(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(document), encoding="utf-8")


def run_validation(gtfs_dir: Path) -> dict:
    tables = load_gtfs(gtfs_dir)
    marker = gtfs_dir / "SYNTHETIC.txt"
    if marker.exists():
        raise RuntimeError("Refusing to validate a synthetic fixture as the Antalya feed.")

    issues = check_feed(tables)
    catalog = json.loads(gtfs_catalog_path().read_text(encoding="utf-8"))
    stops = tables["stops.txt"]
    routes = tables["routes.txt"]
    trips = tables["trips.txt"]
    stop_times = tables["stop_times.txt"]
    calendar = tables["calendar.txt"]
    agencies = tables["agency.txt"]
    if any(frame is None for frame in (stops, routes, trips, stop_times, calendar, agencies)):
        summary = _incomplete_summary(catalog, issues)
        _write_outputs(summary, None, None)
        return summary

    assert stops is not None and routes is not None and trips is not None
    assert stop_times is not None and calendar is not None and agencies is not None

    valid_stops = _valid_stops(stops)
    if valid_stops.empty:
        issues.append(Issue("error", "no_valid_stops", "No stop has a usable coordinate.", 1))
        summary = _incomplete_summary(catalog, issues)
        _write_outputs(summary, None, None)
        return summary
    try:
        study_area, metadata, core_cells = delineate_study_area(valid_stops["lon"], valid_stops["lat"])
    except ValueError as exc:
        issues.append(Issue("error", "study_area_failed", str(exc), 1))
        summary = _incomplete_summary(catalog, issues)
        _write_outputs(summary, None, None)
        return summary
    prepared = prep(study_area)
    inside_mask = [
        prepared.covers(Point(float(lon), float(lat)))
        for lon, lat in zip(valid_stops["lon"], valid_stops["lat"], strict=True)
    ]
    valid_stops = valid_stops.copy()
    valid_stops["inside_study_area"] = inside_mask
    valid_ids = set(valid_stops["stop_id"].astype(str))
    inside_ids = set(valid_stops.loc[valid_stops["inside_study_area"], "stop_id"].astype(str))

    pockets = label_detached_pockets(
        valid_stops["lon"].tolist(),
        valid_stops["lat"].tolist(),
        valid_stops["stop_name"].tolist(),
        resolution=int(metadata["parameters"]["h3_resolution"]),
        threshold=int(metadata["computed"]["threshold_stops"]),
        core_cells=core_cells,
    )
    control_points = evaluate_control_points(study_area, load_reference_points())
    area_km2 = float(metadata["computed"]["area_km2"])
    required_ok = all(point["passed"] for point in control_points if point["role"] == "required")
    sane = area_is_sane(area_km2) and required_ok
    if not area_is_sane(area_km2):
        issues.append(
            Issue(
                "error",
                "study_area_area_out_of_range",
                f"The study area is {area_km2} km², outside the expected {20}–{2000} km² guard.",
                1,
            )
        )
    failed_required = [point["name"] for point in control_points if point["role"] == "required" and not point["passed"]]
    if failed_required:
        issues.append(
            Issue(
                "error",
                "study_area_control_point_failed",
                "Required containment checks failed: " + ", ".join(failed_required) + ".",
                len(failed_required),
                [point for point in control_points if point["role"] == "required" and not point["passed"]],
            )
        )

    register = build_route_register(
        routes,
        trips,
        stop_times,
        valid_stop_ids=valid_ids,
        inside_stop_ids=inside_ids,
        agencies=agencies,
    )
    lines, malformed_shapes = shape_lines(tables["shapes.txt"])
    if malformed_shapes:
        issues.append(
            Issue(
                "warning",
                "malformed_shapes",
                "Some shape groups do not contain two distinct coordinates.",
                malformed_shapes,
            )
        )
    if "shape_id" in trips.columns:
        missing_shape = trips[~trips["shape_id"].isin(lines) | trips["shape_id"].eq("")]
        if not missing_shape.empty:
            issues.append(
                Issue(
                    "warning",
                    "trip_missing_shape",
                    "Trips have a shape_id that does not form a line.",
                    int(missing_shape["shape_id"].nunique()),
                    missing_shape[["trip_id", "route_id", "shape_id"]].head(5).to_dict(orient="records"),
                )
            )

    min_lon = float(valid_stops["lon"].min())
    min_lat = float(valid_stops["lat"].min())
    max_lon = float(valid_stops["lon"].max())
    max_lat = float(valid_stops["lat"].max())

    sensitivity = {}
    for cutoff in (0.6, 0.8, 0.9):
        included_at = 0
        for n_stops, share in zip(register["n_stops"], register["share_stops_inside"], strict=True):
            include, _reason = inclusion_decision(float(share), int(n_stops), cutoff)
            included_at += int(include)
        sensitivity[f"included_at_{cutoff:.1f}"] = included_at

    excluded = register[~register["include_in_routing"]]
    flagged = register[register["include_in_routing"] & register["quality_flags"].ne("")]
    calendar_rows = []
    trip_counts = trips["service_id"].value_counts().to_dict()
    for row in calendar.to_dict(orient="records"):
        calendar_rows.append(
            {
                **row,
                "day_label": _day_label(pd.Series(row)),
                "trip_count": int(trip_counts.get(row["service_id"], 0)),
            }
        )

    route_type_counts = {str(key): int(value) for key, value in routes["route_type"].value_counts().to_dict().items()}
    agency_rows = []
    for agency in agencies.to_dict(orient="records"):
        agency_rows.append(
            {
                "agency_id": agency["agency_id"],
                "agency_name": agency["agency_name"],
                "agency_url": agency.get("agency_url", ""),
                "agency_timezone": agency.get("agency_timezone", ""),
                "route_count": int((routes["agency_id"] == agency["agency_id"]).sum()),
            }
        )

    temporal = _temporal_coverage(calendar)
    geographic = (
        f"Feed stop bbox longitude {min_lon:.5f} to {max_lon:.5f}, "
        f"latitude {min_lat:.5f} to {max_lat:.5f}. "
        f"Phase 1 study area covers {area_km2:.1f} km² and {int(valid_stops['inside_study_area'].sum())} "
        f"of {len(valid_stops)} valid stops."
    )
    catalog.update(
        {
            "geographic_coverage": geographic,
            "temporal_coverage": temporal,
            "processing_notes": (
                "Downloaded from the published kk-gtfs zip and extracted without editing GTFS files. "
                "The study-area polygon and route register are separate project decisions."
            ),
        }
    )
    gtfs_catalog_path().write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    summary = {
        "phase": 1,
        "accessibility_calculated": False,
        "distinction": (
            "Issues below are data-quality or coverage findings. "
            "They are not measurements of public-transport accessibility."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": catalog,
        "counts": {
            "agencies": int(len(agencies)),
            "routes": int(len(routes)),
            "stops": int(len(stops)),
            "valid_stops": int(len(valid_stops)),
            "trips": int(len(trips)),
            "stop_times": int(len(stop_times)),
            "shapes": int(len(lines)),
            "calendar_services": int(len(calendar)),
        },
        "route_type_counts": route_type_counts,
        "agencies": agency_rows,
        "calendar": calendar_rows,
        "bbox": {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
        "study_area": {
            "id": "antalya_central",
            "sane": sane,
            "area_km2": area_km2,
            "stops_inside": int(valid_stops["inside_study_area"].sum()),
            "stops_outside": int((~valid_stops["inside_study_area"]).sum()),
            "parameters": metadata["parameters"],
            "computed": metadata["computed"],
            "control_points": control_points,
            "detached_pockets": pockets,
            "bounds": [float(value) for value in study_area.bounds],
        },
        "route_decisions": {
            "threshold": SHARE_THRESHOLD,
            "rule": (
                "A route is included when at least 80 percent of its valid stops "
                "lie inside the study area and it has at least two valid stops. "
                "Quality flags do not by themselves exclude a route."
            ),
            "included": int(register["include_in_routing"].sum()),
            "excluded": int((~register["include_in_routing"]).sum()),
            "sensitivity": sensitivity,
            "excluded_routes": _route_rows(excluded),
            "flagged_included_routes": _route_rows(flagged),
        },
        "issues": [issue.as_dict() for issue in issues],
    }
    summary = json_safe(summary)
    _write_outputs(summary, study_area, register, lines, routes, valid_stops)
    return summary


def _route_rows(frame: pd.DataFrame) -> list[dict]:
    columns = [
        "route_id",
        "route_short_name",
        "route_long_name",
        "agency_id",
        "route_type",
        "exclude_reason",
        "quality_flags",
        "n_stops",
        "n_stops_inside",
        "share_stops_inside",
    ]
    if frame.empty:
        return []
    ordered = frame.sort_values(["share_stops_inside", "route_short_name"])
    return json_safe(ordered[columns].to_dict(orient="records"))


def _temporal_coverage(calendar: pd.DataFrame) -> str:
    start = str(calendar["start_date"].min())
    end = str(calendar["end_date"].max())
    return (
        f"calendar.txt stamps services from {start} to {end}. "
        "This window is produced by the converter unless an official feed replaces it."
    )


def _incomplete_summary(catalog: dict, issues: list[Issue]) -> dict:
    return json_safe(
        {
            "phase": 1,
            "accessibility_calculated": False,
            "distinction": "The feed is missing required tables. Accessibility was not calculated.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": catalog,
            "counts": {},
            "calendar": [],
            "issues": [issue.as_dict() for issue in issues],
            "study_area": {
                "sane": False,
                "area_km2": None,
                "stops_inside": 0,
                "stops_outside": 0,
                "control_points": [],
            },
            "route_decisions": {
                "rule": "The route register was not built because required GTFS tables are missing.",
                "included": 0,
                "excluded": 0,
                "excluded_routes": [],
                "flagged_included_routes": [],
            },
        }
    )


def _write_outputs(summary, study_area, register, lines=None, routes=None, valid_stops=None) -> None:
    validation_json_path().parent.mkdir(parents=True, exist_ok=True)
    validation_json_path().write_text(json.dumps(summary, indent=2), encoding="utf-8")
    validation_html_path().write_text(render_validation_html(summary), encoding="utf-8")
    if study_area is None or register is None:
        return

    study_area_geojson_path().write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "antalya_central",
                            "method": "h3_density_component",
                            "feed_sha256": summary["dataset"].get("content_sha256"),
                        },
                        "geometry": mapping(study_area),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    study_area_yml_path().write_text(
        yaml.safe_dump(
            {
                "id": "antalya_central",
                "feed_sha256": summary["dataset"].get("content_sha256"),
                "sane": summary["study_area"]["sane"],
                "parameters": summary["study_area"]["parameters"],
                "computed": summary["study_area"]["computed"],
                "control_points": summary["study_area"]["control_points"],
                "detached_pockets": summary["study_area"]["detached_pockets"],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    route_register_path().parent.mkdir(parents=True, exist_ok=True)
    register.to_csv(route_register_path(), index=False)

    if lines is None or routes is None or valid_stops is None:
        return
    _export_processed_layers(valid_stops, lines, routes, register)


def _export_processed_layers(valid_stops, lines, routes, register) -> None:
    import geopandas as gpd

    del routes, register
    destination = processed_dir()
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("stops.geojson", "shapes.geojson"):
        existing = destination / name
        if existing.exists():
            existing.unlink()

    stop_frame = gpd.GeoDataFrame(
        valid_stops[["stop_id", "stop_name", "inside_study_area"]],
        geometry=gpd.points_from_xy(valid_stops["lon"], valid_stops["lat"]),
        crs="EPSG:4326",
    )
    stop_frame.to_file(destination / "stops.geojson", driver="GeoJSON")
    shape_frame = gpd.GeoDataFrame(
        [{"shape_id": shape_id} for shape_id in lines],
        geometry=list(lines.values()),
        crs="EPSG:4326",
    )
    shape_frame.to_file(destination / "shapes.geojson", driver="GeoJSON")
