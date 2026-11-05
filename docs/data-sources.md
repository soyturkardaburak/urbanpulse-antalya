# Data sources

Phase 1 uses two geographic inputs: a KentKart-derived GTFS feed for Antalya, and an OpenStreetMap extract cropped to the study area. Population is not used. No accessibility results are derived from these files yet.

Raw files live in `data/raw/` and are gitignored. Pins and processing notes live in `data/catalog/` after the download commands run.

## Antalya GTFS

| Field | Value |
| --- | --- |
| Dataset | Antalya public transport GTFS (KentKart-derived) |
| Organization | Schedule data via KentKart. GTFS conversion by [Egezenn/kk-gtfs](https://github.com/Egezenn/kk-gtfs). |
| Source URL | `https://github.com/Egezenn/kk-gtfs/raw/main/data/antalya.zip` |
| Retrieval date | 2026-09-29T20:24:20Z, recorded in `data/catalog/antalya_gtfs.json`. |
| SHA-256 | `caff3834e75be575d2f19992b38c5650446747d38e7f026a7e5a2572ab7c145a` |
| Size | 8,960,583 bytes |
| Geographic coverage | Stop bbox longitude 29.32007–32.31751, latitude 36.20068–37.23487. The Phase 1 study area is the dense H3 component: 237.03 km², 2,794 of 4,630 valid stops inside. |
| Temporal coverage | `calendar.txt` stamps every service 20260901–20261031. That 60-day window is the converter's stamp, not an operator validity statement. |
| License | Unclear for redistribution. The converter code is MIT. The repository attaches a CC0 text to generated files and the README says the underlying data remain with KentKart and/or the municipal operator. The zip is not committed. |
| Format | GTFS zip (`agency`, `routes`, `trips`, `stops`, `stop_times`, `calendar`, `shapes`). |
| Processing | Download, SHA-256, unzip. GTFS text is not edited. Validation, the study-area polygon, and the route register are separate outputs. |
| Known limitations | No `calendar_dates`, `transfers`, `frequencies`, or wheelchair fields. Intermediate times are API offsets added to the trip departure. Agency id `99` is named TEST. Provincial routes such as Antalya–Kaş are in the same zip as urban routes. Dolmuş is absent. Alanya is a different zip and is out of scope. |

The enhanced feed republished by busmaps.com is not an input. Its corrections are undocumented.

An official GTFS file from Antalya Büyükşehir Belediyesi or Antalya Ulaşım was not found as an open download. It would replace this feed if it becomes available.

## OpenStreetMap

| Field | Value |
| --- | --- |
| Dataset | Geofabrik Turkey PBF, then a study-area crop |
| Organization | © OpenStreetMap contributors, packaged by Geofabrik |
| Source URL | `https://download.geofabrik.de/europe/turkey-latest.osm.pbf` |
| Retrieval date | 2026-09-29T20:25:37Z, recorded in `data/catalog/turkey_osm.json`. |
| SHA-256 | `0ed7f472a2310e3e6051840ea952a5c25788039def47b0f5e4b06b5dbb32226c` |
| Geofabrik MD5 | `17bcffda4280daed330c4b26bd3b1914` |
| Size | 648,273,003 bytes |
| Geographic coverage | The country file is only a staging extract. `urbanpulse data osm-crop` wrote `data/raw/osm/antalya-study.osm.pbf` (5,525,893 bytes, SHA-256 `5067a98a2c656846ebe90e4c7ebfb3a736250923ca1e964883df89636db752f3`) for the study-area bounding box plus 0.04 degrees: 30.543523, 36.799722, 30.888426, 37.041883. The extract uses `osmium extract --strategy complete_ways`. |
| Temporal coverage | The Geofabrik file date is the OSM replication date of that PBF, recorded by the file itself. |
| License | ODbL 1.0. Credit OpenStreetMap contributors. A produced database derived from OSM is share-alike. |
| Format | OSM PBF |
| Processing | MD5 check against Geofabrik, then SHA-256 pin, then crop. The country file is not loaded into PostGIS. |
| Known limitations | Pedestrian tags in Antalya are incomplete. Phase 1 does not route walks. The crop does not cover Kaş or Gazipaşa. |

The basemap is [OpenFreeMap](https://openfreemap.org/) Positron, which is itself derived from OpenStreetMap. The application credits OpenStreetMap contributors through the map attribution control.

## Not used in Phase 1

- TÜİK population. District and neighbourhood figures are not joined.
- Mahalle polygons.
- GTFS-Realtime or live KentKart vehicle positions.
- A second city's feed.

## What a reader can regenerate

```powershell
urbanpulse data download
urbanpulse data validate
urbanpulse data import
```

`reports/phase1_data_quality.html` is produced by validation. Its issues are data-quality and coverage findings. They are not accessibility measurements.
