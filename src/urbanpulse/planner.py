"""From-to rides on the stored schedule.

A direct option is one scheduled trip that boards near the start and alights
near the end. Walk minutes are a straight-line estimate at 4.8 km/h, the same
speed used for the hospital measurement. They are not a walked path.
"""

from __future__ import annotations

from urbanpulse.db import connect
from urbanpulse.rider import _schedule_meta, _service_rows, clock_label, css_color, gtfs_seconds

WALK_METRES_PER_MINUTE = 80
SEARCH_RADII_M = (700, 1200)


def walk_minutes(metres: float) -> int:
    if metres <= 40:
        return 0
    return max(1, round(metres / WALK_METRES_PER_MINUTE))


def ride_minutes(departure: str, arrival: str) -> int | None:
    start = gtfs_seconds(departure)
    end = gtfs_seconds(arrival)
    if start is None or end is None or end < start:
        return None
    return round((end - start) / 60)


def rank_direct(candidates: list[dict], limit: int = 6) -> list[dict]:
    """Keep the earliest upcoming departure for each line and stop pair."""
    best: dict[tuple, dict] = {}
    for item in candidates:
        key = (item["route_id"], item["direction_id"], item["board_stop_id"], item["alight_stop_id"])
        current = best.get(key)
        item_rank = (0 if item["upcoming"] else 1, item["departure_gtfs"])
        if current is None:
            best[key] = item
            continue
        current_rank = (0 if current["upcoming"] else 1, current["departure_gtfs"])
        if item_rank < current_rank:
            best[key] = item
    ordered = sorted(
        best.values(),
        key=lambda item: (
            0 if item["upcoming"] else 1,
            item["walk_to_minutes"] + (item["ride_minutes"] or 999) + item["walk_from_minutes"],
            item["departure_gtfs"],
        ),
    )
    return ordered[:limit]


