from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape

from urbanpulse.catalog import OSM_MD5_URL, OSM_PBF_URL, sha256_file, stream_download
from urbanpulse.paths import catalog_dir, project_root, study_area_geojson_path, study_osm_path, turkey_pbf_path

BBOX_MARGIN_DEGREES = 0.04


def download_turkey_osm(force: bool = False) -> dict:
    destination = turkey_pbf_path()
    catalog_path = catalog_dir() / "turkey_osm.json"
    if destination.exists() and not force and catalog_path.exists():
        return json.loads(catalog_path.read_text(encoding="utf-8"))

    stream_download(OSM_PBF_URL, destination)
    md5_path = destination.with_name(destination.name + ".md5")
    stream_download(OSM_MD5_URL, md5_path)
    expected = md5_path.read_text(encoding="utf-8").split()[0].strip()
    digest = hashlib.md5()
    with destination.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if expected != actual:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Geofabrik MD5 did not match the downloaded Turkey PBF. The file was deleted.")

    # Hash the large file with sha256 as the project pin. MD5 is only the publisher check.
    catalog = {
        "id": "geofabrik-turkey-latest",
        "name": "OpenStreetMap Turkey extract",
        "organization": "Geofabrik, © OpenStreetMap contributors",
        "source_url": OSM_PBF_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license": "ODbL 1.0. Maps and derived databases must credit OpenStreetMap contributors.",
        "format": "OSM PBF",
        "content_sha256": sha256_file(destination),
        "geofabrik_md5": expected,
        "byte_size": destination.stat().st_size,
        "geographic_coverage": "Turkey, as published by Geofabrik. Not limited to Antalya.",
        "processing_notes": "Downloaded for cropping. The full country file is not loaded into PostGIS.",
        "limitations": [
            "Pedestrian tagging in Antalya is uneven, so later walk times will be modelled network times.",
            "This file changes when Geofabrik republishes turkey-latest.",
        ],
    }
    catalog_dir().mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return catalog


def crop_study_osm() -> dict:
    if not turkey_pbf_path().exists():
        raise RuntimeError("Turkey PBF is missing. Run urbanpulse data download-osm first.")
    if not study_area_geojson_path().exists():
        raise RuntimeError("Study area is missing. Run urbanpulse data validate first.")

    document = json.loads(study_area_geojson_path().read_text(encoding="utf-8"))
    bounds = shape(document["features"][0]["geometry"]).bounds
    min_lon, min_lat, max_lon, max_lat = bounds
    expanded = (
        min_lon - BBOX_MARGIN_DEGREES,
        min_lat - BBOX_MARGIN_DEGREES,
        max_lon + BBOX_MARGIN_DEGREES,
        max_lat + BBOX_MARGIN_DEGREES,
    )
    root = project_root()
    subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "urbanpulse-osmium",
            "-f",
            str(root / "docker" / "osmium.Dockerfile"),
            str(root / "docker"),
        ],
        check=True,
    )
    osm_dir = turkey_pbf_path().parent.resolve()
    study_osm_path().parent.mkdir(parents=True, exist_ok=True)
    if study_osm_path().exists():
        study_osm_path().unlink()
    bbox = ",".join(f"{value:.6f}" for value in expanded)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{osm_dir}:/data",
            "urbanpulse-osmium",
            "extract",
            "-b",
            bbox,
            "--strategy",
            "complete_ways",
            "-o",
            "/data/antalya-study.osm.pbf",
            "/data/turkey-latest.osm.pbf",
        ],
        check=True,
    )
    parent = json.loads((catalog_dir() / "turkey_osm.json").read_text(encoding="utf-8"))
    catalog = {
        "id": "antalya-study-osm",
        "name": "OpenStreetMap extract for the Antalya study area",
        "organization": "Cropped from Geofabrik Turkey; © OpenStreetMap contributors",
        "source_url": OSM_PBF_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license": "ODbL 1.0",
        "format": "OSM PBF",
        "content_sha256": sha256_file(study_osm_path()),
        "byte_size": study_osm_path().stat().st_size,
        "parent_sha256": parent.get("content_sha256"),
        "geographic_coverage": (
            "Bounding box of the Phase 1 study area expanded by "
            f"{BBOX_MARGIN_DEGREES} degrees on each side ({bbox}). "
            "This is not the full GTFS extent and not Antalya Province."
        ),
        "processing_notes": "osmium extract with strategy complete_ways, via the urbanpulse-osmium image.",
        "limitations": [
            "The crop is for later pedestrian routing inside the study area.",
            "Ways that only touch the provincial GTFS fringe outside this box are absent.",
        ],
    }
    path = catalog_dir() / "antalya_osm_extract.json"
    path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return catalog
