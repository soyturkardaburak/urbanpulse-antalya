"""Compare a fixed sample of municipal timetable pages with the pinned GTFS.

The public Antalya Ulaşım page returns one direction's stop times for one
route and one day. This check does not treat that page as a complete day's
timetable. A pattern passes when one GTFS trip on the matching weekday or
Saturday service shares the published stops and the first, middle, and last
of those stops agree within 60 seconds.
"""

from __future__ import annotations

import csv
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from urbanpulse.gtfs_time import gtfs_time_to_seconds
from urbanpulse.paths import (
    raw_gtfs_dir,
    timetable_report_csv_path,
    timetable_report_html_path,
    timetable_report_json_path,
)

TIMETABLE_URL = "https://ulasim.antalya.bel.tr/Home/TarifeListesi"
USER_AGENT = "UrbanPulseAntalya research timetable check"
PASS_TOLERANCE_SECONDS = 60
MIN_COMPARABLE_WEDNESDAY = 12
MIN_WEDNESDAY_PASS_SHARE = 0.8
DIRECTION_GIDIS = 1

# Fixed before the fetches. Sixteen Wednesday gidiş patterns and four Saturday
# repeats. Short names are the labels on the municipal route selector.
SAMPLE = [
    ("998", 3, "included_short_central_bus"),
    ("VF01", 3, "included_fully_inside_bus"),
    ("KL08", 3, "included_long_urban_bus"),
    ("UC32", 3, "included_urban_bus"),
    ("KF52", 3, "included_urban_bus"),
    ("600", 3, "included_airport_bus"),
    ("800", 3, "included_airport_bus"),
    ("T1A", 3, "included_flagged_route_type"),
    ("T1B", 3, "included_flagged_route_type"),
    ("T1C", 3, "included_flagged_route_type"),
    ("T3", 3, "included_flagged_route_type"),
    ("AA01", 3, "excluded_intercity"),
    ("AK03", 3, "excluded_intercity"),
    ("GAN01", 3, "excluded_intercity"),
    ("MAN47", 3, "excluded_intercity"),
    ("SA19", 3, "excluded_below_80_percent"),
    ("998", 6, "saturday_repeat"),
    ("T1A", 6, "saturday_repeat"),
    ("VF01", 6, "saturday_repeat"),
    ("KL08", 6, "saturday_repeat"),
]

DAY_SERVICE = {3: "service_1_MTWTFss", 6: "service_2_mtwtfSs"}
DAY_LABEL = {3: "Wednesday", 6: "Saturday"}

COMPARABLE_STATUSES = {
    "pass",
    "fail",
    "no_gtfs_trip",
    "no_published_stops",
    "not_a_single_pattern",
}


def parse_route_options(page: str) -> dict[str, str]:
    """Map municipal short names to the form's route values."""
    match = re.search(r'<select[^>]*id="selRoute"[^>]*>(.*?)</select>', page, flags=re.S)
    if match is None:
        raise ValueError("The timetable page has no route selector.")
    options: dict[str, str] = {}
    for value, label in re.findall(r'<option value="(\d+)">([^<]*)</option>', match.group(1)):
        name = html.unescape(label).strip()
        if not name or name.lower().startswith("lütfen"):
            continue
        options[name] = value
    if not options:
        raise ValueError("The timetable page listed no routes.")
    return options


def parse_timetable_rows(page: str) -> list[dict[str, str]]:
    """Extract stop-level rows from the timetable table.

    The municipal HTML splits the table body, so this does not rely on a
    well-formed tbody. Rows that do not carry a clock time are ignored.
    """
    rows: list[dict[str, str]] = []
    for block in re.findall(r"<tr>(.*?)</tr>", page, flags=re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", block, flags=re.S)
        ]
        if len(cells) < 7 or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", cells[3]):
            continue
        rows.append(
            {
                "route_short_name": cells[0],
                "route_description": cells[1],
                "pattern_name": cells[2],
                "time": cells[3],
                "stop_id": cells[4],
                "stop_name": cells[5],
                "direction": cells[6],
            }
        )
    return rows


def score_trip(published: list[dict], trip_rows: list[dict]) -> tuple[int, int, list[dict]]:
    """Score one GTFS trip against one published stop list.

    Each published stop uses the GTFS visit whose departure is closest in
    time. Exact matches are counted separately from the total absolute error.
    """
    by_stop: dict[str, list[dict]] = {}
    for row in trip_rows:
        by_stop.setdefault(row["stop_id"], []).append(row)
    exact = 0
    absolute_error = 0
    matched: list[dict] = []
    for item in published:
        options = by_stop.get(item["stop_id"], [])
        if not options:
            continue
        best = min(options, key=lambda row: abs(row["departure_seconds"] - item["time_seconds"]))
        delta = item["time_seconds"] - best["departure_seconds"]
        if delta == 0:
            exact += 1
        absolute_error += abs(delta)
        matched.append(
            {
                "stop_id": item["stop_id"],
                "stop_name": item["stop_name"],
                "published_time": item["time"],
                "stop_sequence": best["stop_sequence"],
                "gtfs_departure_seconds": best["departure_seconds"],
                "delta_seconds": delta,
            }
        )
    return exact, absolute_error, matched


