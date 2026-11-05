from __future__ import annotations

import pandas as pd
from shapely.geometry import LineString


def shape_lines(shapes: pd.DataFrame | None) -> tuple[dict[str, LineString], int]:
    """Build route lines and count shape groups that do not form a line."""
    if shapes is None or shapes.empty:
        return {}, 0
    required = {"shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"}
    if not required.issubset(shapes.columns):
        return {}, 0

    frame = shapes.copy()
    frame["shape_pt_lat"] = pd.to_numeric(frame["shape_pt_lat"], errors="coerce")
    frame["shape_pt_lon"] = pd.to_numeric(frame["shape_pt_lon"], errors="coerce")
    frame["shape_pt_sequence"] = pd.to_numeric(frame["shape_pt_sequence"], errors="coerce")
    malformed = 0
    lines: dict[str, LineString] = {}
    for shape_id, part in frame.groupby("shape_id", sort=False):
        part = part.dropna(subset=["shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
        part = part.sort_values("shape_pt_sequence")
        coordinates: list[tuple[float, float]] = []
        for lon, lat in zip(part["shape_pt_lon"], part["shape_pt_lat"], strict=True):
            point = (float(lon), float(lat))
            if not coordinates or point != coordinates[-1]:
                coordinates.append(point)
        if len(coordinates) < 2:
            malformed += 1
            continue
        lines[str(shape_id)] = LineString(coordinates)
    return lines, malformed
