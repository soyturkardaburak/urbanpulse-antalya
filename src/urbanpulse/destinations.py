"""Freeze the hospital destination register from the reviewed OSM extract.

Every amenity=hospital feature in the extract is listed. Inclusion is a
reviewed decision, written down here, not a silent filter. A name that is
not in this table stops the command.
"""

from __future__ import annotations

import csv
import json
import re

from shapely.geometry import shape

from urbanpulse.paths import destinations_path, hospital_geojsonseq_path, osm_extract_catalog_path

# Exact OpenStreetMap name -> (include, reason). None covers unnamed features.
REVIEW: dict[str | None, tuple[bool, str]] = {
    "Medical Park": (True, "Major private general hospital. The extract has a point and no building polygon."),
    "Antalya Eğitim Ve Araştırma Hastanesi": (True, "Major public training and research hospital."),
    "Antalya Atatürk Devlet Hastanesi": (True, "Major public hospital."),
    "Akdeniz Üniversitesi Hastanesi": (True, "Major public university hospital."),
    "Özel Antalya Anadolu Hastanesi": (True, "Major private general hospital."),
    "Kepez Devlet Hastanesi": (True, "Major public hospital operated by the Ministry of Health."),
    "Memorial Antalya Hastanesi": (True, "Major private general hospital."),
    "Medstar Antalya Hastanesi": (True, "Major private general hospital. The Topçular site is a separate row."),
    "Antalya Şehir Hastanesi": (True, "Major public city hospital operated by the Ministry of Health."),
    "Dünya Göz": (False, "Eye specialty, not a general hospital."),
    "Çallı Meydan Tıp Merkezi": (False, "Named as a medical centre, not a general hospital."),
    "Aşır Aksu Ek Hizmet Binası": (False, "Annex of another facility, not a separate general hospital."),
    "Eye Surgery Antalya": (False, "Eye specialty, not a general hospital."),
    "cinsel saglık hattı merkezi": (False, "Specialty clinic, not a general hospital."),
    "MedStar Topçular Hastanesi": (False, "Separate smaller site. Medstar Antalya is the included hospital."),
    "Uncalı Meydan Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Ofm Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Olimpos Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Rich Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Yaşam Hastanesi, Antalya (Life Hospital)": (
        False,
        "Smaller private hospital, outside the reviewed general-hospital set.",
    ),
    "Özel Lara Anadolu Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Land of Smile": (False, "Tagged healthcare=dentist."),
    "ASV Yaşam Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Opera Yaşam Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Akdeniz Şifa Hastanesi": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Özel Vitale Kadın ve Doğum Hastanesi": (False, "Specialty hospital for women and birth, not a general hospital."),
    "ÖZEL TERMESSOS HASTANESİ": (False, "Smaller private hospital, outside the reviewed general-hospital set."),
    "Atatürk Devlet Hastanesi Semt Polikliniği": (False, "Outpatient annex, not the main public hospital."),
    "Agagsia Dental Clinic": (False, "Dental clinic, not a general hospital."),
    None: (False, "Unnamed amenity=hospital feature."),
}


def review_hospital(name: str | None) -> tuple[bool, str]:
    key = name.strip() if isinstance(name, str) else None
    if key == "":
        key = None
    try:
        include, reason = REVIEW[key]
    except KeyError as exc:
        raise KeyError(f"Hospital name is not in the reviewed list: {name}") from exc
    return include, reason


def load_hospital_features(path=None) -> list[dict]:
    source = hospital_geojsonseq_path() if path is None else path
    features = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        geometry = shape(obj["geometry"])
        properties = obj.get("properties") or {}
        name = properties.get("name")
        features.append(
            {
                "name": name.strip() if isinstance(name, str) and name.strip() else None,
                "healthcare": properties.get("healthcare") or "",
                "source_id": str(obj.get("id") or ""),
                "geometry": geometry,
            }
        )
    return features


def prefer_area_geometries(features: list[dict]) -> list[dict]:
    """Keep a building polygon when the same named place also has a point."""
    names_with_area = {
        feature["name"]
        for feature in features
        if feature["name"] and feature["geometry"].geom_type in {"Polygon", "MultiPolygon"}
    }
    kept = []
    for feature in features:
        if feature["name"] in names_with_area and feature["geometry"].geom_type not in {"Polygon", "MultiPolygon"}:
            continue
        kept.append(feature)
    return kept


def osm_extract_bounds() -> tuple[float, float, float, float]:
    text = osm_extract_catalog_path().read_text(encoding="utf-8")
    catalog = json.loads(text)
    match = re.search(
        r"\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)",
        catalog.get("geographic_coverage", ""),
    )
    if match is None:
        raise ValueError("The OSM extract catalog has no bounding box.")
    min_lon, min_lat, max_lon, max_lat = (float(part) for part in match.groups())
    return min_lon, min_lat, max_lon, max_lat


def routing_point(geometry):
    if geometry.geom_type == "Point":
        return geometry, "osm_node"
    return geometry.representative_point(), "representative_point"


def write_destinations() -> list[dict]:
    features = prefer_area_geometries(load_hospital_features())
    for feature in features:
        review_hospital(feature["name"])
    min_lon, min_lat, max_lon, max_lat = osm_extract_bounds()
    rows = []
    for feature in sorted(features, key=lambda item: (item["source_id"], item["name"] or "")):
        include, reason = review_hospital(feature["name"])
        point, point_method = routing_point(feature["geometry"])
        inside = min_lon <= point.x <= max_lon and min_lat <= point.y <= max_lat
        rows.append(
            {
                "destination_id": f"H{len(rows) + 1:03d}",
                "name": feature["name"] or "",
                "category": "healthcare",
                "include_in_routing": "true" if include else "false",
                "reason": reason,
                "source": "openstreetmap amenity=hospital",
                "source_id": feature["source_id"],
                "longitude": f"{point.x:.6f}",
                "latitude": f"{point.y:.6f}",
                "geometry_type": feature["geometry"].geom_type,
                "point_method": point_method,
                "inside_osm_extract": "true" if inside else "false",
            }
        )
    fieldnames = list(rows[0]) if rows else []
    with destinations_path().open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    included = [row for row in rows if row["include_in_routing"] == "true"]
    outside = [row["name"] for row in included if row["inside_osm_extract"] != "true"]
    if not included:
        raise ValueError("The destination register has no included hospital.")
    if outside:
        raise ValueError("Included hospitals fall outside the OSM extract: " + ", ".join(outside))
    return rows
