# Raw datasets used by the code

Every file the notebooks read from `data/raw/`. Paths are relative to the project root. Most are **inputs**
you supply; a few are **built once** by the pipeline and then read back from `data/raw/`.

| # | File / folder (`data/raw/…`) | Format | Used by | What it is | Source |
|---|---|---|---|---|---|
| 1 | `osm_network_20261023.graphml` | GraphML | NB01, NB02 | Pinned OpenStreetMap network snapshot (cordon + 500 m buffer). Download date 23 Oct 2026. | OpenStreetMap via OSMnx (`graph_from_polygon`) |
| 2 | `osm_way_timestamps.csv` | CSV | NB01 (A.5) | Per-OSM-way last-edit dates, for the data-freshness audit. | OSM way metadata (cached) |
| 3 | `Sydney_stats.csv` | CSV | NB01 (A.2) | **Compass IoT GPS probe speeds** (`osmid`, `p80`, `p90`) — the top of the speed hierarchy. Toggle: `USE_COMPASS_SPEED`. | Compass IoT |
| 4 | `nsw_speed_zones.shp/Speed_Zones.shp` (or `nsw_speed_zones/…`) | Shapefile | NB01 (A.3.1) | TfNSW signposted speed-zone polygons (secondary speed source). | Transport for NSW |
| 5 | `Traffic_Volume_Viewer_-_*_Data.csv` (yearly glob) | CSV | NB01 (A.4) | TfNSW Traffic Volume Viewer motor-vehicle counts, one file per year, for class ADT. | Transport for NSW |
| 6 | `sydney_elvis_dem.tif` *or* `elvis_tiles/*.tif` | GeoTIFF | NB01 (A.6) | ELVIS 1 m bare-earth DEM (merged, or tiles mosaicked at run time) for edge gradients. | Geoscience Australia ELVIS |
| 7 | `SA1s_in_Boundary_Cordon.csv` | CSV | NB01, NB03, NB04 | SA1 zones inside the cordon (`SA1_MAIN16`, `SA1_7DIG16`, `X`, `Y`, `SA2_NAME16`). | ABS + study-area selection |
| 8 | `SA2s_Outside_Boundary_Cordon.csv` | CSV | NB03, NB04 | External SA2 zones (`SA2_MAIN16`, `SA2_NAME16`, `X`, `Y`). | ABS + study-area selection |
| 9 | `DZNs_in_Boundary_Cordon.csv` | CSV | NB03, NB04 | Destination Zones inside the cordon (`DZN_CODE_2016`, `X`, `Y`). | ABS + study-area selection |
| 10 | `1270055001_sa1_2016_aust_shape.zip` | Zipped shapefile | NB01 (0.2), NB04 | ABS 2016 SA1 polygons (national); filtered to the cordon list. | ABS ASGS 2016 |
| 11 | `1270055001_sa2_2016_aust_shape.zip` | Zipped shapefile | NB04 | ABS 2016 SA2 polygons (national). | ABS ASGS 2016 |
| 12 | `80000_dzn_2016_aust_geopackage/ASGS 2016 DZN.gpkg` | GeoPackage | NB03, NB04 | ABS 2016 Destination Zone polygons. | ABS ASGS 2016 |
| 13 | `SA1_to_DZN_20260928.csv` | CSV (TableBuilder) | NB03 | JTW matrix: SA1 (inside) → DZN (inside) — inbound internal. | ABS 2016 Census JTW (TableBuilder) |
| 14 | `SA2_to_DZN_20260929.csv` | CSV (TableBuilder) | NB03 | JTW matrix: SA2 (outside) → DZN (inside) — inbound external. | ABS 2016 Census JTW (TableBuilder) |
| 15 | `SA1inside_to_DZNoutside_20261002.csv` | CSV (TableBuilder) | NB03 | JTW matrix: SA1 (inside) → DZN (outside) — outbound. | ABS 2016 Census JTW (TableBuilder) |
| 16 | `SA2outside_to_DZNoutside_20261003.csv` | CSV (TableBuilder) | NB03 | JTW matrix: SA2 (outside) → DZN (outside) — through. | ABS 2016 Census JTW (TableBuilder) |
| 17 | `cordon_entry_points.csv` | CSV | NB03, NB04 | The 30 cordon entry/exit points (`entry_id`, `x`, `y`, `road_name`). | Manually defined |
| 18 | `snapping_rules.csv` | CSV | NB03 | Corridor share-split rules mapping external SA2s to entry points (`sa2_code`, `entry_id`, `share`). | Manually defined |
| 19 | `bicycle_count_sites.csv` | CSV | NB04 | Count-station register (`SiteID`, `lon`, `lat`). | City of Sydney |
| 20 | `cos_bicycle_counts_2016_2026.csv` | CSV | NB04, NB06, NB09a, NB09b | City of Sydney bicycle count survey (hourly `Time_*`, `Year`, `Month`, `SiteID`). Defines `v_obs` and the count-variance `W`. | City of Sydney |
| 21 | `Shape File.zip` | Zipped shapefile | NB08 | CoS bike-network shapefile (route, status, network-role, geometry) for the planned-corridor scenarios. | City of Sydney |
| 22 | `study_area_cordon.gpkg` | GeoPackage | NB01, NB02, NB04, NB07, NB08 | Dissolved study-area cordon polygon. **Built once** by NB01 §0.2 from #10 + #7, then read back from `data/raw/`. | Produced by NB01 |
| 23 | `station_names_override.csv` *(optional)* | CSV | NB04 (J) | Optional manual station-name overrides (`SiteID`, `label`). Skipped if absent. | Optional, user-supplied |

Notes:
- `thesis_common.py` reads the count survey (#20) via `tc.load_survey()` and the cordon (#22) via
  `tc.STUDY_AREA_CORDON`; it does not read any other raw file directly.
- `osm_network_*.graphml` is downloaded by NB01 §A.1 when `FORCE_REFRESH=True`; the final run uses the
  **pinned** `OSM_SNAPSHOT` so results are reproducible. NB02 reads the exact file named in
  `data/processed/osm_snapshot.txt`.
- The TableBuilder exports (#13–#16) and the ABS shapefiles (#10–#12) are large national files; only the
  cordon-relevant rows/polygons are kept at load time.
