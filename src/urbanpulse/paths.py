from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("URBANPULSE_ROOT")
    if env:
        return Path(env)
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "urbanpulse").exists():
            return candidate
    raise FileNotFoundError("Run UrbanPulse from the repository root.")


def raw_gtfs_zip() -> Path:
    return project_root() / "data" / "raw" / "gtfs" / "antalya.zip"


def raw_gtfs_dir() -> Path:
    return project_root() / "data" / "raw" / "gtfs" / "antalya"


def catalog_dir() -> Path:
    return project_root() / "data" / "catalog"


def processed_dir() -> Path:
    return project_root() / "data" / "processed"


def reports_dir() -> Path:
    return project_root() / "reports"


def gtfs_catalog_path() -> Path:
    return catalog_dir() / "antalya_gtfs.json"


def study_area_geojson_path() -> Path:
    return project_root() / "config" / "study_area.geojson"


def study_area_yml_path() -> Path:
    return project_root() / "config" / "study_area.yml"


def route_register_path() -> Path:
    return project_root() / "config" / "route_register.csv"


def reference_points_path() -> Path:
    return project_root() / "config" / "reference_points.yml"


def validation_json_path() -> Path:
    return reports_dir() / "phase1_data_quality.json"


def validation_html_path() -> Path:
    return reports_dir() / "phase1_data_quality.html"


def migration_path() -> Path:
    return project_root() / "db" / "migrations" / "001_phase1.sql"


def turkey_pbf_path() -> Path:
    return project_root() / "data" / "raw" / "osm" / "turkey-latest.osm.pbf"


def study_osm_path() -> Path:
    return project_root() / "data" / "raw" / "osm" / "antalya-study.osm.pbf"


def hospital_geojsonseq_path() -> Path:
    return project_root() / "data" / "raw" / "osm" / "hospitals-points-polygons.geojsonseq"


def destinations_path() -> Path:
    return project_root() / "config" / "destinations.csv"


def timetable_report_json_path() -> Path:
    return reports_dir() / "timetable_spot_check.json"


def timetable_report_csv_path() -> Path:
    return reports_dir() / "timetable_spot_check.csv"


def timetable_report_html_path() -> Path:
    return reports_dir() / "timetable_spot_check.html"


def osm_extract_catalog_path() -> Path:
    return catalog_dir() / "antalya_osm_extract.json"


def results_dir() -> Path:
    return project_root() / "data" / "results"


def scenario_dir() -> Path:
    return processed_dir() / "wed_morning_20261021"
