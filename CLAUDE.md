# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A five-stage GIS pipeline (not an application, not a library) that finds BLM-administered land
in Utah where Utah juniper (*Juniperus osteosperma*) actually grows, and names the BLM field
office that issues the transplant permit for each parcel. Output is a QGIS project plus CSV /
Markdown / KML / GPX deliverables. There is no test suite and no linter config.

## Commands

`justfile` wraps everything (`just --list`). The pipeline stages are strictly ordered — each
reads what the previous one wrote:

```
just setup                # uv venv + deps
just fetch                # 01 sources        -> data/raw/*.gpkg   (cached; re-runs are free)
just landfire             # 02 EVT tiles      -> data/work/juniper_class.vrt
just overlay              # 03 cross-ref      -> out/juniper_blm.gpkg + out/funnel.csv
just export               # 04 deliverables   -> out/*.csv, summary.md, scouting.kml/.gpx
just qgis                 # 05 map            -> juniper_blm.qgs
just all                  # all five in order
just clean                # drop out/ and the .qgs, keep the data/ download cache
just clean-all            # also drop data/ — forces a full re-download
```

Scripts take no arguments; everything is a module-level constant. They can also be run
directly, but note the two-interpreter split:

* `01`–`04` use `.venv/bin/python` (geopandas, rasterio, exactextract, simplekml, gpxpy).
* `05` **must** use `/usr/bin/python3` — PyQGIS is a system package and is not installed in
  the venv. It sets `QT_QPA_PLATFORM=offscreen` so it runs headless.

`data/` and `out/` are gitignored; `juniper_blm.qgs` at the repo root is a committed artifact.

## Architecture

The screening is a funnel across three layers, each doing a distinct job — this is the core
idea and is why no single layer can be dropped:

| Layer | Job | Why it alone is insufficient |
|---|---|---|
| BLM Utah SMA polygons | jurisdiction (who issues the permit) | says nothing about vegetation |
| Little (1971) *J. osteosperma* range | species filter | 1:2M hand-drawn; blankets most of Utah (`range ∩ BLM` ≈ 11 M acres) |
| LANDFIRE 2023 EVT, 30 m | stand locator | class names can't distinguish *osteosperma* from *scopulorum* / *monosperma* |

`scripts/03_overlay.py` applies them in that order and records each narrowing step in a
`funnel` list, written to `out/funnel.csv` and rendered as the acreage table in `summary.md`.
Wilderness / WSA / NM-NCA are subtracted as hard exclusions; lands with wilderness
characteristics are kept but flagged `in_lwc`.

Two spatial units come out of stage 03 and everything downstream keys off them:
* **`candidates`** — eligible BLM parcels, one row per polygon, ranked by `juniper_acres`.
* **`hotspots`** — 1 km² flat-top hexagons clipped to eligible land, ≥ `HOTSPOT_MIN_PCT`
  juniper. Hexes rather than squares so the lattice has no dominant axis over the terrain.

All layers live in the single `out/juniper_blm.gpkg` (`candidates`, `hotspots`, `blm_all`,
`juniper_range`, `exclusions`, `field_offices`, `office_points`); stages 04 and 05 both read
only from it.

### Projections

`common.py` pins two CRSs and code moves between them deliberately: `CRS = EPSG:26912`
(NAD83 / UTM 12N) is the working projection — BLM Utah publishes in it and areas come out
metric, so all acreage math must happen there. `CRS_LF = EPSG:5070` is the LANDFIRE CONUS
grid; `zonal_juniper()` reprojects to it only for the `exact_extract` call. Lat/lon (4326)
appears only at export time.

### Stage 02 detail

Raw EVT over Utah is ~1 GB. `02_landfire.py` fetches 4096 px tiles and immediately remaps each
through a LUT to `uint8` (0 = not juniper, 1..N = juniper community), then `gdalbuildvrt`s
them into `data/work/juniper_class.vrt`. `data/work/juniper_codes.csv` is the code → EVT name
mapping that stages 03/04 use for `dominant_evt`. Tiles are cached individually by index.

### Where the knobs are

* `scripts/03_overlay.py` — `MIN_JUNIPER_ACRES`, `HOTSPOT_KM2`, `HOTSPOT_MIN_PCT`,
  `EXCLUDED_DESIG`.
* `scripts/04_export.py` — `N_WAYPOINTS`, `MIN_SEPARATION_M`. `spread()` round-robins over
  field offices rather than greedily taking top cells, because thousands of cells tie at 100 %
  juniper and a plain greedy pass returns 50 neighbours in one canyon.
* `scripts/05_qgis_project.py` — `RAMP_CELLS`, `RAMP_PARCELS`, `EXCLUDED`, `RANGE`, `BLM_WASH`.

### Cartography constraints (stage 05)

The palette exists to sit on top of aerial imagery, so changes to it are not free. Adjacent
ramp steps hold ΔE ≥ 20 in normal vision and ≥ 15.8 under simulated CVD, and the two ramps
occupy different hue families deliberately: juniper canopy is green and Utah dirt is brown/tan,
so those hues are avoided; red is reserved for exclusions and violet for Little's range.
Layer draw order matters — the parcel shapeburst is added *after* the hex cells so it draws on
top, and it is transparent in the middle so it rims parcels without hiding the cells beneath.
`SHAPEBURST_M` is in ground metres on purpose; a fixed screen width floods small parcels solid
cyan when zoomed out.

### Network fragility

BLM and USGS endpoints time out intermittently. `common.get()` retries with linear backoff and
`common.esri_features()` handles ArcGIS paging (`exceededTransferLimit`). Stage 01 asserts
fetched feature counts against `esri_count()` so silent paging loss fails loudly. NLCS services
publish a boundary-*line* layer first — the polygon layer is index 1 (index 2 for wild & scenic
rivers).

## Domain caveats worth preserving

Results are a screening tool, not an authorization. The pipeline does not model ACECs, grazing
or mineral leases, rights-of-way, sage-grouse habitat, developed recreation sites, riparian
buffers, or cultural-resource restrictions. LANDFIRE EVT is *modelled* cover, and
pinyon-juniper classes contain pinyon pine. The generated `summary.md` says all of this — keep
it saying it.
