from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
import yaml
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import transform, unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

from urbanpulse.paths import reference_points_path

# Resolution 8 cells are about 0.74 km². That is coarse enough for a stop-density
# threshold to separate a continuous urban network from scattered highway stops.
# Phase 2 accessibility origins, if approved, use resolution 9 instead.
H3_RESOLUTION = 8
COUNT_PERCENTILE = 75
MINIMUM_STOP_COUNT = 5
BUFFER_M = 400
MIN_AREA_KM2 = 20
MAX_AREA_KM2 = 2000

_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32636", always_xy=True)
_FROM_UTM = Transformer.from_crs("EPSG:32636", "EPSG:4326", always_xy=True)


def load_reference_points() -> list[dict[str, Any]]:
    document = yaml.safe_load(reference_points_path().read_text(encoding="utf-8"))
    return list(document["points"])


def _cell_polygon(cell: str) -> Polygon:
    import h3

    boundary = h3.cell_to_boundary(cell)
    ring = [(lng, lat) for lat, lng in boundary]
    polygon = Polygon(ring)
    if not polygon.is_valid:
        polygon = make_valid(polygon)
    return polygon


def _drop_holes(geom):
    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([Polygon(part.exterior) for part in geom.geoms])
    if geom.geom_type == "GeometryCollection":
        polygons = [part for part in geom.geoms if part.geom_type in {"Polygon", "MultiPolygon"}]
        if not polygons:
            return geom
        return _drop_holes(unary_union(polygons))
    return geom


def _buffer_metres(geom, metres: float):
    projected = transform(_TO_UTM.transform, geom)
    buffered = projected.buffer(metres)
    return transform(_FROM_UTM.transform, buffered), projected.area / 1_000_000, buffered.area / 1_000_000


def _component_containing(seed: str, cells: set[str]) -> list[str]:
    import h3

    seen = {seed}
    queue: deque[str] = deque([seed])
    component: list[str] = []
    while queue:
        current = queue.popleft()
        component.append(current)
        for neighbour in h3.grid_disk(current, 1):
            if neighbour in cells and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return component


def delineate_study_area(
    longitudes,
    latitudes,
    *,
    resolution: int = H3_RESOLUTION,
    percentile: float = COUNT_PERCENTILE,
    minimum_stop_count: int = MINIMUM_STOP_COUNT,
    buffer_m: float = BUFFER_M,
) -> tuple[MultiPolygon, dict[str, Any], set[str]]:
    """Delineate the dense connected stop cluster.

    Occupied H3 cells at ``resolution`` are counted. The threshold is the
    maximum of ``minimum_stop_count`` and the ceiling of the given percentile
    of occupied-cell counts. The study area is the connected component of
    cells at or above that threshold which contains the densest cell, with
    interior holes filled and a metric buffer applied.
    """
    import h3

    counts: dict[str, int] = {}
    for lon, lat in zip(longitudes, latitudes, strict=True):
        cell = h3.latlng_to_cell(float(lat), float(lon), resolution)
        counts[cell] = counts.get(cell, 0) + 1

    if not counts:
        raise ValueError("No stops were available to delineate a study area.")

    occupied = np.array(list(counts.values()), dtype=float)
    p50 = float(np.percentile(occupied, 50))
    p75 = float(np.percentile(occupied, percentile))
    p90 = float(np.percentile(occupied, 90))
    threshold = max(minimum_stop_count, int(np.ceil(p75)))
    candidates = {cell for cell, count in counts.items() if count >= threshold}
    if not candidates:
        raise ValueError("The density threshold excluded every stop cell.")

    densest = sorted(candidates, key=lambda cell: (-counts[cell], cell))[0]
    core = _component_containing(densest, candidates)
    core_set = set(core)

    union = unary_union([_cell_polygon(cell) for cell in core])
    union = _drop_holes(union)
    buffered, area_before, area_after = _buffer_metres(union, buffer_m)
    if buffered.geom_type == "Polygon":
        study_area = MultiPolygon([buffered])
    elif buffered.geom_type == "MultiPolygon":
        study_area = buffered
    else:
        polygons = [part for part in buffered.geoms if part.geom_type == "Polygon"]
        study_area = MultiPolygon(polygons)

    metadata = {
        "method": "h3_density_component",
        "parameters": {
            "h3_resolution": resolution,
            "count_percentile": percentile,
            "minimum_stop_count": minimum_stop_count,
            "buffer_m": buffer_m,
            "hole_filling": "Interior rings of the density union are removed before the buffer.",
            "component": "Connected component, among cells at or above the threshold, that contains the densest cell.",
            "tie_break": "Highest stop count, then lexicographic H3 index.",
            "why_this_resolution": (
                "Resolution 8 is used only to delineate the dense service area. "
                "A finer cell often contains too few stops for a density threshold. "
                "This is not the Phase 2 accessibility lattice."
            ),
        },
        "computed": {
            "occupied_cells": int(len(counts)),
            "p50_stops_per_occupied_cell": round(p50, 2),
            "p75_stops_per_occupied_cell": round(p75, 2),
            "p90_stops_per_occupied_cell": round(p90, 2),
            "threshold_stops": int(threshold),
            "core_cells": int(len(core)),
            "densest_cell_stops": int(counts[densest]),
            "area_km2_before_buffer": round(area_before, 2),
            "area_km2": round(area_after, 2),
        },
    }
    return study_area, metadata, core_set


