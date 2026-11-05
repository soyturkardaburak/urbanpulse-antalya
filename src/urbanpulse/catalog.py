from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from urbanpulse.paths import catalog_dir, gtfs_catalog_path, raw_gtfs_dir, raw_gtfs_zip

GTFS_URL = "https://github.com/Egezenn/kk-gtfs/raw/main/data/antalya.zip"
GTFS_REPOSITORY = "https://github.com/Egezenn/kk-gtfs"
OSM_PBF_URL = "https://download.geofabrik.de/europe/turkey-latest.osm.pbf"
OSM_MD5_URL = "https://download.geofabrik.de/europe/turkey-latest.osm.pbf.md5"
USER_AGENT = "UrbanPulseAntalya/0.1 (scheduled-accessibility research; local data acquisition)"

LIMITATIONS = [
    "Derived from the public KentKart API by the community converter Egezenn/kk-gtfs. This is not an official Antalya Büyükşehir GTFS publication.",
    "The converter stamps calendar.txt from the generation date to 60 days later. That window is not evidence of the operator's validity period.",
    "Intermediate stop times are the API's arrival and departure offsets added to each trip's first departure. They are scheduled inputs, not observed running times. A spot-check of 20 municipal timetable patterns is stored in reports/timetable_spot_check.html.",
    "The feed's stop bbox extends beyond central Antalya, including provincial places such as Kaş and Gazipaşa. Those places are not the Phase 1 study area.",
    "Services absent from this feed, including dolmuş, are not represented.",
    "Wheelchair_boarding and wheelchair_accessible fields are absent.",
    "The converter's README says underlying schedule copyright remains with KentKart and/or the municipality, while also attaching a CC0 text to the generated files. The zip is not committed to git.",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stream_download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    temporary.replace(destination)


def load_gtfs_catalog() -> dict | None:
    if not gtfs_catalog_path().exists():
        return None
    return json.loads(gtfs_catalog_path().read_text(encoding="utf-8"))


def write_gtfs_catalog(digest: str, size: int, retrieved_at: str | None = None) -> dict:
    existing = load_gtfs_catalog() or {}
    catalog = {
        "id": "antalya-kentkart-gtfs",
        "name": "Antalya public transport GTFS (KentKart-derived)",
        "organization": "Schedule data via KentKart; GTFS conversion by Egezenn/kk-gtfs",
        "source_url": GTFS_URL,
        "converter_repository": GTFS_REPOSITORY,
        "retrieved_at": retrieved_at or existing.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
        "license": (
            "Unclear for redistribution. Converter code is MIT. A CC0 text is shipped with the generated files, "
            "and the converter README says the underlying data remain with KentKart and/or the municipal operator."
        ),
        "format": "GTFS zip",
        "content_sha256": digest,
        "byte_size": size,
        "geographic_coverage": existing.get("geographic_coverage", "Filled by urbanpulse data validate."),
        "temporal_coverage": existing.get("temporal_coverage", "Filled by urbanpulse data validate."),
        "processing_notes": existing.get("processing_notes", "Downloaded and extracted. GTFS text files are not edited in Phase 1."),
        "limitations": LIMITATIONS,
    }
    catalog_dir().mkdir(parents=True, exist_ok=True)
    gtfs_catalog_path().write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return catalog


def extract_gtfs(zip_path: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise RuntimeError(f"Refusing to extract unsafe path {member}")
        archive.extractall(destination)


def download_gtfs(force: bool = False) -> dict:
    zip_path = raw_gtfs_zip()
    catalog = load_gtfs_catalog()
    if zip_path.exists() and not force:
        digest = sha256_file(zip_path)
        if catalog and catalog.get("content_sha256") not in (None, digest):
            raise RuntimeError(
                "The local GTFS zip does not match the pinned catalog hash. "
                "Pass --force to replace it and update the pin."
            )
        if catalog and catalog.get("content_sha256") == digest and raw_gtfs_dir().joinpath("stops.txt").exists():
            return catalog
        catalog = write_gtfs_catalog(digest, zip_path.stat().st_size)
        extract_gtfs(zip_path, raw_gtfs_dir())
        return catalog

    stream_download(GTFS_URL, zip_path)
    digest = sha256_file(zip_path)
    catalog = write_gtfs_catalog(digest, zip_path.stat().st_size, datetime.now(timezone.utc).isoformat())
    extract_gtfs(zip_path, raw_gtfs_dir())
    return catalog
