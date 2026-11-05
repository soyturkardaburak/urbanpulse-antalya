"""Passenger schedule and live-position views.

Schedules come from the imported GTFS feed. Live positions come from the
public KentKart pathInfo feed for Antalya region 026. Road traffic is not
part of that feed.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from urbanpulse.db import connect

ISTANBUL = ZoneInfo("Europe/Istanbul")
KENTKART_REGION = "026"
KENTKART_PATH = "https://service.kentkart.com/rl1/web/pathInfo"
USER_AGENT = "UrbanPulseAntalya/0.1 (public transit viewer)"
SNAP_METRES = 600
ROUTE_TYPES = {0: "Tram", 1: "Metro", 2: "Rail", 3: "Bus", 4: "Ferry", 7: "Funicular"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def gtfs_seconds(value: str | None) -> int | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hour, minute, second = (int(part) for part in parts)
    except ValueError:
        return None
    if minute < 0 or minute > 59 or second < 0 or second > 59 or hour < 0:
        return None
    return hour * 3600 + minute * 60 + second


def clock_label(value: str | None) -> str:
    seconds = gtfs_seconds(value)
    if seconds is None:
        return value or ""
    extra_days, remainder = divmod(seconds, 24 * 3600)
    hour, rest = divmod(remainder, 3600)
    minute, _second = divmod(rest, 60)
    label = f"{hour:02d}:{minute:02d}"
    if extra_days:
        return f"{label} after midnight"
    return label


def css_color(value: str | None) -> str:
    if not value:
        return "#3b82f6"
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        return text
    if len(text) == 6:
        return f"#{text}"
    return "#3b82f6"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def parse_bus(raw: dict) -> dict | None:
    latitude = _float(raw.get("lat") or raw.get("latitude"))
    longitude = _float(raw.get("lng") or raw.get("lon") or raw.get("longitude"))
    if latitude is None or longitude is None:
        return None
    speed = _float(raw.get("speed"))
    if speed is None or speed < 0 or speed > 140:
        speed = None
    heading = _float(raw.get("bearing") or raw.get("course") or raw.get("heading") or raw.get("angle"))
    plate = raw.get("plateNumber") or raw.get("plate") or raw.get("busPlate")
    identity = raw.get("busId") or raw.get("vehicleId") or plate or ""
    return {
        "id": str(identity),
        "plate": str(plate) if plate else None,
        "latitude": latitude,
        "longitude": longitude,
        "speed_kmh": speed,
        "heading": heading,
        "reported_at": raw.get("time") or raw.get("gpsTime") or raw.get("dateTime"),
    }


def vehicles_from_payload(payload: dict | None, direction_id: int) -> list[dict]:
    if not payload:
        return []
    vehicles = []
    for path in payload.get("pathList") or []:
        for raw in path.get("busList") or []:
            if not isinstance(raw, dict):
                continue
            bus = parse_bus(raw)
            if bus is None:
                continue
            bus["direction_id"] = direction_id
            vehicles.append(bus)
    return vehicles


def snap_to_pattern(latitude: float, longitude: float, stops: list[dict]) -> dict | None:
    if not stops:
        return None
    nearest = min(stops, key=lambda stop: distance_m(latitude, longitude, stop["lat"], stop["lon"]))
    metres = distance_m(latitude, longitude, nearest["lat"], nearest["lon"])
    if metres > SNAP_METRES:
        return None
    return {"stop": nearest, "distance_m": round(metres)}


def arrival_estimate(stops: list[dict], bus_lat: float, bus_lon: float, target_stop_id: str) -> dict | None:
    """Scheduled running time from the stop nearest the bus to the target stop."""
    snapped = snap_to_pattern(bus_lat, bus_lon, stops)
    if snapped is None:
        return None
    nearest = snapped["stop"]
    target = next((stop for stop in stops if stop["stop_id"] == target_stop_id), None)
    if target is None or target["sequence"] < nearest["sequence"]:
        return None
    stops_away = target["sequence"] - nearest["sequence"]
    delta = target["running_seconds"] - nearest["running_seconds"]
    minutes = round(delta / 60) if stops_away == 0 or delta > 0 else None
    if minutes is not None and minutes < 0:
        minutes = None
    return {
        "stops_away": stops_away,
        "eta_minutes": minutes,
        "nearest_stop_id": nearest["stop_id"],
        "nearest_stop_name": nearest["name"],
        "distance_m": snapped["distance_m"],
    }


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _now() -> datetime:
    return datetime.now(ISTANBUL)


def _service_rows(connection) -> tuple[datetime, list[tuple]]:
    moment = _now()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday,
                   start_date, end_date
            FROM service_calendar
            WHERE start_date <= %s AND end_date >= %s
            """,
            (moment.date(), moment.date()),
        )
        rows = cursor.fetchall()
    weekday = moment.weekday()
    active = [row for row in rows if row[1 + weekday] == 1]
    return moment, active


