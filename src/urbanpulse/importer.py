from __future__ import annotations

import json
from collections.abc import Callable

import pandas as pd
import psycopg
from shapely.geometry import Point, shape
from shapely.prepared import prep

from urbanpulse.db import apply_migration, connect
from urbanpulse.geometry import shape_lines
from urbanpulse.gtfs_load import load_gtfs
from urbanpulse.paths import (
    gtfs_catalog_path,
    migration_path,
    raw_gtfs_dir,
    route_register_path,
    study_area_geojson_path,
    validation_json_path,
)


def import_phase1(echo: Callable[[str], None] = print) -> None:
    for path in (gtfs_catalog_path(), route_register_path(), study_area_geojson_path(), validation_json_path()):
        if not path.exists():
            raise RuntimeError(f"Missing {path}. Run urbanpulse data download and urbanpulse data validate first.")

    summary = json.loads(validation_json_path().read_text(encoding="utf-8"))
    if not summary.get("study_area", {}).get("sane"):
        raise RuntimeError("Refusing to import because the study area did not pass its checks.")

    tables = load_gtfs(raw_gtfs_dir())
    register = pd.read_csv(route_register_path(), dtype=str, keep_default_na=False)
    study_document = json.loads(study_area_geojson_path().read_text(encoding="utf-8"))
    study_geom = shape(study_document["features"][0]["geometry"])
    catalog = json.loads(gtfs_catalog_path().read_text(encoding="utf-8"))

    echo("Connecting to PostGIS and loading the Phase 1 schema.")
    with connect() as connection:
        apply_migration(connection, migration_path().read_text(encoding="utf-8"))
        _insert_dataset(connection, catalog)
        _insert_agencies(connection, tables["agency.txt"])
        inserted_routes = _insert_routes(connection, tables["routes.txt"], register)
        _insert_calendar(connection, tables["calendar.txt"])
        inserted_stops = _insert_stops(connection, tables["stops.txt"], study_geom)
        lines, _malformed = shape_lines(tables["shapes.txt"])
        _insert_shapes(connection, lines)
        inserted_trips = _insert_trips(connection, tables["trips.txt"], inserted_routes)
        echo(f"Copying stop_times ({len(tables['stop_times.txt'])} rows before filtering).")
        copied = _copy_stop_times(connection, tables["stop_times.txt"], inserted_trips, inserted_stops)
        _insert_study_area(connection, study_geom, summary)
        _insert_summary(connection, summary)
        connection.commit()
    echo(f"Imported {len(inserted_stops)} stops, {len(inserted_routes)} routes, {len(inserted_trips)} trips, {copied} stop times.")


def _insert_dataset(connection, catalog: dict) -> None:
    limitations = catalog.get("limitations") or []
    if isinstance(limitations, list):
        limitations = "\n".join(limitations)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO datasets (
                id, name, organization, source_url, retrieved_at, license, format,
                content_sha256, geographic_coverage, temporal_coverage, processing_notes, limitations
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                catalog["id"],
                catalog["name"],
                catalog.get("organization"),
                catalog["source_url"],
                catalog.get("retrieved_at"),
                catalog.get("license"),
                catalog.get("format"),
                catalog.get("content_sha256"),
                catalog.get("geographic_coverage"),
                catalog.get("temporal_coverage"),
                catalog.get("processing_notes"),
                limitations,
            ),
        )


def _insert_agencies(connection, agencies: pd.DataFrame) -> None:
    rows = [
        (row.agency_id, row.agency_name, getattr(row, "agency_url", None), getattr(row, "agency_timezone", None))
        for row in agencies.itertuples(index=False)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO agencies (agency_id, agency_name, agency_url, agency_timezone) VALUES (%s, %s, %s, %s)",
            rows,
        )