def compare_pattern(published_rows: list[dict[str, str]], candidate_trips: dict[str, list[dict]]) -> dict:
    """Compare one published pattern with the GTFS trips offered for that route and day."""
    if not published_rows:
        return {"status": "no_published_stops", "detail": "The page returned no stop times."}
    stop_ids = [row["stop_id"] for row in published_rows]
    if len(stop_ids) != len(set(stop_ids)):
        return {
            "status": "not_a_single_pattern",
            "detail": "The page listed the same stop more than once, so this is not one trip pattern.",
            "published_stops": len(published_rows),
        }
    published = []
    for row in published_rows:
        seconds = gtfs_time_to_seconds(row["time"])
        if seconds is None:
            return {"status": "fail", "detail": f"Unreadable published time {row['time']}."}
        published.append({**row, "time_seconds": seconds})
    best_id = None
    best_rank = None
    best_matched: list[dict] = []
    for trip_id, trip_rows in candidate_trips.items():
        exact, absolute_error, matched = score_trip(published, trip_rows)
        # Shared stops come first. An empty trip has zero error and must not
        # outrank a trip whose times are close but not exact.
        rank = (len(matched), exact, -absolute_error)
        if best_rank is None or rank > best_rank or (rank == best_rank and (best_id is None or trip_id < best_id)):
            best_rank = rank
            best_id = trip_id
            best_matched = matched
    if not best_matched or best_id is None:
        return {"status": "no_gtfs_trip", "detail": "No GTFS trip on this service shares a published stop."}
    ordered = sorted(best_matched, key=lambda item: (item["stop_sequence"], item["stop_id"]))
    if len(ordered) < 3:
        return {
            "status": "fail",
            "gtfs_trip_id": best_id,
            "published_stops": len(published),
            "shared_stops": len(ordered),
            "detail": "Fewer than three published stops appear on the closest GTFS trip.",
        }
    anchors = {
        "first": ordered[0],
        "middle": ordered[len(ordered) // 2],
        "last": ordered[-1],
    }
    within = all(abs(item["delta_seconds"]) <= PASS_TOLERANCE_SECONDS for item in anchors.values())
    return {
        "status": "pass" if within else "fail",
        "gtfs_trip_id": best_id,
        "pattern_name": published_rows[0].get("pattern_name", ""),
        "published_stops": len(published),
        "shared_stops": len(ordered),
        "exact_stop_times": best_rank[1] if best_rank else 0,
        "anchors": anchors,
        "detail": "First, middle, and last shared stops are within 60 seconds."
        if within
        else "At least one of the first, middle, or last shared stops differs by more than 60 seconds.",
    }


def interpretation_allowed(wednesday_rows: list[dict]) -> tuple[bool, str]:
    """Apply the rule fixed before looking at the fetched times."""
    if any(row.get("status") == "fetch_error" for row in wednesday_rows):
        return False, "A Wednesday page failed to load, so the check is incomplete."
    comparable = [row for row in wednesday_rows if row.get("status") in COMPARABLE_STATUSES]
    if len(comparable) < MIN_COMPARABLE_WEDNESDAY:
        return False, f"Only {len(comparable)} Wednesday patterns could be compared; {MIN_COMPARABLE_WEDNESDAY} are required."
    passes = sum(1 for row in comparable if row.get("status") == "pass")
    share = passes / len(comparable)
    if share < MIN_WEDNESDAY_PASS_SHARE:
        return (
            False,
            f"{passes} of {len(comparable)} comparable Wednesday patterns passed ({share:.2f}); 0.80 is required.",
        )
    return True, f"{passes} of {len(comparable)} comparable Wednesday patterns passed ({share:.2f})."


def fetch_page(route_value: str | None, day: int | None, direction: int = DIRECTION_GIDIS) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if route_value is None:
                request = urllib.request.Request(TIMETABLE_URL, headers={"User-Agent": USER_AGENT})
            else:
                payload = urllib.parse.urlencode(
                    {"selDay": str(day), "selDirection": str(direction), "selRoute": route_value}
                ).encode()
                request = urllib.request.Request(
                    TIMETABLE_URL,
                    data=payload,
                    headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
                )
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2)
    assert last_error is not None
    raise last_error


