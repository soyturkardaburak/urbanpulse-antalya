import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getJson } from "./api";
import { distanceMetres, walkMinutes } from "./geo";
import { StudyView } from "./StudyView";
import { TransitMap, type MapPlace } from "./TransitMap";
import type {
  DirectRide,
  Journey,
  LiveRoute,
  LiveStop,
  RouteCatalog,
  RouteDetail,
  StopBoard,
  StopCatalog,
  StopPoint,
  TransferRide,
} from "./types";

const MODES = ["All", "Bus", "Tram", "Rail", "Ferry"] as const;
const TABS = ["Plan", "Nearby", "Lines", "Study"] as const;
type Tab = (typeof TABS)[number];
type Place = { lat: number; lon: number; label: string };

const CITY: Place = { lat: 36.9, lon: 30.713, label: "City centre" };

export function App() {
  const [catalog, setCatalog] = useState<RouteCatalog | null>(null);
  const [stops, setStops] = useState<StopPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<(typeof MODES)[number]>("All");
  const [tab, setTab] = useState<Tab>("Plan");
  const [online, setOnline] = useState(navigator.onLine);
  const [routeId, setRouteId] = useState<string | null>(null);
  const [directionId, setDirectionId] = useState(0);
  const [stopId, setStopId] = useState<string | null>(null);
  const [route, setRoute] = useState<RouteDetail | null>(null);
  const [board, setBoard] = useState<StopBoard | null>(null);
  const [liveRoute, setLiveRoute] = useState<LiveRoute | null>(null);
  const [liveStop, setLiveStop] = useState<LiveStop | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [activeEnd, setActiveEnd] = useState<"start" | "end">("start");
  const [start, setStart] = useState<Place | null>(null);
  const [end, setEnd] = useState<Place | null>(null);
  const [here, setHere] = useState<Place>(CITY);
  const [journey, setJourney] = useState<Journey | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  useEffect(() => {
    void Promise.all([getJson<RouteCatalog>("/api/transit/routes"), getJson<StopCatalog>("/api/transit/stops")])
      .then(([routes, stopCatalog]) => {
        setCatalog(routes);
        setStops(stopCatalog.stops);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "The schedule could not be loaded.");
      });
  }, []);

  useEffect(() => {
    if (!routeId) {
      setRoute(null);
      return;
    }
    setDetailError(null);
    void getJson<RouteDetail>(`/api/transit/routes/${encodeURIComponent(routeId)}`)
      .then((detail) => {
        setRoute(detail);
        setDirectionId((current) =>
          detail.directions.some((direction) => direction.direction_id === current)
            ? current
            : (detail.directions[0]?.direction_id ?? 0),
        );
      })
      .catch((reason: unknown) => {
        setRoute(null);
        setDetailError(reason instanceof Error ? reason.message : "This line could not be loaded.");
      });
  }, [routeId]);

  useEffect(() => {
    if (!stopId) {
      setBoard(null);
      return;
    }
    void getJson<StopBoard>(`/api/transit/stops/${encodeURIComponent(stopId)}`)
      .then(setBoard)
      .catch((reason: unknown) => {
        setBoard(null);
        setDetailError(reason instanceof Error ? reason.message : "This stop could not be loaded.");
      });
  }, [stopId]);

  useEffect(() => {
    if (!online || tab === "Study") {
      setLiveRoute(null);
      setLiveStop(null);
      return;
    }
    let cancelled = false;
    const load = () => {
      if (routeId) {
        void getJson<LiveRoute>(`/api/transit/live/routes/${encodeURIComponent(routeId)}`)
          .then((payload) => {
            if (!cancelled) {
              setLiveRoute(payload);
            }
          })
          .catch(() => {
            if (!cancelled) {
              setLiveRoute(null);
            }
          });
      } else {
        setLiveRoute(null);
      }
      if (stopId) {
        void getJson<LiveStop>(`/api/transit/live/stops/${encodeURIComponent(stopId)}`)
          .then((payload) => {
            if (!cancelled) {
              setLiveStop(payload);
            }
          })
          .catch(() => {
            if (!cancelled) {
              setLiveStop(null);
            }
          });
      } else {
        setLiveStop(null);
      }
    };
    load();
    const timer = window.setInterval(load, 20000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [online, routeId, stopId, tab]);

  const direction = route?.directions.find((item) => item.direction_id === directionId) ?? route?.directions[0] ?? null;
  const matches = useMemo(() => search(catalog?.routes ?? [], stops, query, mode, tab), [catalog, stops, query, mode, tab]);
  const places = useMemo<MapPlace[]>(() => {
    const items: MapPlace[] = [];
    if (start) {
      items.push({ lat: start.lat, lon: start.lon, role: "start" });
    }
    if (end) {
      items.push({ lat: end.lat, lon: end.lon, role: "end" });
    }
    return items;
  }, [start, end]);
  const mapStops = useMemo(() => {
    if (tab === "Lines") {
      return stops;
    }
    const point = tab === "Nearby" ? here : activeEnd === "start" ? (start ?? end) : (end ?? start);
    if (!point) {
      return [];
    }
    return nearestStops(stops, point.lat, point.lon, 12);
  }, [tab, stops, here, activeEnd, start, end]);
  const nearby = useMemo(() => nearestStops(stops, here.lat, here.lon, 8).map((stop) => ({
    ...stop,
    metres: Math.round(distanceMetres(here.lat, here.lon, stop.lat, stop.lon)),
  })), [stops, here]);

  function openRoute(id: string, nextDirection?: number) {
    if (nextDirection != null) {
      setDirectionId(nextDirection);
    }
    setRouteId(id);
    setStopId(null);
    setQuery("");
  }

  function openStop(id: string) {
    setStopId(id);
    setQuery("");
  }

  function place(next: Place) {
    if (activeEnd === "start") {
      setStart(next);
      if (!end) {
        setActiveEnd("end");
      }
    } else {
      setEnd(next);
    }
    setQuery("");
    setJourney(null);
  }

  function useMyLocation() {
    if (!navigator.geolocation) {
      setPlanError("This browser has no location. Tap the map or search a stop.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const next = { lat: position.coords.latitude, lon: position.coords.longitude, label: "My location" };
        setHere(next);
        setStart(next);
        setActiveEnd(end ? "start" : "end");
        setPlanError(null);
        setJourney(null);
      },
      () => setPlanError("Location is unavailable. Tap the map or search a stop."),
    );
  }

  function findRoutes() {
    if (!start || !end) {
      return;
    }
    setPlanning(true);
    setPlanError(null);
    setRouteId(null);
    setStopId(null);
    const path = `/api/transit/plan?from_lat=${start.lat}&from_lon=${start.lon}&to_lat=${end.lat}&to_lon=${end.lon}`;
    void getJson<Journey>(path)
      .then(setJourney)
      .catch((reason: unknown) => {
        setJourney(null);
        setPlanError(reason instanceof Error ? reason.message : "The timetable could not be searched.");
      })
      .finally(() => setPlanning(false));
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <strong>Antalya Transit</strong>
          <span className={online ? "status on" : "status"}>{online ? "Online" : "Offline"}</span>
        </div>
        <p className="sub">
          {online
            ? "Schedules are stored. Live buses load from KentKart."
            : "Offline. Lines and times you have opened stay available. Live buses need a connection."}
        </p>
      </header>
      <nav className="tabs" aria-label="Sections">
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={tab === item}
            onClick={() => {
              if (item === tab) {
                return;
              }
              setTab(item);
              setQuery("");
              if (item !== "Lines") {
                setRouteId(null);
                setStopId(null);
              }
            }}
          >
            {item}
          </button>
        ))}
      </nav>
      <div className="tools">
        {tab === "Plan" && (
          <>
            <div className="plan-bar">
              <button type="button" className="end" aria-pressed={activeEnd === "start"} onClick={() => setActiveEnd("start")}>
                <small>Start</small>
                {start?.label ?? "Choose a start"}
              </button>
              <button type="button" className="end" aria-pressed={activeEnd === "end"} onClick={() => setActiveEnd("end")}>
                <small>End</small>
                {end?.label ?? "Choose an end"}
              </button>
              <button type="button" onClick={useMyLocation}>My location</button>
              <button type="button" className="primary" disabled={!start || !end || planning} onClick={findRoutes}>
                {planning ? "Looking…" : "Find routes"}
              </button>
            </div>
            <label className="search">
              <span className="sr">Search a stop for the {activeEnd}</span>
              <input
                value={query}
                placeholder={activeEnd === "start" ? "Search a stop for the start" : "Search a stop for the end"}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </>
        )}
        {tab === "Nearby" && (
          <div className="plan-bar">
            <button type="button" className="primary" onClick={useMyLocation}>Stops near me</button>
            <p className="sub">Showing stops around {here.label}. Distance is a straight line.</p>
          </div>
        )}
        {tab === "Lines" && (
          <>
            <label className="search">
              <span className="sr">Search lines and stops</span>
              <input value={query} placeholder="Search a line or a stop" onChange={(event) => setQuery(event.target.value)} />
            </label>
            <div className="modes" role="group" aria-label="Vehicle type">
              {MODES.map((item) => (
                <button key={item} type="button" aria-pressed={mode === item} onClick={() => setMode(item)}>
                  {item}
                </button>
              ))}
            </div>
          </>
        )}
        {tab === "Study" && <p className="sub">Stored study area, timetable check, and Wednesday morning hospital times.</p>}
        {query.trim().length >= 2 && tab !== "Study" && (
          <ul className="search-results">
            {matches.length === 0 && <li>No match.</li>}
            {matches.map((item) =>
              item.kind === "route" ? (
                <li key={`r-${item.id}`}>
                  <button type="button" onClick={() => openRoute(item.id)}>
                    <b style={{ color: item.color }}>{item.short_name}</b> {item.long_name}
                    <span>{item.type_label}</span>
                  </button>
                </li>
              ) : (
                <li key={`s-${item.id}`}>
                  <button
                    type="button"
                    onClick={() => (tab === "Plan" ? place({ lat: item.lat, lon: item.lon, label: item.name }) : openStop(item.id))}
                  >
                    {item.name}
                    <span>{tab === "Plan" ? `Use as ${activeEnd}` : "Stop"}</span>
                  </button>
                </li>
              ),
            )}
          </ul>
        )}
      </div>
      {tab === "Study" ? (
        <StudyView />
      ) : (
        <div className="stage">
          <TransitMap
            stops={mapStops}
            direction={direction}
            color={route?.color ?? "#3b82f6"}
            buses={liveRoute?.vehicles ?? []}
            selectedStopId={stopId}
            places={tab === "Plan" ? places : []}
            onMapClick={
              tab === "Plan" && !routeId
                ? (lat, lon) => place({ lat, lon, label: "Pinned on the map" })
                : undefined
            }
            onStop={(id) => {
              const stop = stops.find((item) => item.id === id);
              if (tab === "Plan" && !routeId && stop) {
                place({ lat: stop.lat, lon: stop.lon, label: stop.name });
                return;
              }
              openStop(id);
            }}
          />
          <aside className="sheet" aria-live="polite">
            {error && <p className="error">{error}</p>}
            {detailError && <p className="error">{detailError}</p>}
            {!catalog && !error && <p>Loading the schedule…</p>}
            {catalog && <p className="note">{catalog.schedule_note}</p>}
            {board && (
              <StopSheet
                board={board}
                live={online ? liveStop : null}
                online={online}
                onLine={(id) => openRoute(id)}
                onBack={() => setStopId(null)}
              />
            )}
            {!board && tab === "Plan" && (
              <PlanSheet
                journey={journey}
                planning={planning}
                planError={planError}
                start={start}
                end={end}
                routeOpen={Boolean(route)}
                onOpen={openRoute}
                onBack={() => {
                  setRouteId(null);
                  setStopId(null);
                }}
              />
            )}
            {!board && tab === "Plan" && route && direction && (
              <RouteSheet
                route={route}
                directionId={direction.direction_id}
                onDirection={setDirectionId}
                onStop={openStop}
                live={online ? liveRoute : null}
                online={online}
              />
            )}
            {!board && tab === "Nearby" && <NearbyList stops={nearby} onStop={openStop} />}
            {!board && tab === "Lines" && route && direction && (
              <RouteSheet
                route={route}
                directionId={direction.direction_id}
                onDirection={setDirectionId}
                onStop={openStop}
                live={online ? liveRoute : null}
                online={online}
              />
            )}
            {!board && tab === "Lines" && !route && catalog && (
              <p>Search a line or a stop, or choose one on the map. Open it once while online and the schedule stays on this device.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function PlanSheet({
  journey,
  planning,
  planError,
  start,
  end,
  routeOpen,
  onOpen,
  onBack,
}: {
  journey: Journey | null;
  planning: boolean;
  planError: string | null;
  start: Place | null;
  end: Place | null;
  routeOpen: boolean;
  onOpen: (routeId: string, directionId: number) => void;
  onBack: () => void;
}) {
  if (routeOpen) {
    return (
      <button type="button" className="back" onClick={onBack}>
        Back to these routes
      </button>
    );
  }
  return (
    <>
      <h2>Route</h2>
      <p>Select Start or End, then tap the map or search a stop. The blue pin is the start and the white pin is the end.</p>
      {(!start || !end) && <p>Both ends are needed before a route can be looked up.</p>}
      {planError && <p className="error">{planError}</p>}
      {planning && <p>Looking up today’s timetable…</p>}
      {journey && (
        <>
          <p className="note">
            Stops within {journey.origin_radius_m} m of the start and {journey.destination_radius_m} m of the end. {journey.walk_note}
          </p>
          {journey.options.length === 0 && <p>{journey.empty_note}</p>}
          {journey.options.map((option, index) =>
            option.kind === "direct" ? (
              <DirectCard key={`${option.route_id}-${option.departure}-${index}`} option={option} onOpen={onOpen} />
            ) : (
              <TransferCard key={`${option.route_id}-${option.second_route_id}-${index}`} option={option} onOpen={onOpen} />
            ),
          )}
        </>
      )}
    </>
  );
}

function DirectCard({ option, onOpen }: { option: DirectRide; onOpen: (routeId: string, directionId: number) => void }) {
  return (
    <article className="card">
      <button type="button" onClick={() => onOpen(option.route_id, option.direction_id)}>
        <b className="chip" style={{ background: option.color }}>{option.short_name}</b>
        {option.headsign || option.long_name}
      </button>
      <p>
        {option.departure} from {option.board_stop_name}. Arrive {option.arrival} at {option.alight_stop_name}.
      </p>
      <p className="muted">
        {option.ride_minutes == null ? "The timetable has no ride duration." : `${option.ride_minutes} min on the bus`}
        {` · ${option.stops_between} stops · walk ${option.walk_to_minutes} min to the stop and ${option.walk_from_minutes} min after, in a straight line`}
      </p>
    </article>
  );
}

function TransferCard({ option, onOpen }: { option: TransferRide; onOpen: (routeId: string, directionId: number) => void }) {
  return (
    <article className="card">
      <p>
        <button type="button" onClick={() => onOpen(option.route_id, option.direction_id)}>
          <b className="chip" style={{ background: option.color }}>{option.short_name}</b>
        </button>
        then
        <button type="button" onClick={() => onOpen(option.second_route_id, option.second_direction_id)}>
          <b className="chip" style={{ background: option.second_color }}>{option.second_short_name}</b>
        </button>
      </p>
      <p>
        {option.departure} from {option.board_stop_name}. Change at {option.change_stop_name} ({option.change_arrival}), wait{" "}
        {option.wait_minutes == null ? "an unknown time" : `${option.wait_minutes} min`}, then {option.second_departure}. Arrive{" "}
        {option.arrival} at {option.alight_stop_name}.
      </p>
      <p className="muted">
        One change. Both times are the published timetable.
        {option.ride_minutes == null ? "" : ` ${option.ride_minutes} min riding, plus the wait.`}
        {` Walk ${option.walk_to_minutes} min to the first stop and ${option.walk_from_minutes} min from the last, in a straight line.`}
      </p>
    </article>
  );
}

function NearbyList({ stops, onStop }: { stops: Array<StopPoint & { metres: number }>; onStop: (id: string) => void }) {
  return (
    <>
      <h2>Nearby stops</h2>
      {stops.length === 0 && <p>Stops are still loading.</p>}
      <ul className="lines">
        {stops.map((stop) => (
          <li key={stop.id}>
            <button type="button" onClick={() => onStop(stop.id)}>
              {stop.name}
              <span>
                {stop.metres} m · about {walkMinutes(stop.metres)} min in a straight line
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function RouteSheet({
  route,
  directionId,
  onDirection,
  onStop,
  live,
  online,
}: {
  route: RouteDetail;
  directionId: number;
  onDirection: (directionId: number) => void;
  onStop: (stopId: string) => void;
  live: LiveRoute | null;
  online: boolean;
}) {
  const direction = route.directions.find((item) => item.direction_id === directionId) ?? route.directions[0];
  const upcoming = direction.departures.filter((item) => item.upcoming);
  const earlier = direction.departures.filter((item) => !item.upcoming);
  const buses = (live?.vehicles ?? []).filter((bus) => bus.direction_id === direction.direction_id);
  return (
    <>
      <h2>
        <span className="chip" style={{ background: route.color }}>{route.short_name}</span>
        {route.long_name}
      </h2>
      <p className="muted">
        {route.type_label}
        {route.service_label ? ` · ${route.service_label} ${route.service_date}` : ""}
      </p>
      {route.directions.length > 1 && (
        <div className="modes">
          {route.directions.map((item) => (
            <button
              key={item.direction_id}
              type="button"
              aria-pressed={item.direction_id === direction.direction_id}
              onClick={() => onDirection(item.direction_id)}
            >
              {item.headsign || `Direction ${item.direction_id + 1}`}
            </button>
          ))}
        </div>
      )}
      <h3>Departures</h3>
      {upcoming.length === 0 && earlier.length === 0 && <p>No departures are stored for this direction today.</p>}
      <div className="times">
        {(upcoming.length > 0 ? upcoming : earlier).slice(0, 16).map((item) => (
          <span key={item.gtfs_time}>{item.time}</span>
        ))}
      </div>
      {upcoming.length > 0 && earlier.length > 0 && (
        <details>
          <summary>Earlier today</summary>
          <div className="times">
            {earlier.map((item) => (
              <span key={item.gtfs_time}>{item.time}</span>
            ))}
          </div>
        </details>
      )}
      <h3>Stops</h3>
      <ol className="stops">
        {direction.stops.map((stop) => (
          <li key={`${stop.sequence}-${stop.stop_id}`}>
            <button type="button" onClick={() => onStop(stop.stop_id)}>
              {stop.name}
            </button>
          </li>
        ))}
      </ol>
      <LiveBlock online={online} available={live?.available ?? false} note={live?.traffic_note}>
        {buses.length === 0 && <p>No bus is currently reported on this direction.</p>}
        <ul className="buses">
          {buses.map((bus) => (
            <li key={bus.id || `${bus.latitude}-${bus.longitude}`}>
              <b>{bus.plate || "Bus"}</b>
              {bus.speed_kmh != null && <span>{bus.speed_kmh} km/h reported</span>}
              {bus.nearest_stop_name && <span>Nearest stop {bus.nearest_stop_name}</span>}
            </li>
          ))}
        </ul>
      </LiveBlock>
    </>
  );
}

function StopSheet({
  board,
  live,
  online,
  onLine,
  onBack,
}: {
  board: StopBoard;
  live: LiveStop | null;
  online: boolean;
  onLine: (routeId: string) => void;
  onBack?: () => void;
}) {
  const liveByRoute = new Map((live?.arrivals ?? []).map((item) => [`${item.route_id}-${item.headsign}`, item]));
  return (
    <>
      {onBack && (
        <button type="button" className="back" onClick={onBack}>
          Back
        </button>
      )}
      <h2>{board.name}</h2>
      <p className="muted">Stop {board.id}</p>
      <h3>Next departures</h3>
      {board.lines.length === 0 && <p>No departures are stored at this stop for today.</p>}
      <ul className="lines">
        {board.lines.map((line) => {
          const arrival = liveByRoute.get(`${line.route_id}-${line.headsign}`);
          return (
            <li key={`${line.route_id}-${line.direction_id}-${line.headsign}`}>
              <button type="button" onClick={() => onLine(line.route_id)}>
                <b style={{ color: line.color }}>{line.short_name}</b> {line.headsign || line.long_name}
              </button>
              <div className="times">
                {line.departures.map((item) => (
                  <span key={item.gtfs_time}>{item.time}</span>
                ))}
              </div>
              {online && arrival && (
                <p className="eta">
                  {arrival.eta_minutes != null
                    ? `About ${arrival.eta_minutes} min from the last reported position`
                    : arrival.stops_away != null
                      ? `${arrival.stops_away} stops from the last reported position`
                      : "No live bus is close enough to estimate an arrival"}
                  {arrival.plate ? ` · ${arrival.plate}` : ""}
                  {arrival.speed_kmh != null ? ` · ${arrival.speed_kmh} km/h reported` : ""}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <LiveBlock online={online} available={live?.available ?? false} note={live?.estimate_note ?? live?.traffic_note} />
    </>
  );
}

function LiveBlock({
  online,
  available,
  note,
  children,
}: {
  online: boolean;
  available: boolean;
  note?: string;
  children?: ReactNode;
}) {
  return (
    <section className="live">
      <h3>Live</h3>
      {!online && <p>Live bus positions and reported speeds need a connection.</p>}
      {online && !available && <p>The live feed did not respond. Scheduled times above are unchanged.</p>}
      {online && children}
      {online && note && <p className="note">{note}</p>}
    </section>
  );
}

function nearestStops(stops: StopPoint[], lat: number, lon: number, limit: number): StopPoint[] {
  return [...stops]
    .sort((a, b) => distanceMetres(lat, lon, a.lat, a.lon) - distanceMetres(lat, lon, b.lat, b.lon))
    .slice(0, limit);
}

function search(
  routes: RouteCatalog["routes"],
  stops: StopPoint[],
  query: string,
  mode: (typeof MODES)[number],
  tab: Tab,
) {
  const text = query.trim().toLocaleLowerCase("tr");
  if (text.length < 2) {
    return [];
  }
  const stopHits = stops
    .filter((stop) => stop.name.toLocaleLowerCase("tr").includes(text))
    .slice(0, 8)
    .map((stop) => ({ kind: "stop" as const, ...stop }));
  if (tab === "Plan") {
    return stopHits;
  }
  const routeHits = routes
    .filter((route) => mode === "All" || route.type_label === mode)
    .filter((route) => `${route.short_name} ${route.long_name}`.toLocaleLowerCase("tr").includes(text))
    .slice(0, 8)
    .map((route) => ({ kind: "route" as const, ...route }));
  return [...routeHits, ...(mode === "All" ? stopHits : [])];
}
