import type { LineString, MultiLineString } from "geojson";

export type RouteSummary = {
  id: string;
  short_name: string;
  long_name: string;
  type: number;
  type_label: string;
  color: string;
};

export type StopPoint = {
  id: string;
  name: string;
  lat: number;
  lon: number;
};

export type Departure = {
  time: string;
  gtfs_time: string;
  upcoming: boolean;
};

export type RouteStop = {
  sequence: number;
  stop_id: string;
  name: string;
  lat: number;
  lon: number;
  running_seconds: number;
};

export type RouteDirection = {
  direction_id: number;
  headsign: string;
  geometry: LineString | MultiLineString | null;
  stops: RouteStop[];
  departures: Departure[];
};

export type ScheduleMeta = {
  timezone: string;
  service_date: string;
  service_label: string | null;
  schedule_through: string | null;
  now: string;
  schedule_note: string;
};

export type RouteCatalog = ScheduleMeta & {
  routes: RouteSummary[];
};

export type StopCatalog = {
  stops: StopPoint[];
};

export type RouteDetail = ScheduleMeta & {
  id: string;
  short_name: string;
  long_name: string;
  type_label: string;
  color: string;
  directions: RouteDirection[];
};

export type StopLine = {
  route_id: string;
  short_name: string;
  long_name: string;
  color: string;
  type_label: string;
  direction_id: number;
  headsign: string;
  departures: Departure[];
};

export type StopBoard = ScheduleMeta & {
  id: string;
  name: string;
  lat: number;
  lon: number;
  lines: StopLine[];
};

export type LiveBus = {
  id: string;
  plate: string | null;
  latitude: number;
  longitude: number;
  speed_kmh: number | null;
  heading: number | null;
  direction_id: number | null;
  reported_at: string | null;
  nearest_stop_id: string | null;
  nearest_stop_name: string | null;
  nearest_distance_m: number | null;
};

export type LiveRoute = {
  route_id: string;
  short_name: string;
  fetched_at: string;
  available: boolean;
  vehicles: LiveBus[];
  traffic_note: string;
  estimate_note: string;
};

export type LiveArrival = {
  route_id: string;
  short_name: string;
  headsign: string;
  color: string;
  scheduled: string | null;
  eta_minutes: number | null;
  stops_away: number | null;
  plate: string | null;
  speed_kmh: number | null;
};

export type LiveStop = {
  stop_id: string;
  fetched_at: string;
  available: boolean;
  arrivals: LiveArrival[];
  traffic_note: string;
  estimate_note: string;
};

export type DirectRide = {
  kind: "direct";
  route_id: string;
  short_name: string;
  long_name: string;
  color: string;
  direction_id: number;
  headsign: string;
  board_stop_id: string;
  board_stop_name: string;
  alight_stop_id: string;
  alight_stop_name: string;
  departure: string;
  arrival: string;
  ride_minutes: number | null;
  walk_to_minutes: number;
  walk_from_minutes: number;
  stops_between: number;
};

export type TransferRide = {
  kind: "transfer";
  route_id: string;
  short_name: string;
  color: string;
  direction_id: number;
  headsign: string;
  second_route_id: string;
  second_short_name: string;
  second_color: string;
  second_direction_id: number;
  second_headsign: string;
  board_stop_name: string;
  change_stop_name: string;
  alight_stop_name: string;
  departure: string;
  change_arrival: string;
  second_departure: string;
  arrival: string;
  ride_minutes: number | null;
  wait_minutes: number | null;
  walk_to_minutes: number;
  walk_from_minutes: number;
};

export type Journey = ScheduleMeta & {
  options: Array<DirectRide | TransferRide>;
  walk_note: string;
  empty_note: string;
  origin_radius_m: number;
  destination_radius_m: number;
};

export type StudyOverview = {
  study_area: {
    area_km2: number;
    stops_inside: number;
    stops_outside: number;
    bounds: [number, number, number, number];
  };
  route_decisions: {
    included: number;
    excluded: number;
    rule: string;
  };
};

export type AccessSummary = {
  time_basis: string;
  origins: number;
  hospital_within_30: number;
  hospital_within_45: number;
  hospital_within_60: number;
  interpretation_reason: string;
  null_means: string;
  parameters: { departure: string };
  kaleici_example: {
    walk_minutes: number;
    access_stop_name: string;
    departures_per_hour: number;
    hospital_minutes_p50: number;
    nearest_hospital_name: string;
  };
};

export type TimetableReport = {
  counts: { wednesday_pass: number; wednesday_comparable: number };
  patterns: { route_short_name: string; day: string; status: string }[];
};

export type CellResult = {
  h3: string;
  walk_minutes: number | null;
  access_stop_name: string | null;
  departures_per_hour: number | null;
  distinct_routes: number | null;
  hospital_minutes_p50: number | null;
  nearest_hospital_name: string | null;
  hospitals_within_30: number | null;
  hospitals_within_45: number | null;
  hospitals_within_60: number | null;
};