def plan_journey(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> dict:
    with connect() as connection:
        moment, active = _service_rows(connection)
        service_ids = [row[0] for row in active]
        now_seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
        with connection.cursor() as cursor:
            origin, origin_radius = _nearby(cursor, from_lat, from_lon)
            destination, dest_radius = _nearby(cursor, to_lat, to_lon)
            rows = []
            if origin and destination and service_ids:
                rows = _direct_rows(cursor, origin, destination, service_ids)
            direct = rank_direct(_as_options(rows, origin, destination, now_seconds))
            transfers = []
            if origin and destination and service_ids and not direct:
                transfers = _transfer_rows(cursor, origin, destination, service_ids, now_seconds)
    options = direct + transfers[: max(0, 6 - len(direct))]
    payload = _schedule_meta(moment, active)
    payload.update(
        {
            "from": {"lat": from_lat, "lon": from_lon},
            "to": {"lat": to_lat, "lon": to_lon},
            "origin_stops": origin,
            "destination_stops": destination,
            "origin_radius_m": origin_radius,
            "destination_radius_m": dest_radius,
            "options": options,
            "walk_note": "Walk minutes are a straight line at 4.8 km/h to the stop, not a street path.",
            "empty_note": "No scheduled ride today connects a stop near the start with a stop near the end.",
        }
    )
    return payload


def _nearby(cursor, lat: float, lon: float) -> tuple[list[dict], int]:
    for radius in SEARCH_RADII_M:
        cursor.execute(
            """
            SELECT stop_id, stop_name, stop_lat, stop_lon,
                   ST_Distance(
                       geom::geography,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) AS metres
            FROM stops
            WHERE ST_DWithin(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
            ORDER BY metres
            LIMIT 6
            """,
            (lon, lat, lon, lat, radius),
        )
        rows = cursor.fetchall()
        if rows:
            return (
                [
                    {
                        "id": row[0],
                        "name": row[1],
                        "lat": row[2],
                        "lon": row[3],
                        "metres": round(row[4]),
                    }
                    for row in rows
                ],
                radius,
            )
    return [], SEARCH_RADII_M[-1]


def _direct_rows(cursor, origin: list[dict], destination: list[dict], service_ids: list[str]):
    origin_ids = [stop["id"] for stop in origin]
    dest_ids = [stop["id"] for stop in destination]
    cursor.execute(
        """
        SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_color,
               COALESCE(t.direction_id, 0), COALESCE(t.trip_headsign, ''),
               so.stop_id, sd.stop_id, so.departure_time, sd.arrival_time,
               sd.stop_sequence - so.stop_sequence
        FROM stop_times so
        JOIN stop_times sd
          ON sd.trip_id = so.trip_id
         AND sd.stop_sequence > so.stop_sequence
        JOIN trips t ON t.trip_id = so.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE so.stop_id = ANY(%s)
          AND sd.stop_id = ANY(%s)
          AND t.service_id = ANY(%s)
        """,
        (origin_ids, dest_ids, service_ids),
    )
    return cursor.fetchall()


def _as_options(rows, origin: list[dict], destination: list[dict], now_seconds: int) -> list[dict]:
    origin_by_id = {stop["id"]: stop for stop in origin}
    dest_by_id = {stop["id"]: stop for stop in destination}
    options = []
    for row in rows:
        departure = row[8]
        arrival = row[9]
        seconds = gtfs_seconds(departure)
        board = origin_by_id[row[6]]
        alight = dest_by_id[row[7]]
        options.append(
            {
                "kind": "direct",
                "route_id": row[0],
                "short_name": row[1],
                "long_name": row[2],
                "color": css_color(row[3]),
                "direction_id": int(row[4]),
                "headsign": row[5] or "",
                "board_stop_id": board["id"],
                "board_stop_name": board["name"],
                "board_lat": board["lat"],
                "board_lon": board["lon"],
                "alight_stop_id": alight["id"],
                "alight_stop_name": alight["name"],
                "alight_lat": alight["lat"],
                "alight_lon": alight["lon"],
                "departure": clock_label(departure),
                "arrival": clock_label(arrival),
                "departure_gtfs": departure,
                "upcoming": seconds is not None and seconds >= now_seconds,
                "ride_minutes": ride_minutes(departure, arrival),
                "walk_to_minutes": walk_minutes(board["metres"]),
                "walk_from_minutes": walk_minutes(alight["metres"]),
                "stops_between": int(row[10]),
            }
        )
    return options


def _transfer_rows(cursor, origin: list[dict], destination: list[dict], service_ids: list[str], now_seconds: int) -> list[dict]:
    hour, rem = divmod(now_seconds, 3600)
    minute, second = divmod(rem, 60)
    now_text = f"{hour:02d}:{minute:02d}:{second:02d}"
    origin_ids = [stop["id"] for stop in origin]
    dest_ids = [stop["id"] for stop in destination]
    origin_by_id = {stop["id"]: stop for stop in origin}
    dest_by_id = {stop["id"]: stop for stop in destination}
    try:
        cursor.execute("SET LOCAL statement_timeout = '4000'")
        cursor.execute(
            """
            WITH boards AS (
                SELECT DISTINCT ON (t.route_id, COALESCE(t.direction_id, 0), so.stop_id)
                       t.trip_id, t.route_id, COALESCE(t.direction_id, 0) AS direction_id,
                       COALESCE(t.trip_headsign, '') AS headsign,
                       r.route_short_name, r.route_color,
                       so.stop_id AS board_id, so.departure_time, so.stop_sequence
                FROM stop_times so
                JOIN trips t ON t.trip_id = so.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE so.stop_id = ANY(%s)
                  AND t.service_id = ANY(%s)
                  AND so.departure_time >= %s
                ORDER BY t.route_id, COALESCE(t.direction_id, 0), so.stop_id, so.departure_time
                LIMIT 16
            )
            SELECT b.route_id, b.route_short_name, b.route_color, b.direction_id, b.headsign,
                   b.board_id, b.departure_time,
                   s.stop_id, s.stop_name, s.stop_lat, s.stop_lon, st.arrival_time,
                   r2.route_id, r2.route_short_name, r2.route_color,
                   COALESCE(t2.direction_id, 0), COALESCE(t2.trip_headsign, ''),
                   so2.departure_time, sd.arrival_time, sd.stop_id
            FROM boards b
            JOIN stop_times st ON st.trip_id = b.trip_id AND st.stop_sequence > b.stop_sequence
            JOIN stops s ON s.stop_id = st.stop_id
            JOIN stop_times so2 ON so2.stop_id = st.stop_id AND so2.departure_time >= st.arrival_time
            JOIN trips t2 ON t2.trip_id = so2.trip_id AND t2.service_id = ANY(%s) AND t2.route_id <> b.route_id
            JOIN routes r2 ON r2.route_id = t2.route_id
            JOIN stop_times sd
              ON sd.trip_id = so2.trip_id
             AND sd.stop_sequence > so2.stop_sequence
             AND sd.stop_id = ANY(%s)
            ORDER BY b.departure_time, so2.departure_time
            LIMIT 40
            """,
            (origin_ids, service_ids, now_text, service_ids, dest_ids),
        )
        found = cursor.fetchall()
    except Exception:
        return []
    shaped = []
    for row in found:
        board = origin_by_id.get(row[5])
        alight = dest_by_id.get(row[19])
        if board is None or alight is None:
            continue
        first_ride = ride_minutes(row[6], row[11])
        second_ride = ride_minutes(row[17], row[18])
        shaped.append(
            {
                "kind": "transfer",
                "route_id": row[0],
                "short_name": row[1],
                "color": css_color(row[2]),
                "direction_id": int(row[3]),
                "headsign": row[4],
                "board_stop_id": board["id"],
                "board_stop_name": board["name"],
                "board_lat": board["lat"],
                "board_lon": board["lon"],
                "change_stop_id": row[7],
                "change_stop_name": row[8],
                "change_lat": row[9],
                "change_lon": row[10],
                "second_route_id": row[12],
                "second_short_name": row[13],
                "second_color": css_color(row[14]),
                "second_direction_id": int(row[15]),
                "second_headsign": row[16],
                "alight_stop_id": alight["id"],
                "alight_stop_name": alight["name"],
                "alight_lat": alight["lat"],
                "alight_lon": alight["lon"],
                "departure": clock_label(row[6]),
                "departure_gtfs": row[6],
                "change_arrival": clock_label(row[11]),
                "second_departure": clock_label(row[17]),
                "arrival": clock_label(row[18]),
                "upcoming": True,
                "ride_minutes": None if first_ride is None or second_ride is None else first_ride + second_ride,
                "wait_minutes": _wait_minutes(row[11], row[17]),
                "walk_to_minutes": walk_minutes(board["metres"]),
                "walk_from_minutes": walk_minutes(alight["metres"]),
                "stops_between": None,
            }
        )
    return _best_transfers(shaped)


def _wait_minutes(arrival: str, departure: str) -> int | None:
    arrived = gtfs_seconds(arrival)
    leaves = gtfs_seconds(departure)
    if arrived is None or leaves is None or leaves < arrived:
        return None
    return (leaves - arrived) // 60


def _best_transfers(options: list[dict]) -> list[dict]:
    """One connection per pair of lines, preferring a short wait and a short ride."""
    usable = [item for item in options if item["wait_minutes"] is not None and item["wait_minutes"] <= 25]
    pool = usable or [item for item in options if item["wait_minutes"] is not None]
    best: dict[tuple, dict] = {}
    for item in pool:
        key = (item["route_id"], item["second_route_id"])
        current = best.get(key)
        rank = (item["wait_minutes"], item["ride_minutes"] or 999, item["departure_gtfs"])
        if current is None:
            best[key] = item
            continue
        current_rank = (current["wait_minutes"], current["ride_minutes"] or 999, current["departure_gtfs"])
        if rank < current_rank:
            best[key] = item
    ordered = sorted(best.values(), key=lambda item: (item["wait_minutes"], item["ride_minutes"] or 999, item["departure_gtfs"]))
    return ordered[:4]
