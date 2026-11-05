# Antalya Transit

A public-transport app for Antalya, with the earlier study of the central network kept beside it.

Schedules, lines, stops, and departure times stay available after they have been opened once. While the device is online, a line or a stop also shows live bus positions, the speed a bus reports, and an arrival estimate. Arrival minutes are the scheduled running time from the stop nearest a live bus. They are not an operator prediction. There is no road-traffic feed, so the app does not draw a traffic map.

## Getting around

Plan is the first screen. Choose Start or End, tap the map or search a stop, then Find routes. A blue pin is the start and a white pin is the end. My location fills the start when the browser allows it.

A result is a scheduled ride. If one bus does the trip, the card shows that line, the departure, the arrival, and the ride. If not, the app looks for one change of bus and shows the wait. Walk minutes are a straight line at 4.8 km/h to the nearest stops, not a street path. The search uses stops within 700 m, or 1,200 m when nothing is closer.

Nearby lists the closest stops and opens the board for that stop. Lines searches the stored network. Online, a live bus is drawn on the line you have open.

## Study

The Study tab keeps the earlier measurement. The area is 237.03 km². 2,794 stops are inside it and 1,836 are outside. 102 lines are included because at least 80 percent of their stops are inside, and 67 are excluded. The Wednesday timetable check matched 14 of 16 comparable patterns; VF01 and UC32 did not. Each check is one published gidiş pattern, not the full day.

The coloured cells are a Wednesday 21 October 2026, 07:00 hospital journey. The minutes are scheduled and modelled. 944 cells have a hospital within 30 minutes, 1,505 within 45, and 1,809 within 60, out of 1,881. A cell with no time means no journey was returned within the cutoff. Click a cell to read the stored result. The Kaleiçi example is a 4 minute walk to Kale Kapı 3 and 19 minutes to Özel Antalya Anadolu Hastanesi.

## Run

Requirements: Python 3.12 and Docker Desktop. The schedule has to be imported once.

```powershell
python -m pip install -e ".[dev]"
docker compose up db -d
urbanpulse data download
urbanpulse data validate
urbanpulse data import
docker compose up --build
```

Open `http://127.0.0.1:8080`. The first visit needs the API. After that, the app shell and the lines you opened are cached for offline use. Finding a new route needs the API. Map tiles are cached only for areas the map has already shown.

## Tests

```powershell
pytest
```

## Data

The schedule is a KentKart-derived GTFS imported into PostGIS. It is not an official municipal file. The stored calendar runs from 1 September 2026 to 31 October 2026 and has weekday, Saturday, and Sunday services. Public holidays are not listed separately. Raw GTFS is not committed. The converter README says the underlying schedule remains with KentKart and the municipality.

Live requests go to `https://service.kentkart.com/rl1/web/pathInfo` for region `026`. OpenStreetMap provides the basemap. The hospital layer is the stored Wednesday-morning result, not a live observation.
