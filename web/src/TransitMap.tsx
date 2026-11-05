import type { FeatureCollection, LineString, MultiLineString } from "geojson";
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { LiveBus, RouteDirection, StopPoint } from "./types";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

export type MapPlace = { lat: number; lon: number; role: "start" | "end" };

type Props = {
  stops: StopPoint[];
  direction: RouteDirection | null;
  color: string;
  buses: LiveBus[];
  selectedStopId: string | null;
  onStop: (stopId: string) => void;
  places?: MapPlace[];
  onMapClick?: (lat: number, lon: number) => void;
};

export function TransitMap({ stops, direction, color, buses, selectedStopId, onStop, places = [], onMapClick }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const onStopRef = useRef(onStop);
  const onMapClickRef = useRef(onMapClick);
  onStopRef.current = onStop;
  onMapClickRef.current = onMapClick;
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) {
      return;
    }
    const map = new maplibregl.Map({
      container,
      style: "https://tiles.openfreemap.org/styles/dark",
      center: [30.713, 36.9],
      zoom: 12,
      attributionControl: { compact: false },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.on("load", () => {
      map.addSource("stops", { type: "geojson", data: EMPTY });
      map.addSource("route", { type: "geojson", data: EMPTY });
      map.addSource("buses", { type: "geojson", data: EMPTY });
      map.addSource("places", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route",
        paint: { "line-color": color, "line-width": 4, "line-opacity": 0.95 },
      });
      map.addLayer({
        id: "stops",
        type: "circle",
        source: "stops",
        paint: {
          "circle-radius": ["case", ["==", ["get", "selected"], 1], 7, 4],
          "circle-color": ["case", ["==", ["get", "onroute"], 1], color, "#8ea0b8"],
          "circle-stroke-width": 1,
          "circle-stroke-color": "#071018",
          "circle-opacity": ["case", ["==", ["get", "onroute"], 1], 1, 0.85],
        },
      });
      map.addLayer({
        id: "places",
        type: "circle",
        source: "places",
        paint: {
          "circle-radius": 9,
          "circle-color": ["match", ["get", "role"], "start", "#3b82f6", "#f8fafc"],
          "circle-stroke-width": 3,
          "circle-stroke-color": ["match", ["get", "role"], "start", "#dbeafe", "#1d4ed8"],
        },
      });
      map.addLayer({
        id: "buses",
        type: "circle",
        source: "buses",
        paint: {
          "circle-radius": 8,
          "circle-color": "#f8fafc",
          "circle-stroke-width": 3,
          "circle-stroke-color": "#1d4ed8",
        },
      });
      map.on("click", (event) => {
        const features = map.queryRenderedFeatures(event.point, { layers: ["stops"] });
        const stopId = features[0]?.properties?.id;
        if (typeof stopId === "string") {
          onStopRef.current(stopId);
          return;
        }
        onMapClickRef.current?.(event.lngLat.lat, event.lngLat.lng);
      });
      map.on("mouseenter", "stops", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "stops", () => {
        map.getCanvas().style.cursor = onMapClickRef.current ? "crosshair" : "";
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
    const map = mapRef.current;
    if (map) {
      map.getCanvas().style.cursor = onMapClick ? "crosshair" : "";
    }
  }, [onMapClick, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("stops") as maplibregl.GeoJSONSource | undefined;
    if (!source) {
      return;
    }
    const onRoute = new Set(direction?.stops.map((stop) => stop.stop_id) ?? []);
    const visible = direction
      ? direction.stops.map((stop) => ({ id: stop.stop_id, name: stop.name, lat: stop.lat, lon: stop.lon }))
      : stops;
    source.setData({
      type: "FeatureCollection",
      features: visible.map((stop) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        properties: {
          id: stop.id,
          name: stop.name,
          onroute: !direction || onRoute.has(stop.id) ? 1 : 0,
          selected: stop.id === selectedStopId ? 1 : 0,
        },
      })),
    });
  }, [stops, direction, selectedStopId, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("route") as maplibregl.GeoJSONSource | undefined;
    if (!source) {
      return;
    }
    const geometry = direction?.geometry;
    source.setData(
      geometry ? { type: "FeatureCollection", features: [{ type: "Feature", geometry, properties: {} }] } : EMPTY,
    );
    if (geometry && map) {
      const bounds = boundsOf(geometry);
      if (bounds) {
        map.fitBounds(bounds, { padding: 48, duration: 400 });
      }
    }
    if (map?.getLayer("route-line")) {
      map.setPaintProperty("route-line", "line-color", color);
    }
    if (map?.getLayer("stops")) {
      map.setPaintProperty("stops", "circle-color", ["case", ["==", ["get", "onroute"], 1], color, "#8ea0b8"]);
    }
  }, [direction, color, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("buses") as maplibregl.GeoJSONSource | undefined;
    if (!source) {
      return;
    }
    const shown = direction ? buses.filter((bus) => bus.direction_id === direction.direction_id) : buses;
    source.setData({
      type: "FeatureCollection",
      features: shown.map((bus) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [bus.longitude, bus.latitude] },
        properties: { id: bus.id, plate: bus.plate },
      })),
    });
  }, [buses, direction, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("places") as maplibregl.GeoJSONSource | undefined;
    if (!source || !map) {
      return;
    }
    source.setData({
      type: "FeatureCollection",
      features: places.map((place) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [place.lon, place.lat] },
        properties: { role: place.role },
      })),
    });
    if (!direction && places.length > 0) {
      const bounds = new maplibregl.LngLatBounds([places[0].lon, places[0].lat], [places[0].lon, places[0].lat]);
      for (const place of places) {
        bounds.extend([place.lon, place.lat]);
      }
      map.fitBounds(bounds, { padding: 72, maxZoom: 15, duration: 400 });
    }
  }, [places, direction, ready]);

  return <div ref={containerRef} className="map" role="region" aria-label="Map of Antalya public transport" />;
}

function boundsOf(geometry: LineString | MultiLineString): maplibregl.LngLatBounds | null {
  const coordinates = geometry.type === "LineString" ? geometry.coordinates : geometry.coordinates.flat();
  if (coordinates.length === 0) {
    return null;
  }
  const bounds = new maplibregl.LngLatBounds(coordinates[0] as [number, number], coordinates[0] as [number, number]);
  for (const pair of coordinates) {
    bounds.extend(pair as [number, number]);
  }
  return bounds;
}
