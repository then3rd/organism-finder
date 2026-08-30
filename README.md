# Utah juniper × BLM land — transplant permit screening

Finds BLM-administered land in Utah where Utah juniper (*Juniperus osteosperma*) actually grows,
and tells you which BLM field office issues the permit for it.

## The three layers and what each one is for

| Layer | Job | Source |
|---|---|---|
| BLM Utah Surface Management Agency (polygon) | jurisdiction — who issues the permit | [`BLM_UT_SMA/FeatureServer/0`](https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0) |
| Little (1971) *J. osteosperma* range | species filter | Little's *Atlas of United States Trees*, the same polygons as [Data Basin `9fc1ef07…`](https://databasin.org/datasets/9fc1ef07b9c74de2940d4d9a43cfc362/), mirrored as GeoJSON in [`wpetry/USTreeAtlas`](https://github.com/wpetry/USTreeAtlas) |
| LANDFIRE 2023 Existing Vegetation Type, 30 m | stand locator | [LF2023 EVT CONUS ImageServer](https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2023/LF2023_EVT_CONUS/ImageServer) |

Little's range map alone is not enough: at 1:2,000,000 it blankets most of Utah, so
`range ∩ BLM` is ~11 million acres. LANDFIRE narrows that to the ground that is genuinely
juniper woodland. Little's range still does real work — it keeps results inside *osteosperma*
country rather than *J. scopulorum* / *J. monosperma* / western juniper country, which the
LANDFIRE class names alone would not.

Excluded from results: designated Wilderness, Wilderness Study Areas, and National
Monuments / National Conservation Areas, plus any SMA polygon carrying those designations.
Lands with wilderness characteristics are kept but flagged (`in_lwc`).

## Outputs (`out/`)

| File | What it is |
|---|---|
| `juniper_blm.gpkg` | all layers: `candidates`, `hotspots`, `blm_all`, `juniper_range`, `exclusions`, `field_offices`, `office_points` |
| `juniper_blm.qgs` (repo root) | the map — QGIS project over a Google satellite basemap |
| `summary.md` | acreage funnel, per-field-office rollup, top scouting cells, caveats |
| `candidates.csv` | one row per eligible BLM parcel |
| `hotspots.csv` | one row per 1 km² hex scouting cell ≥ 25 % juniper |
| `scouting.kml` / `.gpx` | 50 waypoints, ≥ 8 km apart, for a phone GPS |

## Running it

```bash
uv venv && uv pip install --python .venv/bin/python \
  geopandas rasterio exactextract simplekml gpxpy requests pyogrio

.venv/bin/python scripts/01_fetch.py       # sources -> data/raw (cached)
.venv/bin/python scripts/02_landfire.py    # EVT tiles -> data/work/juniper_class.vrt
.venv/bin/python scripts/03_overlay.py     # the cross-reference -> out/juniper_blm.gpkg
.venv/bin/python scripts/04_export.py      # csv / md / kml / gpx
/usr/bin/python3 scripts/05_qgis_project.py   # PyQGIS lives in system python, not the venv
```

Everything downloaded is cached under `data/` (gitignored); re-runs skip the network.

Knobs are constants at the top of `scripts/03_overlay.py`: `MIN_JUNIPER_ACRES`,
`HOTSPOT_KM2`, `HOTSPOT_MIN_PCT`, `EXCLUDED_DESIG`.

## Colours

The palette is tuned to sit on top of aerial imagery, so it deliberately avoids the hues the
ground already uses: juniper canopy is green and Utah dirt is brown/tan.

* **1 km² hex scouting cells** carry the magnitude — a graduated magenta fill on `juniper_pct`,
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
* LANDFIRE EVT is *modelled* 30 m vegetation, not a tree inventory. "Pinyon-juniper woodland"
  cells contain pinyon pine as well as juniper.
* This screening does not model ACECs, grazing/mineral leases, rights-of-way, sage-grouse
  habitat, developed recreation sites, riparian buffers, or cultural-resource restrictions.
  **Call the field office before digging** — the permit is theirs to issue and their
  restrictions are the ones that count.