def _schedule_meta(moment: datetime, active: list[tuple]) -> dict:
    label = WEEKDAYS[moment.weekday()].capitalize()
    through = None
    if active:
        through = max(row[9] for row in active).isoformat()
    note = (
        "Times follow the stored "
        + label
        + " schedule. Public holidays are not listed separately. These are scheduled times, not observed running times."
    )
    if not active:
        note = "The stored schedule does not include this date, so departure times are not shown."
    return {
        "timezone": "Europe/Istanbul",
        "service_date": moment.date().isoformat(),
        "service_label": label if active else None,
        "schedule_through": through,
        "now": moment.strftime("%H:%M"),
        "schedule_note": note,
    }


def list_routes() -> dict:
    with connect() as connection:
        moment, active = _service_rows(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT route_id, route_short_name, route_long_name, route_type, route_color
                FROM routes
                ORDER BY route_short_name, route_id
                """
            )
            rows = cursor.fetchall()
    payload = _schedule_meta(moment, active)
    payload["routes"] = [
        {
            "id": row[0],
            "short_name": row[1],
            "long_name": row[2],
            "type": row[3],
            "type_label": ROUTE_TYPES.get(row[3], "Transit"),
            "color": css_color(row[4]),
        }
        for row in rows
    ]
    return payload


def list_stops() -> dict:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stop_id, stop_name, stop_lat, stop_lon
                FROM stops
                ORDER BY stop_name, stop_id
                """
            )
            rows = cursor.fetchall()
    return {
        "stops": [
            {"id": row[0], "name": row[1], "lat": row[2], "lon": row[3]}
            for row in rows
        ]
    }