def label_detached_pockets(
    longitudes,
    latitudes,
    names: list[str],
    *,
    resolution: int,
    threshold: int,
    core_cells: set[str],
) -> list[dict[str, Any]]:
    import h3

    cell_names: dict[str, list[str]] = {}
    cell_counts: dict[str, int] = {}
    for lon, lat, name in zip(longitudes, latitudes, names, strict=True):
        cell = h3.latlng_to_cell(float(lat), float(lon), resolution)
        cell_counts[cell] = cell_counts.get(cell, 0) + 1
        if cell not in core_cells and cell_counts[cell] <= 8:
            cell_names.setdefault(cell, []).append(str(name))
        elif cell not in core_cells:
            cell_names.setdefault(cell, [])
            if len(cell_names[cell]) < 8:
                cell_names[cell].append(str(name))

    detached = {cell for cell, count in cell_counts.items() if count >= threshold and cell not in core_cells}
    remaining = set(detached)
    pockets: list[dict[str, Any]] = []
    while remaining:
        seed = sorted(remaining)[0]
        component = _component_containing(seed, remaining)
        remaining -= set(component)
        examples: list[str] = []
        for cell in component:
            for name in cell_names.get(cell, []):
                if name not in examples:
                    examples.append(name)
                if len(examples) >= 8:
                    break
            if len(examples) >= 8:
                break
        pockets.append(
            {
                "h3_cells": len(component),
                "stop_count": int(sum(cell_counts[cell] for cell in component)),
                "example_stop_names": examples,
            }
        )
    pockets.sort(key=lambda pocket: (-int(pocket["stop_count"]), -int(pocket["h3_cells"])))
    return pockets


def evaluate_control_points(study_area, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = prep(study_area)
    results = []
    for point in points:
        inside = bool(prepared.covers(Point(float(point["lon"]), float(point["lat"]))))
        expected_inside = point["expect"] == "inside"
        results.append(
            {
                "id": point["id"],
                "name": point["name"],
                "lon": float(point["lon"]),
                "lat": float(point["lat"]),
                "expect": point["expect"],
                "role": point["role"],
                "note": point.get("note", ""),
                "inside": inside,
                "passed": inside == expected_inside,
            }
        )
    return results


def as_multipolygon(geom) -> MultiPolygon:
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    polygons = []
    for part in getattr(geom, "geoms", []):
        if part.geom_type == "Polygon":
            polygons.append(part)
        elif part.geom_type == "MultiPolygon":
            polygons.extend(part.geoms)
    if not polygons:
        raise ValueError(f"Study area geometry is {geom.geom_type}.")
    return MultiPolygon(polygons)


def area_is_sane(area_km2: float) -> bool:
    return MIN_AREA_KM2 <= area_km2 <= MAX_AREA_KM2