def _read_table(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(directory / name, dtype=str, keep_default_na=False)


def load_candidates(directory: Path, short_names: set[str]) -> dict[tuple[str, str], dict[str, list[dict]]]:
    """Index GTFS trips by short name and service id for the sample only."""
    routes = _read_table(directory, "routes.txt")
    calendar = _read_table(directory, "calendar.txt")
    missing_services = [service for service in DAY_SERVICE.values() if service not in set(calendar["service_id"])]
    if missing_services:
        raise ValueError(f"Pinned feed is missing calendar services: {', '.join(missing_services)}")
    selected = routes[routes["route_short_name"].isin(short_names)]
    trips = _read_table(directory, "trips.txt")
    trips = trips[trips["route_id"].isin(set(selected["route_id"]))]
    stop_times = _read_table(directory, "stop_times.txt")
    stop_times = stop_times[stop_times["trip_id"].isin(set(trips["trip_id"]))]
    route_names = dict(zip(selected["route_id"], selected["route_short_name"], strict=True))
    by_trip: dict[str, list[dict]] = {}
    for row in stop_times.itertuples(index=False):
        seconds = gtfs_time_to_seconds(row.departure_time)
        if seconds is None:
            continue
        by_trip.setdefault(row.trip_id, []).append(
            {
                "stop_id": row.stop_id,
                "stop_sequence": int(row.stop_sequence),
                "departure_seconds": seconds,
            }
        )
    indexed: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for trip in trips.itertuples(index=False):
        name = route_names.get(trip.route_id)
        if name is None or trip.trip_id not in by_trip:
            continue
        indexed.setdefault((name, trip.service_id), {})[trip.trip_id] = by_trip[trip.trip_id]
    return indexed


def _anchor_fields(result: dict) -> dict[str, str | int | None]:
    fields: dict[str, str | int | None] = {}
    anchors = result.get("anchors") or {}
    for name in ("first", "middle", "last"):
        item = anchors.get(name) or {}
        fields[f"{name}_stop_id"] = item.get("stop_id")
        fields[f"{name}_stop_name"] = item.get("stop_name")
        fields[f"{name}_published_time"] = item.get("published_time")
        fields[f"{name}_delta_seconds"] = item.get("delta_seconds")
    return fields


def run_spot_check(sleep_seconds: float = 1.0) -> dict:
    """Fetch the fixed sample and write the timetable report."""
    candidates = load_candidates(raw_gtfs_dir(), {name for name, _, _ in SAMPLE})
    form = fetch_page(None, None)
    options = parse_route_options(form)
    results = []
    for index, (short_name, day, role) in enumerate(SAMPLE):
        if index:
            time.sleep(sleep_seconds)
        row: dict = {
            "route_short_name": short_name,
            "day": DAY_LABEL[day],
            "day_code": day,
            "role": role,
            "service_id": DAY_SERVICE[day],
            "direction": "Gidiş",
        }
        route_value = options.get(short_name)
        if route_value is None:
            row["status"] = "not_listed"
            row["detail"] = "This short name is not in the municipal route selector."
            results.append(row)
            continue
        row["official_route_value"] = route_value
        try:
            page = fetch_page(route_value, day)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            row["status"] = "fetch_error"
            row["detail"] = exc.__class__.__name__
            results.append(row)
            continue
        published = parse_timetable_rows(page)
        row["published_direction_values"] = sorted({item["direction"] for item in published})
        comparison = compare_pattern(published, candidates.get((short_name, DAY_SERVICE[day]), {}))
        row.update(comparison)
        row.update(_anchor_fields(comparison))
        results.append(row)

    wednesday = [row for row in results if row["day_code"] == 3]
    allowed, rule_text = interpretation_allowed(wednesday)
    saturday_notes = []
    for short_name in sorted({name for name, day, _ in SAMPLE if day == 6}):
        wed = next(row for row in results if row["route_short_name"] == short_name and row["day_code"] == 3)
        sat = next(row for row in results if row["route_short_name"] == short_name and row["day_code"] == 6)
        saturday_notes.append(
            {
                "route_short_name": short_name,
                "wednesday_status": wed.get("status"),
                "saturday_status": sat.get("status"),
                "wednesday_first_time": wed.get("first_published_time"),
                "saturday_first_time": sat.get("first_published_time"),
                "same_first_stop": wed.get("first_stop_id") == sat.get("first_stop_id"),
                "first_time_delta_seconds": _clock_delta(wed.get("first_published_time"), sat.get("first_published_time")),
            }
        )
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_url": TIMETABLE_URL,
        "method": (
            "One gidiş pattern was requested per route and day. "
            "Agreement uses the first, middle, and last shared stops, ordered by GTFS stop_sequence, "
            f"with a tolerance of {PASS_TOLERANCE_SECONDS} seconds. "
            "The page is not a full-day timetable."
        ),
        "interpretation_rule": (
            f"Hospital times may be interpreted only when every Wednesday fetch succeeds, "
            f"at least {MIN_COMPARABLE_WEDNESDAY} Wednesday patterns are comparable, "
            f"and at least {MIN_WEDNESDAY_PASS_SHARE:.0%} of those pass."
        ),
        "interpretation_allowed": allowed,
        "interpretation_reason": rule_text,
        "patterns": results,
        "saturday_compared_with_wednesday": saturday_notes,
        "counts": {
            "patterns": len(results),
            "wednesday_pass": sum(1 for row in wednesday if row.get("status") == "pass"),
            "wednesday_comparable": sum(1 for row in wednesday if row.get("status") in COMPARABLE_STATUSES),
            "fetch_errors": sum(1 for row in results if row.get("status") == "fetch_error"),
            "not_listed": sum(1 for row in results if row.get("status") == "not_listed"),
        },
    }
    _write_reports(summary)
    return summary