def route_detail(route_id: str) -> dict | None:
    with connect() as connection:
        moment, active = _service_rows(connection)
        service_ids = [row[0] for row in active]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT route_id, route_short_name, route_long_name, route_type, route_color
                FROM routes
                WHERE route_id = %s
                """,
                (route_id,),
            )
            route = cursor.fetchone()
            if route is None:
                return None
            directions = _directions(cursor, route_id, service_ids, moment)
    payload = _schedule_meta(moment, active)
    payload.update(
        {
            "id": route[0],
            "short_name": route[1],
            "long_name": route[2],
            "type_label": ROUTE_TYPES.get(route[3], "Transit"),
            "color": css_color(route[4]),
            "directions": directions,
        }
    )
    return payload


def _directions(cursor, route_id: str, service_ids: list[str], moment: datetime) -> list[dict]:
    ids = service_ids or ["__none__"]
    cursor.execute(
        """
        SELECT COALESCE(t.direction_id, 0) AS direction_id, t.trip_headsign, COUNT(*) AS n
        FROM trips t
        WHERE t.route_id = %s AND t.service_id = ANY(%s)
        GROUP BY 1, 2
        ORDER BY 1, n DESC
        """,
        (route_id, ids),
    )
    headsigns: dict[int, str] = {}
    for direction_id, headsign, _count in cursor.fetchall():
        headsigns.setdefault(int(direction_id), headsign or "")
    if not headsigns:
        cursor.execute(
            """
            SELECT COALESCE(direction_id, 0), trip_headsign, COUNT(*)
            FROM trips
            WHERE route_id = %s
            GROUP BY 1, 2
            ORDER BY 1, 3 DESC
            """,
            (route_id,),
        )
        for direction_id, headsign, _count in cursor.fetchall():
            headsigns.setdefault(int(direction_id), headsign or "")
    directions = []
    now_seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
    for direction_id, headsign in sorted(headsigns.items()):
        pattern, geometry = _pattern_and_shape(cursor, route_id, direction_id, ids)
        first_stop = pattern[0]["stop_id"] if pattern else None
        departures = _departures(cursor, route_id, direction_id, ids, first_stop, now_seconds) if first_stop else []
        directions.append(
            {
                "direction_id": direction_id,
                "headsign": headsign,
                "geometry": geometry,
                "stops": pattern,
                "departures": departures,
            }
        )
    return directions


def _pattern_and_shape(cursor, route_id: str, direction_id: int, service_ids: list[str]) -> tuple[list[dict], dict | None]:
    cursor.execute(
        """
        SELECT t.trip_id
        FROM trips t
        JOIN stop_times st ON st.trip_id = t.trip_id
        WHERE t.route_id = %s
          AND COALESCE(t.direction_id, 0) = %s
          AND t.service_id = ANY(%s)
        GROUP BY t.trip_id
        ORDER BY COUNT(*) DESC, t.trip_id
        LIMIT 1
        """,
        (route_id, direction_id, service_ids),
    )
    chosen = cursor.fetchone()
    if chosen is None:
        cursor.execute(
            """
            SELECT t.trip_id
            FROM trips t
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE t.route_id = %s AND COALESCE(t.direction_id, 0) = %s
            GROUP BY t.trip_id
            ORDER BY COUNT(*) DESC, t.trip_id
            LIMIT 1
            """,
            (route_id, direction_id),
        )
        chosen = cursor.fetchone()
    pattern: list[dict] = []
    if chosen is not None:
        cursor.execute(
            """
            SELECT st.stop_sequence, st.stop_id, s.stop_name, s.stop_lat, s.stop_lon, st.arrival_time
            FROM stop_times st
            JOIN stops s ON s.stop_id = st.stop_id
            WHERE st.trip_id = %s
            ORDER BY st.stop_sequence
            """,
            (chosen[0],),
        )
        rows = cursor.fetchall()
        origin = gtfs_seconds(rows[0][5]) if rows else 0
        origin = origin or 0
        for sequence, stop_id, name, lat, lon, arrival in rows:
            arrival_seconds = gtfs_seconds(arrival)
            running = (arrival_seconds - origin) if arrival_seconds is not None else 0
            pattern.append(
                {
                    "sequence": sequence,
                    "stop_id": stop_id,
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "running_seconds": running,
                }
            )
    cursor.execute(
        """
        SELECT t.shape_id
        FROM trips t
        WHERE t.route_id = %s
          AND COALESCE(t.direction_id, 0) = %s
          AND t.shape_id IS NOT NULL
          AND t.service_id = ANY(%s)
        GROUP BY t.shape_id
        ORDER BY COUNT(*) DESC, t.shape_id
        LIMIT 1
        """,
        (route_id, direction_id, service_ids),
    )
    shape = cursor.fetchone()
    if shape is None:
        cursor.execute(
            """
            SELECT t.shape_id
            FROM trips t
            WHERE t.route_id = %s
              AND COALESCE(t.direction_id, 0) = %s
              AND t.shape_id IS NOT NULL
            GROUP BY t.shape_id
            ORDER BY COUNT(*) DESC, t.shape_id
            LIMIT 1
            """,
            (route_id, direction_id),
        )
        shape = cursor.fetchone()
    geometry = None
    if shape is not None:
        cursor.execute("SELECT ST_AsGeoJSON(geom) FROM shapes WHERE shape_id = %s", (shape[0],))
        geo = cursor.fetchone()
        if geo and geo[0]:
            geometry = json.loads(geo[0]) if isinstance(geo[0], str) else geo[0]
    return pattern, geometry


def _departures(cursor, route_id: str, direction_id: int, service_ids: list[str], stop_id: str, now_seconds: int) -> list[dict]:
    cursor.execute(
        """
        SELECT DISTINCT st.departure_time
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        WHERE t.route_id = %s
          AND COALESCE(t.direction_id, 0) = %s
          AND t.service_id = ANY(%s)
          AND st.stop_id = %s
        ORDER BY st.departure_time
        """,
        (route_id, direction_id, service_ids, stop_id),
    )
    departures = []
    for (raw,) in cursor.fetchall():
        seconds = gtfs_seconds(raw)
        departures.append(
            {
                "time": clock_label(raw),
                "gtfs_time": raw,
                "upcoming": seconds is not None and seconds >= now_seconds,
            }
        )
    return departures


def stop_board(stop_id: str) -> dict | None:
    with connect() as connection:
        moment, active = _service_rows(connection)
        service_ids = [row[0] for row in active] or ["__none__"]
        now_seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE stop_id = %s",
                (stop_id,),
            )
            stop = cursor.fetchone()
            if stop is None:
                return None
            cursor.execute(
                """
                SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_color, r.route_type,
                       COALESCE(t.direction_id, 0), COALESCE(t.trip_headsign, ''), st.departure_time
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE st.stop_id = %s AND t.service_id = ANY(%s)
                ORDER BY r.route_short_name, st.departure_time
                """,
                (stop_id, service_ids),
            )
            rows = cursor.fetchall()
    grouped: dict[tuple, list[dict]] = {}
    for route_id, short_name, long_name, color, route_type, direction_id, headsign, raw in rows:
        key = (route_id, direction_id, headsign)
        seconds = gtfs_seconds(raw)
        grouped.setdefault(key, [])
        if any(item["gtfs_time"] == raw for item in grouped[key]):
            continue
        grouped[key].append(
            {
                "time": clock_label(raw),
                "gtfs_time": raw,
                "upcoming": seconds is not None and seconds >= now_seconds,
                "route_id": route_id,
                "short_name": short_name,
                "long_name": long_name,
                "color": css_color(color),
                "type_label": ROUTE_TYPES.get(route_type, "Transit"),
                "direction_id": direction_id,
                "headsign": headsign,
            }
        )
    lines = []
    for items in grouped.values():
        upcoming = [item for item in items if item["upcoming"]][:6]
        shown = upcoming or items[:3]
        sample = items[0]
        lines.append(
            {
                "route_id": sample["route_id"],
                "short_name": sample["short_name"],
                "long_name": sample["long_name"],
                "color": sample["color"],
                "type_label": sample["type_label"],
                "direction_id": sample["direction_id"],
                "headsign": sample["headsign"],
                "departures": [{"time": item["time"], "gtfs_time": item["gtfs_time"], "upcoming": item["upcoming"]} for item in shown],
            }
        )
    lines.sort(key=lambda line: ((line["departures"][0]["gtfs_time"] if line["departures"] else "99"), line["short_name"]))
    payload = _schedule_meta(moment, active)
    payload.update(
        {
            "id": stop[0],
            "name": stop[1],
            "lat": stop[2],
            "lon": stop[3],
            "lines": lines,
        }
    )
    return payload


def fetch_kentkart(short_name: str, direction: int) -> dict | None:
    query = urllib.parse.urlencode(
        {
            "region": KENTKART_REGION,
            "lang": "tr",
            "direction": str(direction),
            "displayRouteCode": short_name,
            "resultType": "011000",
        }
    )
    request = urllib.request.Request(
        f"{KENTKART_PATH}?{query}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def live_route(route_id: str, fetcher=fetch_kentkart) -> dict | None:
    detail = route_detail(route_id)
    if detail is None:
        return None
    vehicles, failed = _live_vehicles(detail["short_name"], detail["directions"], fetcher)
    _attach_nearest(vehicles, detail["directions"])
    return {
        "route_id": route_id,
        "short_name": detail["short_name"],
        "fetched_at": _now().isoformat(timespec="seconds"),
        "available": not failed or bool(vehicles),
        "vehicles": vehicles,
        "traffic_note": "The public feed does not include road traffic. A speed is the speed reported by that bus.",
        "estimate_note": "An arrival estimate is the scheduled running time from the stop nearest the bus. It is not an operator prediction.",
    }


def live_stop(stop_id: str, fetcher=fetch_kentkart) -> dict | None:
    board = stop_board(stop_id)
    if board is None:
        return None
    wanted = {}
    for line in board["lines"]:
        wanted.setdefault(line["route_id"], {"short_name": line["short_name"], "directions": set()})
        wanted[line["route_id"]]["directions"].add(line["direction_id"])
    # A busy stop can be served by many lines. Ask the live feed for the first lines only.
    chosen = list(wanted.items())[:4]
    vehicles_by_route: dict[str, list[dict]] = {}
    failed = False
    jobs = []
    for route_id, info in chosen:
        for direction_id in sorted(info["directions"]):
            jobs.append((route_id, info["short_name"], direction_id))
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(_safe_fetch, fetcher, short_name, direction_id): (route_id, direction_id)
            for route_id, short_name, direction_id in jobs
        }
        for future in as_completed(future_map):
            route_id, direction_id = future_map[future]
            payload = future.result()
            if payload is None:
                failed = True
                continue
            vehicles_by_route.setdefault(route_id, []).extend(vehicles_from_payload(payload, direction_id))
    arrivals = []
    for line in board["lines"]:
        if line["route_id"] not in dict(chosen):
            continue
        pattern = _pattern_for(line["route_id"], line["direction_id"])
        best = None
        best_bus = None
        for bus in vehicles_by_route.get(line["route_id"], []):
            if bus["direction_id"] != line["direction_id"]:
                continue
            estimate = arrival_estimate(pattern, bus["latitude"], bus["longitude"], stop_id)
            if estimate is None:
                continue
            if best is None or (estimate["eta_minutes"] or 10_000) < (best["eta_minutes"] or 10_000):
                best = estimate
                best_bus = bus
        next_time = next((item["time"] for item in line["departures"] if item["upcoming"]), None)
        if next_time is None and line["departures"]:
            next_time = line["departures"][0]["time"]
        arrivals.append(
            {
                "route_id": line["route_id"],
                "short_name": line["short_name"],
                "headsign": line["headsign"],
                "color": line["color"],
                "scheduled": next_time,
                "eta_minutes": None if best is None else best["eta_minutes"],
                "stops_away": None if best is None else best["stops_away"],
                "plate": None if best_bus is None else best_bus["plate"],
                "speed_kmh": None if best_bus is None else best_bus["speed_kmh"],
            }
        )
    return {
        "stop_id": stop_id,
        "fetched_at": _now().isoformat(timespec="seconds"),
        "available": not failed or any(vehicles_by_route.values()),
        "arrivals": arrivals,
        "traffic_note": "The public feed does not include road traffic. A speed is the speed reported by that bus.",
        "estimate_note": "An arrival estimate is the scheduled running time from the stop nearest the bus. It is not an operator prediction.",
    }


def _pattern_for(route_id: str, direction_id: int) -> list[dict]:
    detail = route_detail(route_id)
    if detail is None:
        return []
    for direction in detail["directions"]:
        if direction["direction_id"] == direction_id:
            return direction["stops"]
    return []


def _live_vehicles(short_name: str, directions: list[dict], fetcher) -> tuple[list[dict], bool]:
    vehicles = []
    failed = False
    direction_ids = [direction["direction_id"] for direction in directions] or [0, 1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {pool.submit(_safe_fetch, fetcher, short_name, direction_id): direction_id for direction_id in direction_ids}
        for future in as_completed(future_map):
            direction_id = future_map[future]
            payload = future.result()
            if payload is None:
                failed = True
                continue
            vehicles.extend(vehicles_from_payload(payload, direction_id))
    return vehicles, failed


def _attach_nearest(vehicles: list[dict], directions: list[dict]) -> None:
    by_direction = {direction["direction_id"]: direction["stops"] for direction in directions}
    for bus in vehicles:
        snapped = snap_to_pattern(bus["latitude"], bus["longitude"], by_direction.get(bus["direction_id"], []))
        bus["nearest_stop_id"] = None if snapped is None else snapped["stop"]["stop_id"]
        bus["nearest_stop_name"] = None if snapped is None else snapped["stop"]["name"]
        bus["nearest_distance_m"] = None if snapped is None else snapped["distance_m"]


def _safe_fetch(fetcher, short_name: str, direction_id: int) -> dict | None:
    try:
        return fetcher(short_name, direction_id)
    except Exception:
        return None
