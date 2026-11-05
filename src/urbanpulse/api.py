from __future__ import annotations

import json
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from urbanpulse import __version__
from urbanpulse.accessibility import result_geojson_path, result_summary_path
from urbanpulse.db import connect
from urbanpulse.paths import timetable_report_json_path
from urbanpulse.rider import list_routes, list_stops, live_route, live_stop, route_detail, stop_board
from urbanpulse.planner import plan_journey

app = FastAPI(title="UrbanPulse Antalya", version=__version__)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8080"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _query_one(sql: str, params: tuple = ()):
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def _database_error(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "PostGIS is not reachable or Phase 1 has not been imported.", "error": exc.__class__.__name__},
    )


@app.get("/api/health")
def health():
    try:
        _query_one("SELECT PostGIS_Version()")
    except Exception as exc:
        return _database_error(exc)
    return {"status": "ok", "phase": 1, "accessibility_calculated": result_summary_path().exists()}


@app.get("/api/overview")
def overview():
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT body FROM phase1_summary WHERE id = 'current'")
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(status_code=503, detail="Phase 1 data has not been imported.")
                body = row[0]
                if isinstance(body, str):
                    body = json.loads(body)
                cursor.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM agencies) AS agencies,
                        (SELECT count(*) FROM routes) AS routes,
                        (SELECT count(*) FROM stops) AS stops,
                        (SELECT count(*) FROM trips) AS trips,
                        (SELECT count(*) FROM stop_times) AS stop_times,
                        (SELECT count(*) FROM shapes) AS shapes
                    """
                )
                live = cursor.fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        return _database_error(exc)

    payload = dict(body)
    payload["served_from"] = "postgis"
    payload["database_counts"] = {
        "agencies": live[0],
        "routes": live[1],
        "stops": live[2],
        "trips": live[3],
        "stop_times": live[4],
        "shapes": live[5],
    }
    expected = payload.get("counts") or {}
    mismatches = []
    for key in ("routes", "stops", "trips"):
        if key in expected and int(expected[key]) != int(payload["database_counts"][key]):
            mismatches.append(key)
    payload["consistency_warning"] = (
        "Imported row counts differ from the validation summary for: " + ", ".join(mismatches)
        if mismatches
        else None
    )
    return payload


def _read_result_json(path):
    if not path.exists():
        raise HTTPException(status_code=404, detail="That result has not been computed.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/timetable-check")
def timetable_check():
    return _read_result_json(timetable_report_json_path())


@app.get("/api/accessibility/summary")
def accessibility_summary():
    return _read_result_json(result_summary_path())


@app.get("/api/accessibility/cells")
def accessibility_cells():
    loaded = _accessibility_cells()
    return loaded["collection"]


@app.get("/api/accessibility/cells/{cell_id}")
def accessibility_cell(cell_id: str):
    if not cell_id.isalnum():
        raise HTTPException(status_code=404, detail="Cell not found in the stored result.")
    loaded = _accessibility_cells()
    properties = loaded["cells"].get(cell_id)
    if properties is None:
        raise HTTPException(status_code=404, detail="Cell not found in the stored result.")
    return properties


_ACCESS_CACHE: dict = {"mtime": None, "cells": {}, "collection": None}


def _accessibility_cells() -> dict:
    path = result_geojson_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="The Wednesday-morning hospital measurement has not been run.")
    mtime = path.stat().st_mtime
    if _ACCESS_CACHE["mtime"] != mtime:
        collection = json.loads(path.read_text(encoding="utf-8"))
        _ACCESS_CACHE["collection"] = collection
        _ACCESS_CACHE["cells"] = {
            feature["properties"]["h3"]: feature["properties"] for feature in collection.get("features", [])
        }
        _ACCESS_CACHE["mtime"] = mtime
    return _ACCESS_CACHE


@app.get("/api/transit/plan")
def transit_plan(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    if not (-90 <= from_lat <= 90 and -90 <= to_lat <= 90 and -180 <= from_lon <= 180 and -180 <= to_lon <= 180):
        raise HTTPException(status_code=400, detail="Those coordinates are not on the map.")
    try:
        return plan_journey(from_lat, from_lon, to_lat, to_lon)
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/transit/routes")
def transit_routes():
    try:
        return list_routes()
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/transit/stops")
def transit_stops():
    try:
        return list_stops()
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/transit/routes/{route_id}")
def transit_route(route_id: str):
    try:
        detail = route_detail(route_id)
    except Exception as exc:
        return _database_error(exc)
    if detail is None:
        raise HTTPException(status_code=404, detail="That line is not in the stored schedule.")
    return detail


@app.get("/api/transit/stops/{stop_id}")
def transit_stop(stop_id: str):
    try:
        board = stop_board(stop_id)
    except Exception as exc:
        return _database_error(exc)
    if board is None:
        raise HTTPException(status_code=404, detail="That stop is not in the stored schedule.")
    return board


@app.get("/api/transit/live/routes/{route_id}")
def transit_live_route(route_id: str):
    try:
        payload = live_route(route_id)
    except Exception as exc:
        return _database_error(exc)
    if payload is None:
        raise HTTPException(status_code=404, detail="That line is not in the stored schedule.")
    return payload


@app.get("/api/transit/live/stops/{stop_id}")
def transit_live_stop(stop_id: str):
    try:
        payload = live_stop(stop_id)
    except Exception as exc:
        return _database_error(exc)
    if payload is None:
        raise HTTPException(status_code=404, detail="That stop is not in the stored schedule.")
    return payload


@app.get("/api/map/stops")
def map_stops():
    try:
        return _cached_feature_collection("stops")
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/map/routes")
def map_routes():
    try:
        return _cached_feature_collection("routes")
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/map/study-area")
def map_study_area():
    try:
        return _cached_feature_collection("study-area")
    except Exception as exc:
        return _database_error(exc)


@app.get("/api/stops/{stop_id}")
def stop_detail(stop_id: str):
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT stop_id, stop_name, stop_lat, stop_lon, inside_study_area
                    FROM stops
                    WHERE stop_id = %s
                    """,
                    (stop_id,),
                )
                stop = cursor.fetchone()
                if stop is None:
                    raise HTTPException(status_code=404, detail="Stop not found in the imported feed.")
                cursor.execute(
                    """
                    SELECT DISTINCT r.route_id, r.route_short_name, r.route_long_name,
                                    r.include_in_routing, r.exclude_reason, r.quality_flags, r.route_type
                    FROM stop_times st
                    JOIN trips t ON t.trip_id = st.trip_id
                    JOIN routes r ON r.route_id = t.route_id
                    WHERE st.stop_id = %s
                    ORDER BY r.route_short_name, r.route_id
                    """,
                    (stop_id,),
                )
                routes = cursor.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        return _database_error(exc)
    return {
        "stop_id": stop[0],
        "stop_name": stop[1],
        "latitude": stop[2],
        "longitude": stop[3],
        "inside_study_area": stop[4],
        "routes": [
            {
                "route_id": route[0],
                "route_short_name": route[1],
                "route_long_name": route[2],
                "include_in_routing": route[3],
                "exclude_reason": route[4],
                "quality_flags": route[5],
                "route_type": route[6],
            }
            for route in routes
        ],
    }


