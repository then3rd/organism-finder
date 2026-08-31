# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A five-stage GIS pipeline (not an application, not a library) that finds BLM-administered land
in Utah where a given tree actually grows, and names the BLM field office that issues the
transplant permit for each parcel. Output is a QGIS project plus CSV / Markdown / KML / GPX
deliverables. There is no test suite and no linter config.

Which tree is a run-time argument. Utah juniper (*Juniperus osteosperma*, slug `junioste`) is
the default and the species the pipeline was built around; four more Utah trees are registered.

## Commands

`justfile` wraps everything (`just --list`). Every stage recipe takes a species slug, defaulting
to `junioste`. The pipeline stages are strictly ordered — each reads what the previous one wrote:

```
just setup                # uv venv + deps
just species              # list the registry
just evt-classes pinyon   # which LANDFIRE classes a keyword selects
just fetch      [slug]    # 01 sources    -> data/raw/**        (cached; re-runs are free)
just landfire   [slug]    # 02 EVT tiles  -> data/work/<slug>/class.vrt
just overlay    [slug]    # 03 cross-ref  -> out/<slug>/<slug>_blm.gpkg + funnel.csv
just export     [slug]    # 04 deliverables -> out/<slug>/*.csv, summary.md, scouting.kml/.gpx
just qgis       [slug]    # 05 map        -> <slug>_blm.qgs
just all        [slug]    # all five in order
just clean                # drop out/ and the .qgs files, keep the data/ download cache
just clean-all            # also drop data/ — forces a full re-download
```

Scripts take one optional argument (the slug); every other setting is a module-level constant
or a registry field. Note the two-interpreter split:

* `01`–`04` use `.venv/bin/python` (geopandas, rasterio, exactextract, simplekml, gpxpy).
* `05` **must** use `/usr/bin/python3` — PyQGIS is a system package and is not installed in
  the venv. It sets `QT_QPA_PLATFORM=offscreen` so it runs headless.

`data/` and `out/` are gitignored; `<slug>_blm.qgs` at the repo root is a committed artifact.

## Architecture

The screening is a funnel across three layers, each doing a distinct job — this is the core
idea and is why no single layer can be dropped:

| Layer | Job | Why it alone is insufficient |
|---|---|---|
| BLM Utah SMA polygons | jurisdiction (who issues the permit) | says nothing about vegetation |
| Little (1971) species range | species filter | 1:2M hand-drawn; for juniper it blankets most of Utah (`range ∩ BLM` ≈ 11 M acres) |
| LANDFIRE 2023 EVT, 30 m | stand locator | classes are plant *communities*, so they can't separate congeners — *osteosperma* from *scopulorum*, *edulis* from *monophylla* |

`scripts/03_overlay.py` applies them in that order and records each narrowing step in a
`funnel` list, written to `out/<slug>/funnel.csv` and rendered as the acreage table in
`summary.md`. Wilderness / WSA / NM-NCA are subtracted as hard exclusions; lands with
wilderness characteristics are kept but flagged `in_lwc`.

Three spatial units come out of stage 03 and everything downstream keys off them:
* **`candidates`** — eligible BLM parcels, one row per polygon, ranked by `species_acres`.
* **`hotspots`** — 1 km² flat-top hexagons clipped to eligible land, ≥ `HOTSPOT_MIN_PCT`
  cover. Hexes rather than squares so the lattice has no dominant axis over the terrain.
* **`off_blm_cells`** — the same grid, same scoring pass, over the exact complement:
  region ∩ range minus every BLM SMA polygon. Context for reading the map, never a target —
  there is no field office to issue a permit on that ground, so the rows carry `county` but
  no `field_office`, the acreage column is `cell_acres` rather than `blm_acres`, and stage
  04 derives nothing from them. `grid_over()` builds both grids so the two are comparable
  cell for cell.

All layers live in the single `out/<slug>/<slug>_blm.gpkg` (`candidates`, `hotspots`,
`off_blm_cells`, `blm_all`, `species_range`, `exclusions`, `field_offices`,
`office_points`); stages 04 and 05 both read only from it. Cover columns are `species_pct` / `species_acres` — deliberately
species-neutral, because which tree they describe is the slug in the path.

### The two axes: species and region

Three small modules hold everything that varies, and **all three are standard-library only** —
stage 05 imports them under the system interpreter, where geopandas does not exist. Do not add
a third-party import to `paths.py`, `species.py`, or `region.py`.

