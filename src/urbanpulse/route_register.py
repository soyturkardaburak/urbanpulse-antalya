from __future__ import annotations

import re

import pandas as pd

SHARE_THRESHOLD = 0.8


def inclusion_decision(share: float, n_stops: int, threshold: float = SHARE_THRESHOLD) -> tuple[bool, str]:
    if n_stops < 2:
        return False, "fewer_than_2_stops"
    if share + 1e-12 >= threshold:
        return True, ""
    return False, "fewer_than_80_percent_of_stops_inside_study_area"


def quality_flags(agency_id: str, route_type: str, route_short_name: str, route_long_name: str) -> str:
    flags: list[str] = []
    if str(agency_id) == "99":
        flags.append("agency_named_test")
    folded = f"{route_short_name} {route_long_name}".casefold()
    if str(route_type) == "3" and ("tramvay" in folded or "tram" in folded):
        flags.append("gtfs_type_bus_but_name_suggests_tram")
    if re.search(r"\d{5}\s*-", route_long_name or ""):
        flags.append("long_name_looks_like_stop_codes")
    if str(route_type) in {"0", "2"}:
        flags.append(f"gtfs_route_type_{route_type}")
    return ";".join(flags)


def build_route_register(
    routes: pd.DataFrame,
    trips: pd.DataFrame,
    stop_times: pd.DataFrame,
    *,
    valid_stop_ids: set[str],
    inside_stop_ids: set[str],
    agencies: pd.DataFrame | None = None,
) -> pd.DataFrame:
    agency_names = {}
    if agencies is not None and {"agency_id", "agency_name"}.issubset(agencies.columns):
        agency_names = dict(zip(agencies["agency_id"], agencies["agency_name"], strict=True))

    route_of_trip = dict(zip(trips["trip_id"], trips["route_id"], strict=False))
    pairs = stop_times[["trip_id", "stop_id"]].copy()
    pairs["route_id"] = pairs["trip_id"].map(route_of_trip)
    pairs = pairs.dropna(subset=["route_id"]).drop_duplicates(["route_id", "stop_id"])
    stops_by_route: dict[str, set[str]] = {}
    for route_id, stop_id in zip(pairs["route_id"], pairs["stop_id"], strict=True):
        stops_by_route.setdefault(str(route_id), set()).add(str(stop_id))

    rows = []
    for route in routes.itertuples(index=False):
        route_id = str(route.route_id)
        stop_ids = stops_by_route.get(route_id, set())
        valid = [stop_id for stop_id in stop_ids if stop_id in valid_stop_ids]
        inside = [stop_id for stop_id in valid if stop_id in inside_stop_ids]
        n_stops = len(valid)
        n_inside = len(inside)
        share = (n_inside / n_stops) if n_stops else 0.0
        include, reason = inclusion_decision(share, n_stops)
        short_name = getattr(route, "route_short_name", "")
        long_name = getattr(route, "route_long_name", "")
        rows.append(
            {
                "route_id": route_id,
                "agency_id": str(getattr(route, "agency_id", "")),
                "agency_name": agency_names.get(str(getattr(route, "agency_id", "")), ""),
                "route_short_name": short_name,
                "route_long_name": long_name,
                "route_type": str(getattr(route, "route_type", "")),
                "include_in_routing": include,
                "exclude_reason": reason,
                "quality_flags": quality_flags(
                    str(getattr(route, "agency_id", "")),
                    str(getattr(route, "route_type", "")),
                    str(short_name),
                    str(long_name),
                ),
                "n_stops": n_stops,
                "n_stops_inside": n_inside,
                "share_stops_inside": round(share, 6),
            }
        )
    register = pd.DataFrame(rows)
    if register.empty:
        return register
    return register.sort_values(["include_in_routing", "route_short_name", "route_id"], ascending=[False, True, True])