def _cached_feature_collection(name: str) -> dict:
    return _load_feature_collection(name)


@lru_cache(maxsize=4)
def _load_feature_collection(name: str) -> dict:
    if name == "stops":
        sql = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(geom)::json,
                        'properties', json_build_object(
                            'stop_id', stop_id,
                            'stop_name', stop_name,
                            'inside_study_area', inside_study_area
                        )
                    )
                ), '[]'::json)
            )
            FROM stops
        """
    elif name == "routes":
        sql = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(feature), '[]'::json)
            )
            FROM (
                SELECT DISTINCT ON (r.route_id, t.shape_id)
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(s.geom)::json,
                        'properties', json_build_object(
                            'route_id', r.route_id,
                            'route_short_name', r.route_short_name,
                            'route_long_name', r.route_long_name,
                            'include_in_routing', r.include_in_routing,
                            'exclude_reason', r.exclude_reason,
                            'quality_flags', r.quality_flags,
                            'shape_id', t.shape_id
                        )
                    ) AS feature
                FROM routes r
                JOIN trips t ON t.route_id = r.route_id
                JOIN shapes s ON s.shape_id = t.shape_id
                ORDER BY r.route_id, t.shape_id
            ) features
        """
    elif name == "study-area":
        sql = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(geom)::json,
                        'properties', json_build_object('id', id, 'method', method)
                    )
                ), '[]'::json)
            )
            FROM study_area
        """
    else:
        raise HTTPException(status_code=404, detail="Unknown map layer.")
    row = _query_one(sql)
    if row is None or row[0] is None:
        return {"type": "FeatureCollection", "features": []}
    return row[0]
