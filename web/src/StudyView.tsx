import type { FeatureCollection } from "geojson";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { getJson } from "./api";
import type { AccessSummary, CellResult, StudyOverview, TimetableReport } from "./types";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

export function StudyView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [overview, setOverview] = useState<StudyOverview | null>(null);
  const [summary, setSummary] = useState<AccessSummary | null>(null);
  const [timetable, setTimetable] = useState<TimetableReport | null>(null);
  const [area, setArea] = useState<FeatureCollection | null>(null);
  const [routes, setRoutes] = useState<FeatureCollection | null>(null);
  const [stops, setStops] = useState<FeatureCollection | null>(null);
  const [cells, setCells] = useState<FeatureCollection | null>(null);
  const [cell, setCell] = useState<CellResult | null>(null);
  const [cellError, setCellError] = useState<string | null>(null);
  const [loadNote, setLoadNote] = useState("Loading the stored study.");
  const [showIncluded, setShowIncluded] = useState(true);
  const [showExcluded, setShowExcluded] = useState(false);
  const [showStops, setShowStops] = useState(false);
  const [showHospitals, setShowHospitals] = useState(true);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) {
      return;
    }
    const map = new maplibregl.Map({
      container,
      style: "https://tiles.openfreemap.org/styles/dark",
      center: [30.713, 36.9],
      zoom: 11,
      attributionControl: { compact: false },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.on("load", () => {
      map.addSource("cells", { type: "geojson", data: EMPTY });
      map.addSource("area", { type: "geojson", data: EMPTY });
      map.addSource("routes", { type: "geojson", data: EMPTY });
      map.addSource("stops", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "access-fill",
        type: "fill",
        source: "cells",
        paint: {
          "fill-color": [
            "case",
            ["==", ["typeof", ["get", "hospital_minutes_p50"]], "number"],
            ["step", ["get", "hospital_minutes_p50"], "#93c5fd", 30, "#3b82f6", 45, "#1d4ed8", 60, "#1e3a8a"],
            "#334155",
          ],
          "fill-opacity": 0.55,
        },
      });
      map.addLayer({
        id: "area-line",
        type: "line",
        source: "area",
        paint: { "line-color": "#93c5fd", "line-width": 2 },
      });
      map.addLayer({
        id: "routes",
        type: "line",
        source: "routes",
        paint: {
          "line-color": ["case", ["==", ["get", "include_in_routing"], true], "#60a5fa", "#64748b"],
          "line-width": 1.6,
          "line-opacity": 0.85,
        },
      });
      map.addLayer({
        id: "stops",
        type: "circle",
        source: "stops",
        paint: { "circle-radius": 3, "circle-color": "#e7eef8", "circle-opacity": 0.8 },
      });
      map.on("click", (event) => {
        if (!map.getLayer("access-fill") || map.getLayoutProperty("access-fill", "visibility") === "none") {
          return;
        }
        const hits = map.queryRenderedFeatures(event.point, { layers: ["access-fill"] });
        const h3 = hits[0]?.properties?.h3;
        if (typeof h3 !== "string") {
          return;
        }
        setCell(null);
        setCellError(null);
        void getJson<CellResult>(`/api/accessibility/cells/${encodeURIComponent(h3)}`)
          .then(setCell)
          .catch((reason: unknown) => {
            setCellError(reason instanceof Error ? reason.message : "That cell could not be read.");
          });
      });
      setReady(true);
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      getJson<StudyOverview>("/api/overview"),
      getJson<FeatureCollection>("/api/map/study-area"),
      getJson<FeatureCollection>("/api/map/routes"),
      getJson<FeatureCollection>("/api/map/stops"),
    ])
      .then(([overviewBody, areaBody, routeBody, stopBody]) => {
        if (cancelled) {
          return;
        }
        setOverview(overviewBody);
        setArea(areaBody);
        setRoutes(routeBody);
        setStops(stopBody);
        setLoadNote("");
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setLoadNote(reason instanceof Error ? reason.message : "The study map could not be loaded.");
        }
      });
    void getJson<AccessSummary>("/api/accessibility/summary")
      .then((body) => {
        if (!cancelled) {
          setSummary(body);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSummary(null);
        }
      });
    void getJson<FeatureCollection>("/api/accessibility/cells")
      .then((body) => {
        if (!cancelled) {
          setCells(body);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCells(null);
        }
      });
    void getJson<TimetableReport>("/api/timetable-check")
      .then((body) => {
        if (!cancelled) {
          setTimetable(body);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTimetable(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const bounds = overview?.study_area.bounds;
    if (!map || !ready || !bounds) {
      return;
    }
    map.fitBounds(
      [
        [bounds[0], bounds[1]],
        [bounds[2], bounds[3]],
      ],
      { padding: 28, duration: 0 },
    );
  }, [overview, ready]);

  const routeData = useMemo(() => {
    if (!routes) {
      return EMPTY;
    }
    return {
      type: "FeatureCollection" as const,
      features: routes.features.filter((feature) => {
        const included = feature.properties?.include_in_routing === true;
        return (included && showIncluded) || (!included && showExcluded);
      }),
    };
  }, [routes, showIncluded, showExcluded]);

  const stopData = useMemo(() => {
    if (!stops || !showStops) {
      return EMPTY;
    }
    return {
      type: "FeatureCollection" as const,
      features: stops.features.filter((feature) => feature.properties?.inside_study_area === true),
    };
  }, [stops, showStops]);

  useEffect(() => {
    setSource(mapRef.current, "cells", showHospitals && cells ? cells : EMPTY);
    setSource(mapRef.current, "area", area ?? EMPTY);
    setSource(mapRef.current, "routes", routeData);
    setSource(mapRef.current, "stops", stopData);
    const map = mapRef.current;
    if (map?.getLayer("access-fill")) {
      map.setLayoutProperty("access-fill", "visibility", showHospitals && cells ? "visible" : "none");
    }
  }, [cells, area, routeData, stopData, showHospitals, ready]);

  const failures = [...new Set(
    (timetable?.patterns ?? [])
      .filter((pattern) => pattern.day === "Wednesday" && pattern.status === "fail")
      .map((pattern) => pattern.route_short_name),
  )];

  return (
    <div className="stage">
      <div ref={containerRef} className="map" role="region" aria-label="Study area map" />
      <aside className="sheet">
        <div className="modes">
          <button type="button" aria-pressed={showIncluded} onClick={() => setShowIncluded((value) => !value)}>
            Included lines
          </button>
          <button type="button" aria-pressed={showExcluded} onClick={() => setShowExcluded((value) => !value)}>
            Excluded lines
          </button>
          <button type="button" aria-pressed={showStops} onClick={() => setShowStops((value) => !value)}>
            Stops inside
          </button>
          <button type="button" aria-pressed={showHospitals} onClick={() => setShowHospitals((value) => !value)}>
            Hospital times
          </button>
        </div>
        {loadNote && <p>{loadNote}</p>}
        {overview && (
          <>
            <h2>Study area</h2>
            <p>
              {overview.study_area.area_km2} km². {overview.study_area.stops_inside.toLocaleString("en-GB")} stops inside,{" "}
              {overview.study_area.stops_outside.toLocaleString("en-GB")} outside.
            </p>
            <p>
              {overview.route_decisions.included} lines included and {overview.route_decisions.excluded} excluded.{" "}
              {overview.route_decisions.rule}
            </p>
          </>
        )}
        {timetable && (
          <>
            <h2>Timetable check</h2>
            <p>
              {timetable.counts.wednesday_pass} of {timetable.counts.wednesday_comparable} comparable Wednesday patterns
              matched the public timetable. Each check is one published gidiş pattern, not the full day.
            </p>
            {failures.length > 0 && <p>Did not pass: {failures.join(", ")}.</p>}
          </>
        )}
        {summary ? (
          <>
            <h2>Wednesday morning hospitals</h2>
            <p>
              Departure {summary.parameters.departure.replace("T", " ")}. These minutes are scheduled and modelled. They
              are not observed journeys, and a missing time is not a long journey.
            </p>
            <p>{summary.null_means}</p>
            <p>
              {summary.hospital_within_30.toLocaleString("en-GB")} cells have a hospital within 30 min,{" "}
              {summary.hospital_within_45.toLocaleString("en-GB")} within 45, and{" "}
              {summary.hospital_within_60.toLocaleString("en-GB")} within 60, out of{" "}
              {summary.origins.toLocaleString("en-GB")}.
            </p>
            <p>{summary.interpretation_reason}</p>
            <p>
              Kaleiçi example: {summary.kaleici_example.walk_minutes} min walk to {summary.kaleici_example.access_stop_name},{" "}
              {summary.kaleici_example.departures_per_hour} departures an hour,{" "}
              {summary.kaleici_example.hospital_minutes_p50} min to {summary.kaleici_example.nearest_hospital_name}.
            </p>
            <div className="legend">
              <span><i className="swatch a" /> 30 min or less</span>
              <span><i className="swatch b" /> 45</span>
              <span><i className="swatch c" /> 60</span>
              <span><i className="swatch d" /> longer</span>
              <span><i className="swatch e" /> no time stored</span>
            </div>
          </>
        ) : (
          !loadNote && <p>The hospital layer is not on this server. The study area and lines above are still the stored ones.</p>
        )}
        <h2>Clicked cell</h2>
        {!cell && !cellError && <p>Click a coloured cell. The numbers come from the stored result.</p>}
        {cellError && <p className="error">{cellError}</p>}
        {cell && (
          <dl className="facts">
            <div><dt>Cell</dt><dd>{cell.h3}</dd></div>
            <div><dt>Walk to the access stop</dt><dd>{minutes(cell.walk_minutes)}</dd></div>
            <div><dt>Access stop</dt><dd>{cell.access_stop_name ?? "None stored"}</dd></div>
            <div><dt>Departures an hour at that stop</dt><dd>{cell.departures_per_hour ?? "None stored"}</dd></div>
            <div><dt>Lines at that stop</dt><dd>{cell.distinct_routes ?? "None stored"}</dd></div>
            <div><dt>Hospital time</dt><dd>{minutes(cell.hospital_minutes_p50)}</dd></div>
            <div><dt>Nearest hospital</dt><dd>{cell.nearest_hospital_name ?? "None stored"}</dd></div>
            <div>
              <dt>Hospitals within 30 / 45 / 60 min</dt>
              <dd>
                {cell.hospitals_within_30 ?? "—"} / {cell.hospitals_within_45 ?? "—"} / {cell.hospitals_within_60 ?? "—"}
              </dd>
            </div>
          </dl>
        )}
      </aside>
    </div>
  );
}

function minutes(value: number | null): string {
  return value == null ? "No time stored" : `${value} min`;
}

function setSource(map: maplibregl.Map | null, name: string, data: FeatureCollection) {
  const source = map?.getSource(name) as maplibregl.GeoJSONSource | undefined;
  source?.setData(data);
}