def _insert_routes(connection, routes: pd.DataFrame, register: pd.DataFrame) -> set[str]:
    decisions = register.set_index("route_id")
    rows = []
    for route in routes.itertuples(index=False):
        route_id = str(route.route_id)
        decision = decisions.loc[route_id]
        include = str(decision["include_in_routing"]).lower() == "true"
        reason = decision["exclude_reason"] or None
        flags = decision["quality_flags"] or None
        rows.append(
            (
                route_id,
                str(route.agency_id),
                getattr(route, "route_short_name", None),
                getattr(route, "route_long_name", None),
                int(route.route_type),
                getattr(route, "route_color", None) or None,
                getattr(route, "route_text_color", None) or None,
                include,
                reason,
                flags,
                int(decision["n_stops"]),
                int(decision["n_stops_inside"]),
                float(decision["share_stops_inside"]),
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO routes (
                route_id, agency_id, route_short_name, route_long_name, route_type,
                route_color, route_text_color, include_in_routing, exclude_reason,
                quality_flags, stop_count, stops_inside_study_area, share_stops_inside
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return {row[0] for row in rows}


def _insert_calendar(connection, calendar: pd.DataFrame) -> None:
    rows = []
    for row in calendar.itertuples(index=False):
        rows.append(
            (
                row.service_id,
                int(row.monday),
                int(row.tuesday),
                int(row.wednesday),
                int(row.thursday),
                int(row.friday),
                int(row.saturday),
                int(row.sunday),
                _gtfs_date(row.start_date),
                _gtfs_date(row.end_date),
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO service_calendar (
                service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _gtfs_date(value: str):
    text = str(value)
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def _insert_stops(connection, stops: pd.DataFrame, study_geom) -> set[str]:
    prepared = prep(study_geom)
    rows = []
    for stop in stops.itertuples(index=False):
        try:
            lat = float(stop.stop_lat)
            lon = float(stop.stop_lon)
        except ValueError:
            continue
        if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
            continue
        location_type = getattr(stop, "location_type", "")
        location_value = int(location_type) if str(location_type).isdigit() else None
        point = Point(lon, lat)
        rows.append(
            (
                str(stop.stop_id),
                stop.stop_name,
                lat,
                lon,
                location_value,
                point.wkt,
                bool(prepared.covers(point)),
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO stops (stop_id, stop_name, stop_lat, stop_lon, location_type, geom, inside_study_area)
            VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s)
            """,
            rows,
        )
    return {row[0] for row in rows}


def _insert_shapes(connection, lines: dict) -> None:
    rows = [(shape_id, line.wkt) for shape_id, line in lines.items()]
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO shapes (shape_id, geom) VALUES (%s, ST_GeomFromText(%s, 4326))",
            rows,
        )


def _insert_trips(connection, trips: pd.DataFrame, route_ids: set[str]) -> set[str]:
    rows = []
    for trip in trips.itertuples(index=False):
        route_id = str(trip.route_id)
        if route_id not in route_ids:
            continue
        direction = getattr(trip, "direction_id", "")
        direction_value = int(direction) if str(direction).isdigit() else None
        rows.append(
            (
                str(trip.trip_id),
                route_id,
                str(trip.service_id),
                getattr(trip, "trip_headsign", None) or None,
                direction_value,
                getattr(trip, "shape_id", None) or None,
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO trips (trip_id, route_id, service_id, trip_headsign, direction_id, shape_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return {row[0] for row in rows}


def _copy_stop_times(connection, stop_times: pd.DataFrame, trip_ids: set[str], stop_ids: set[str]) -> int:
    frame = stop_times[["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]].copy()
    frame["trip_id"] = frame["trip_id"].astype(str)
    frame["stop_id"] = frame["stop_id"].astype(str)
    frame["stop_sequence"] = pd.to_numeric(frame["stop_sequence"], errors="coerce")
    frame = frame.dropna(subset=["stop_sequence"])
    frame = frame[frame["trip_id"].isin(trip_ids) & frame["stop_id"].isin(stop_ids)]
    frame["stop_sequence"] = frame["stop_sequence"].astype(int)
    frame = frame.drop_duplicates(["trip_id", "stop_sequence"], keep="first")
    buffer = frame.to_csv(index=False, header=False)
    with connection.cursor() as cursor:
        with cursor.copy(
            "COPY stop_times (trip_id, arrival_time, departure_time, stop_id, stop_sequence) FROM STDIN WITH (FORMAT CSV)"
        ) as copy:
            copy.write(buffer)
    return int(len(frame))


def _insert_study_area(connection, study_geom, summary: dict) -> None:
    from shapely.geometry import MultiPolygon

    geometry = study_geom
    if geometry.geom_type == "Polygon":
        geometry = MultiPolygon([geometry])
    parameters = {
        "parameters": summary["study_area"]["parameters"],
        "computed": summary["study_area"]["computed"],
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO study_area (id, method, parameters, geom)
            VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))
            """,
            ("antalya_central", "h3_density_component", psycopg.types.json.Jsonb(parameters), geometry.wkt),
        )


def _insert_summary(connection, summary: dict) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO phase1_summary (id, body) VALUES (%s, %s)",
            ("current", psycopg.types.json.Jsonb(summary)),
        )
        cursor.executemany(
            """
            INSERT INTO validation_issues (severity, code, message, issue_count, samples)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (
                    issue["severity"],
                    issue["code"],
                    issue["message"],
                    int(issue["count"]),
                    psycopg.types.json.Jsonb(issue.get("samples") or []),
                )
                for issue in summary.get("issues", [])
            ],
        )