def _clock_delta(left: str | None, right: str | None) -> int | None:
    if left is None or right is None:
        return None
    left_seconds = gtfs_time_to_seconds(left)
    right_seconds = gtfs_time_to_seconds(right)
    if left_seconds is None or right_seconds is None:
        return None
    return right_seconds - left_seconds


def _write_reports(summary: dict) -> None:
    timetable_report_json_path().write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "route_short_name",
        "day",
        "role",
        "status",
        "official_route_value",
        "gtfs_trip_id",
        "pattern_name",
        "published_stops",
        "shared_stops",
        "exact_stop_times",
        "first_stop_id",
        "first_stop_name",
        "first_published_time",
        "first_delta_seconds",
        "middle_stop_id",
        "middle_stop_name",
        "middle_published_time",
        "middle_delta_seconds",
        "last_stop_id",
        "last_stop_name",
        "last_published_time",
        "last_delta_seconds",
        "detail",
    ]
    with timetable_report_csv_path().open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary["patterns"])
    timetable_report_html_path().write_text(_html(summary), encoding="utf-8")


def _html(summary: dict) -> str:
    def cell(value: object) -> str:
        return html.escape("" if value is None else str(value))

    body = []
    for row in summary["patterns"]:
        body.append(
            "<tr>"
            f"<td>{cell(row.get('route_short_name'))}</td>"
            f"<td>{cell(row.get('day'))}</td>"
            f"<td>{cell(row.get('role'))}</td>"
            f"<td>{cell(row.get('status'))}</td>"
            f"<td>{cell(row.get('gtfs_trip_id'))}</td>"
            f"<td>{cell(row.get('first_delta_seconds'))}</td>"
            f"<td>{cell(row.get('middle_delta_seconds'))}</td>"
            f"<td>{cell(row.get('last_delta_seconds'))}</td>"
            f"<td>{cell(row.get('detail'))}</td>"
            "</tr>"
        )
    saturday = []
    for row in summary["saturday_compared_with_wednesday"]:
        saturday.append(
            "<tr>"
            f"<td>{cell(row.get('route_short_name'))}</td>"
            f"<td>{cell(row.get('wednesday_first_time'))}</td>"
            f"<td>{cell(row.get('saturday_first_time'))}</td>"
            f"<td>{cell(row.get('first_time_delta_seconds'))}</td>"
            f"<td>{cell(row.get('same_first_stop'))}</td>"
            "</tr>"
        )
    allowed = "Yes" if summary["interpretation_allowed"] else "No"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Antalya timetable spot-check</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; line-height: 1.45; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid #ccc; text-align: left; padding: 0.35rem; vertical-align: top; }}
</style>
</head>
<body>
<h1>Timetable spot-check</h1>
<p>{cell(summary["method"])}</p>
<p><strong>Interpret hospital times:</strong> {allowed}. {cell(summary["interpretation_reason"])}</p>
<p>{cell(summary["interpretation_rule"])}</p>
<p>Checked at {cell(summary["checked_at"])}. Source: {cell(summary["source_url"])}.</p>
<table>
<thead><tr><th>Route</th><th>Day</th><th>Role</th><th>Status</th><th>GTFS trip</th><th>First delta (s)</th><th>Middle delta (s)</th><th>Last delta (s)</th><th>Detail</th></tr></thead>
<tbody>
{''.join(body)}
</tbody>
</table>
<h2>Saturday against Wednesday</h2>
<p>This compares the first shared stop of the single published pattern. It is not proof that the whole Saturday timetable differs.</p>
<table>
<thead><tr><th>Route</th><th>Wednesday first time</th><th>Saturday first time</th><th>Delta (s)</th><th>Same first stop</th></tr></thead>
<tbody>
{''.join(saturday)}
</tbody>
</table>
</body>
</html>
"""
