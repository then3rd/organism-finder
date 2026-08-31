# Tree species × BLM Utah land — transplant permit screening

Finds BLM-administered land in Utah where a given tree actually grows, and tells you which BLM
field office issues the permit for it. Ships with Utah juniper (*Juniperus osteosperma*) as the
default and four more Utah trees registered; adding another is a registry entry, not a code
change.

```bash
just species          # what it knows how to screen for
just all              # Utah juniper
just all pinuedul     # two-needle pinyon
```

## The three layers and what each one is for

| Layer | Job | Source |
|---|---|---|
| BLM Utah Surface Management Agency (polygon) | jurisdiction — who issues the permit | [`BLM_UT_SMA/FeatureServer/0`](https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0) |
| Little (1971) species range | species filter | Little's *Atlas of United States Trees*, the same polygons as the Data Basin datasets, mirrored as GeoJSON in [`wpetry/USTreeAtlas`](https://github.com/wpetry/USTreeAtlas) |
| LANDFIRE 2023 Existing Vegetation Type, 30 m | stand locator | [LF2023 EVT CONUS ImageServer](https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2023/LF2023_EVT_CONUS/ImageServer) |

Little's range map alone is not enough: at 1:2,000,000 it blankets most of Utah, so for juniper
`range ∩ BLM` is ~11 million acres. LANDFIRE narrows that to ground that is genuinely woodland
of the right type. Little's range still does real work — LANDFIRE class names are *community*
names, so they cannot tell *J. osteosperma* from *J. scopulorum* / *J. monosperma*, or
*P. edulis* from *P. monophylla*; the range polygon can.

Excluded from results: designated Wilderness, Wilderness Study Areas, and National
Monuments / National Conservation Areas, plus any SMA polygon carrying those designations.
Lands with wilderness characteristics are kept but flagged (`in_lwc`).

## Species

`scripts/species.py` is the registry. Each entry carries the USTreeAtlas slug (Little's range is
a URL swap), the LANDFIRE EVT keywords that select the tree's communities, and the
ground-truthing caveat printed in `summary.md`.

The keywords are the part that needs judgment — EVT names a plant community, not a species, and
some trees have no class of their own at all. Derive them before adding an entry:

```bash
just evt-classes pinyon              # what the attribute table actually calls it
just evt-classes ponderosa jeffrey
```

Both a 404 on the slug and an empty EVT match are hard errors; the pipeline will not hand you a
plausible-looking empty map.

Registered, 15 trees covering every tree-bearing EVT class present in Utah:
`junioste` Utah juniper · `juniscop` Rocky Mountain juniper · `pinuedul` two-needle pinyon ·
`pinumono` singleleaf pinyon · `pinupond` ponderosa pine · `pseumenz` Douglas-fir ·
`pinucont` lodgepole pine · `pinuflex` limber pine · `pinulong` Great Basin bristlecone pine ·
`piceenge` Engelmann spruce · `abielasi` subalpine fir · `poputrem` quaking aspen ·
`acergran` bigtooth maple · `quergamb` Gambel oak · `cercledi` curl-leaf mountain mahogany.

Some Utah trees are absent on purpose: no EVT class present in the state names cottonwood or
white fir, so there is nothing to screen against. A tree with no EVT signal gets no entry.

A species whose Little range reaches Utah but whose stands are all off BLM surface — the
high-elevation conifers, mostly National Forest — is not an error. Stage 03 writes the funnel,
says nothing qualified, and exits 0; you get a `summary.md` explaining it and no map.

## Outputs (`out/<slug>/`)

| File | What it is |
|---|---|
| `<slug>_blm.gpkg` | all layers: `candidates`, `hotspots`, `blm_all`, `species_range`, `exclusions`, `field_offices`, `office_points` |
| `<slug>_blm.qgs` (repo root) | the map — QGIS project over a Google satellite basemap |
| `summary.md` | acreage funnel, per-field-office rollup, top scouting cells, caveats |
| `candidates.csv` | one row per eligible BLM parcel |
| `hotspots.csv` | one row per 1 km² hex scouting cell ≥ 25 % cover |
| `scouting.kml` / `.gpx` | 50 waypoints, ≥ 8 km apart, for a phone GPS |

