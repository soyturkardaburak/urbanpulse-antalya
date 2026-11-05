-- Phase 1 serving schema.
-- Accessibility result tables are intentionally absent.

DROP TABLE IF EXISTS validation_issues CASCADE;
DROP TABLE IF EXISTS phase1_summary CASCADE;
DROP TABLE IF EXISTS study_area CASCADE;
DROP TABLE IF EXISTS stop_times CASCADE;
DROP TABLE IF EXISTS trips CASCADE;
DROP TABLE IF EXISTS shapes CASCADE;
DROP TABLE IF EXISTS stops CASCADE;
DROP TABLE IF EXISTS routes CASCADE;
DROP TABLE IF EXISTS service_calendar CASCADE;
DROP TABLE IF EXISTS agencies CASCADE;
DROP TABLE IF EXISTS datasets CASCADE;

CREATE TABLE datasets (
    id text PRIMARY KEY,
    name text NOT NULL,
    organization text,
    source_url text NOT NULL,
    retrieved_at timestamptz,
    license text,
    format text,
    content_sha256 text,
    geographic_coverage text,
    temporal_coverage text,
    processing_notes text,
    limitations text
);

CREATE TABLE agencies (
    agency_id text PRIMARY KEY,
    agency_name text,
    agency_url text,
    agency_timezone text
);

CREATE TABLE routes (
    route_id text PRIMARY KEY,
    agency_id text REFERENCES agencies (agency_id),
    route_short_name text,
    route_long_name text,
    route_type integer,
    route_color text,
    route_text_color text,
    include_in_routing boolean NOT NULL,
    exclude_reason text,
    quality_flags text,
    stop_count integer,
    stops_inside_study_area integer,
    share_stops_inside double precision
);

CREATE TABLE service_calendar (
    service_id text PRIMARY KEY,
    monday smallint NOT NULL,
    tuesday smallint NOT NULL,
    wednesday smallint NOT NULL,
    thursday smallint NOT NULL,
    friday smallint NOT NULL,
    saturday smallint NOT NULL,
    sunday smallint NOT NULL,
    start_date date,
    end_date date
);

CREATE TABLE stops (
    stop_id text PRIMARY KEY,
    stop_name text,
    stop_lat double precision,
    stop_lon double precision,
    location_type integer,
    geom geometry(Point, 4326) NOT NULL,
    inside_study_area boolean NOT NULL
);

CREATE INDEX stops_geom_gix ON stops USING GIST (geom);
CREATE INDEX stops_inside_idx ON stops (inside_study_area);

CREATE TABLE shapes (
    shape_id text PRIMARY KEY,
    geom geometry(LineString, 4326) NOT NULL
);

CREATE INDEX shapes_geom_gix ON shapes USING GIST (geom);

CREATE TABLE trips (
    trip_id text PRIMARY KEY,
    route_id text REFERENCES routes (route_id),
    service_id text,
    trip_headsign text,
    direction_id smallint,
    shape_id text
);

CREATE INDEX trips_route_idx ON trips (route_id);
CREATE INDEX trips_shape_idx ON trips (shape_id);
CREATE INDEX trips_service_idx ON trips (service_id);

CREATE TABLE stop_times (
    trip_id text REFERENCES trips (trip_id),
    stop_id text REFERENCES stops (stop_id),
    arrival_time text,
    departure_time text,
    stop_sequence integer NOT NULL,
    PRIMARY KEY (trip_id, stop_sequence)
);

CREATE INDEX stop_times_stop_idx ON stop_times (stop_id);

CREATE TABLE study_area (
    id text PRIMARY KEY,
    method text NOT NULL,
    parameters jsonb NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX study_area_geom_gix ON study_area USING GIST (geom);

CREATE TABLE phase1_summary (
    id text PRIMARY KEY,
    body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE validation_issues (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    severity text NOT NULL,
    code text NOT NULL,
    message text NOT NULL,
    issue_count integer NOT NULL,
    samples jsonb
);