* `scripts/species.py` — the registry, keyed by USTreeAtlas slug. Each `Species` carries the
  slug (Little's range is a URL swap), `evt_include` / `evt_exclude` keywords, an optional
  `little_polygons` count asserted at fetch time, and `ground_truth_caveat` (per-species prose
  that lands in `summary.md`).
* `scripts/region.py` — jurisdiction service URLs, the working CRS, the county filter, and
  `excluded_desig`. Only `UTAH` ships and region is not yet selectable at run time, but every
  state-specific fact lives here so adding a state is a data entry. BLM publishes the same
  service families per state office; layer indices and SMA attribute values do vary.
* `scripts/paths.py` — every path. Nothing else builds one by concatenation. `out_dir()`
  returns `out/<slug>/` today and becomes `out/<region>/<slug>/` with no caller changes.

Stages 03/04/05 contain no species logic at all — only registry lookups for display strings.

### Adding a species

The slug is mechanical; the EVT keywords are a judgment call and are the whole reason the
registry exists. `just evt-classes <keyword>` prints matching class names from the cached
attribute table — tune there first, then write the entry. Some trees have no EVT class of their
own and simply cannot be screened this way.

Pinyon and juniper deliberately draw on overlapping classes (the pinyon-juniper communities);
Little's range polygon is what separates the two maps. A 404 on the slug and an empty EVT match
are both hard errors — the failure this guards against is a plausible-looking empty map.

### Projections

Stage 05 pins a NAD83 → WGS 84 operation into the project's transform context; without it
QGIS interrupts every open to ask which of the dozen published operations to use, because
the layers are NAD83 and the XYZ basemap is WGS 84. `best_operation()` asks QGIS for the
most accurate pipeline the machine can actually run rather than writing one out, so the
answer improves by itself if the NADCON5 grids ever get installed. Note what QGIS stores
per CRS pair is the **whole** transformation, not just the datum shift — a bare
`+proj=noop` there skips the UTM-to-Mercator step as well and puts Utah in the
Mediterranean.

`region.py` pins the working CRS and `common.py` the LANDFIRE grid; code moves between them
deliberately. `Region.crs = EPSG:26912` (NAD83 / UTM 12N) is the working projection — BLM Utah
publishes in it and areas come out metric, so all acreage math must happen there.
`CRS_LF = EPSG:5070` is the LANDFIRE CONUS grid; `zonal_species()` reprojects to it only for
the `exact_extract` call. Lat/lon (4326) appears only at export time.

### Stage 02 detail

Raw EVT over Utah is ~1 GB. `02_landfire.py` fetches 4096 px tiles and caches them **twice**:
the raw S16 tile under `data/work/evt/<region>/`, then a `uint8` remap through the species LUT
under `data/work/<slug>/` (0 = not this tree's community, 1..N = which one). The raw cache is
region-scoped and species-free, so the first species pays the download and every later species
is local work only — that split is the point, not an optimisation. `gdalbuildvrt` assembles the
class tiles into `data/work/<slug>/class.vrt`; `evt_codes.csv` alongside it is the code → EVT
name mapping stages 03/04 use for `dominant_evt`. Both caches are keyed by tile index.

### Where the knobs are

* `scripts/03_overlay.py` — `MIN_SPECIES_ACRES`, `HOTSPOT_KM2`, `HOTSPOT_MIN_PCT`,
  `OFFBLM_MIN_ACRES`. The off-BLM grid covers several times the BLM one's area, so it is
  the slow half of the stage.
* `scripts/region.py` — `excluded_desig`, service URLs, working CRS.
* `scripts/04_export.py` — `N_WAYPOINTS`, `N_TABLE`, `MIN_SEPARATION_M`. `spread()` round-robins
  over field offices rather than greedily taking top cells, because thousands of cells tie at
  100 % cover and a plain greedy pass returns 50 neighbours in one canyon.
* `scripts/05_qgis_project.py` — `RAMP_CELLS`, `RAMP_PARCELS`, `RAMP_OFFBLM`, `EXCLUDED`,
  `RANGE`, `BLM_WASH`.

### Cartography constraints (stage 05)

The palette exists to sit on top of aerial imagery, so changes to it are not free. Adjacent
ramp steps hold ΔE ≥ 20 in normal vision and ≥ 15.8 under simulated CVD, and the two ramps
occupy different hue families deliberately: canopy is green and Utah dirt is brown/tan, so
those hues are avoided; red is reserved for exclusions, violet for Little's range and amber
for the BLM wash. That exhausts the hue budget, so `RAMP_OFFBLM` is achromatic — four
neutral steps, because five cannot span the lightness range and still clear ΔE 20. Grey
reading as "no jurisdiction" is deliberate. This
reasoning is about ground cover, not one species, so it holds for every tree. Layer draw order
matters — the parcel shapeburst is added *after* the hex cells so it draws on top, and it is
transparent in the middle so it rims parcels without hiding the cells beneath. `SHAPEBURST_M`
is in ground metres on purpose; a fixed screen width floods small parcels solid cyan when
zoomed out. Ramp labels are format strings taking `{short}` from the registry.

## Domain caveats worth preserving

Results are a screening tool, not an authorization. The pipeline does not model ACECs, grazing
or mineral leases, rights-of-way, sage-grouse habitat, developed recreation sites, riparian
buffers, or cultural-resource restrictions. LANDFIRE EVT is *modelled* cover and its classes
are communities rather than species. The generated `summary.md` says all of this — keep it
saying it, including the per-species `ground_truth_caveat`.