`species_pct` / `species_acres` are the cover columns; which tree they refer to is the slug in
the path.

## Running it

```bash
just setup            # uv venv + deps
just all [slug]       # fetch -> landfire -> overlay -> export -> qgis
just open [slug]      # build the project and open it in QGIS
```

Stages can be run individually (`just fetch`, `just landfire`, …) or directly, but note the
two-interpreter split: `01`–`04` need the venv, `05` needs system python because PyQGIS is a
system package.

```bash
.venv/bin/python scripts/01_fetch.py junioste      # sources -> data/raw (cached)
.venv/bin/python scripts/02_landfire.py junioste   # EVT tiles -> data/work/<slug>/class.vrt
.venv/bin/python scripts/03_overlay.py junioste    # cross-reference -> out/<slug>/<slug>_blm.gpkg
.venv/bin/python scripts/04_export.py junioste     # csv / md / kml / gpx
/usr/bin/python3 scripts/05_qgis_project.py junioste
```

Everything downloaded is cached under `data/` (gitignored); re-runs skip the network. Raw EVT
tiles are cached per region *before* the species remap, so the first species pays the ~1 GB
download and every species after it is local work only.

Knobs are constants at the top of `scripts/03_overlay.py`: `MIN_SPECIES_ACRES`, `HOTSPOT_KM2`,
`HOTSPOT_MIN_PCT`. Jurisdiction, projection and legal exclusions live on the `Region` record in
`scripts/region.py` — only Utah ships, but every state-specific fact is in that one place.

## Colours

The palette is tuned to sit on top of aerial imagery, so it deliberately avoids the hues the
ground already uses: canopy is green and Utah dirt is brown/tan.

* **1 km² hex scouting cells** carry the magnitude — a graduated magenta fill on `species_pct`,
  `#ffb3e0` (25-40 %) → `#4d0038` (85 %+). Breaks start at 25 % because `HOTSPOT_MIN_PCT`
  already excludes anything below that.
* **Eligible BLM parcels** carry the same measure at parcel scale, one hue over: a *shapeburst*
  fill graduated on a cyan ramp, `#d6faff` (0-10 %) → `#00303f` (60 %+), with the boundary line
  taking the class colour too. The gradient starts at the parcel's outer contour and fades to
  fully transparent 400 m in. Drawn above the cells; because its middle is transparent it rims
  the parcel without hiding the cells or the imagery. Cyan has a narrow gamut, so the steps are
  spread wide to clear the ΔE 15 adjacent-pair floor. The 400 m is in ground metres, so the band
  follows the contour at working zoom and thins back to the outline statewide — a fixed screen
  width instead floods every small parcel solid cyan when you zoom out.

Red (`#ff1744`) is reserved for the excluded areas and violet (`#7c4dff`) outlines Little's range.

Adjacent ramp steps are ΔE 20 apart in normal vision and 15.8 under simulated colour-vision
deficiency, and the two ramps sit in different hue families. All colours live in `RAMP_CELLS` /
`RAMP_PARCELS` / `EXCLUDED` / `RANGE` at the top of `scripts/05_qgis_project.py`.

## Accuracy caveats

* Little's range is a hand-drawn 1971 envelope at 1:2M, published in NAD27. It is reprojected
  to NAD83 / UTM 12N here; the ~100 m datum shift is far below the source's own accuracy.
* LANDFIRE EVT is *modelled* 30 m vegetation, not a tree inventory, and its classes are plant
  communities. "Pinyon-juniper woodland" cells contain both pinyon and juniper, and pinyon and
  juniper runs therefore draw on overlapping classes — it is Little's range that separates them.
  Each species' registry entry carries its own version of this caveat into `summary.md`.
* This screening does not model ACECs, grazing/mineral leases, rights-of-way, sage-grouse
  habitat, developed recreation sites, riparian buffers, or cultural-resource restrictions.
  **Call the field office before digging** — the permit is theirs to issue and their
  restrictions are the ones that count.
