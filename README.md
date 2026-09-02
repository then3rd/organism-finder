# Plants × Utah public land — where it grows, and who administers the ground

Finds **public land** in Utah where a given plant actually grows, and names the agency and
unit that administers each parcel. Ships Utah juniper (*Juniperus osteosperma*) as the
default, with twenty-two more taxa registered; adding another is a registry entry, not a
code change.

```bash
just species          # every registered plant, and how each one is screened
just owners           # who administers ground here, and what may be taken off it
just all              # Utah juniper
just all pinuedul     # two-needle pinyon
just all platdila     # white bog orchid — a different screening method entirely
```

## The three layers

| Layer | Job | Source |
|---|---|---|
| Utah Surface Management Agency (polygon) | jurisdiction — who administers it | [`BLM_UT_SMA/FeatureServer/0`](https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0) |
| range | is this the plant's country at all? | Little (1971) *Atlas of United States Trees*, mirrored as GeoJSON in [`wpetry/USTreeAtlas`](https://github.com/wpetry/USTreeAtlas) — or [GBIF](https://www.gbif.org) occurrence records |
| cover | does it grow on *this* ground? | [LANDFIRE 2023 EVT CONUS, 30 m](https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2023/LF2023_EVT_CONUS/ImageServer) — or the same GBIF records |

Despite the URL, the SMA service is not a BLM layer: it is the whole surface-management
picture for Utah, 11,687 polygons of which BLM administers 2,171 and the state trust lands
agency 4,316. Ownership is a column carried end to end, not a download filter, so the map
shows the entire public estate and the tables say per agency what may be taken off it.

No layer can be dropped. Little's range map alone is 1:2,000,000 and blankets most of Utah,
so for juniper `range ∩ public land` is millions of acres. LANDFIRE narrows that to ground
genuinely woodland of the right type — but LANDFIRE class names are *community* names, so
they cannot tell *J. osteosperma* from *J. scopulorum*, which is what the range polygon is
still doing. And neither says anything about who issues a permit.

## Two ways to screen a plant

Little's atlas maps only trees, and LANDFIRE EVT names only woody *communities*. A
herbaceous plant is invisible to both, so orchids are screened from georeferenced
occurrence records instead — buffered by each record's own stated accuracy.

| the plant | range | cover | example |
|---|---|---|---|
| a tree with a Little map and an EVT class | Little 1971 | LANDFIRE EVT | `junioste` |
| a woody plant EVT names but Little never mapped | none — the region is the range | LANDFIRE EVT | `artetrid` |
| anything herbaceous | buffered GBIF records | the same records | `platdila` |

Occurrence screening is a much weaker claim and the output says so: it records where
somebody looked and found, not where the plant is. Absence of records is absence of
records.

For EVT taxa, derive the keywords before writing the entry:

```bash
just evt-classes pinyon            # what the attribute table actually calls it
just evt-classes big sagebrush
```

Bare keywords are a trap. `sagebrush` matches eight Utah classes and three of them are a
different shrub (*A. nova*, *A. arbuscula*) on different soils.

A 404 on the slug, an empty EVT match and a GBIF key with no records in the region are all
hard errors — the pipeline will not hand you a plausible-looking empty map.

## Collect mode and observe mode

Each taxon carries a `mode`, and it is what makes ownership do real work:

* **`collect`** — screens only owners where a plant can lawfully leave, and subtracts
  Wilderness, WSAs and monuments. Output is permit-oriented: who to apply to, and where.
* **`observe`** — screens anything the public may stand on, and *keeps* those designations,
  flagging them instead. Output is a looking map.

Every orchid here is observe mode, and not out of squeamishness: Utah's orchids depend on
soil fungi that do not come up with the plant, so a transplanted one dies whatever the
paperwork says. There is nothing to permit, which makes "where may I dig this" the wrong
question. It is also why an orchid map covers national parks and a juniper transplant map
does not.

Three taxa are marked `sensitive` — both slipper orchids and *Spiranthes diluvialis*, which
is federally listed as threatened. The flag is a label, not a filter: they get the same
full-precision coordinates and the same waypoint files as everything else, and `summary.md`
says plainly that a list of every known plant is the artifact that gets a population dug out.
What you do with the file is the control here, not what the pipeline withholds.

## Registered taxa

Fifteen trees, covering every tree-bearing EVT class present in Utah:
`junioste` Utah juniper · `juniscop` Rocky Mountain juniper · `pinuedul` two-needle pinyon ·
`pinumono` singleleaf pinyon · `pinupond` ponderosa pine · `pseumenz` Douglas-fir ·
`pinucont` lodgepole pine · `pinuflex` limber pine · `pinulong` Great Basin bristlecone pine ·
`piceenge` Engelmann spruce · `abielasi` subalpine fir · `poputrem` quaking aspen ·
`acergran` bigtooth maple · `quergamb` Gambel oak · `cercledi` curl-leaf mountain mahogany.

One shrub: `artetrid` big sagebrush.

Seven orchids, all observe mode: `calybulb` fairy slipper · `coramacu` spotted coralroot ·
`platdila` white bog orchid · `epipgiga` stream orchid · `goodoblo` western rattlesnake
plantain · `cyprfasc` clustered lady's slipper · `spirdilu` Ute ladies'-tresses.

Some Utah plants are absent on purpose. No EVT class present in the state names cottonwood
or white fir — the riparian classes are named for the landform — so there is nothing to
screen a tree against, and a plant with no signal gets no entry rather than a misleading one.

A taxon whose range reaches Utah but whose stands are all off screenable ground is not an
error. Stage 03 writes the funnel, says so, and exits cleanly without a GeoPackage; stage 04
writes an explanatory summary and no map.

## Output

Everything lands in `out/<slug>/`:

| file | what |
|---|---|
| `<slug>.gpkg` | every layer: `candidates`, `hotspots`, `other_cells`, `public_land`, `land_all`, `species_range`, `occurrences`, `exclusions`, `field_offices`, `office_points` |
| `funnel.csv` | how the acreage narrows, step by step |
| `summary.md` | per-agency and per-unit rollups, who to ask, and the caveats |
| `candidates.csv` | one row per eligible public parcel |
| `hotspots.csv` | one row per 1 km² scouting cell |
| `scouting.kml` / `.gpx` | spread waypoints for a phone GPS |

The QGIS project is `<slug>.qgs` at the repo root, regenerated by `just qgis <slug>` rather
than tracked.

## Colours

The palette exists to sit on top of aerial imagery. Adjacent ramp steps hold ΔE ≥ 20 in
normal vision and ≥ 15.8 under simulated colour-vision deficiency. Magenta is the hex cells,
cyan the parcels, red the context grid, violet the range; canopy green and dirt brown are
avoided because they are the ground itself. Exclusions are a near-black hatch — they are
identified by texture, so they can give red up to the context grid, which is far the larger
area and vanished into snow and pale rock while it was grey. Red there is a tint over a
lightness ramp, because lightness is the channel colour blindness leaves intact.

The per-agency ownership wash is the one deliberate exception, and it is safe because that
layer sits at the bottom at low alpha with agency identity carried by the opaque boundary
line rather than the fill. Its colours live on the `Owner` records in `scripts/ownership.py`,
so the map and the tables cannot disagree.

## Knobs

`MIN_SPECIES_ACRES`, `HOTSPOT_KM2`, `HOTSPOT_MIN_PCT` and `OTHER_MIN_ACRES` in
`scripts/03_overlay.py`; `N_WAYPOINTS`, `N_TABLE` and `MIN_SEPARATION_M` in
`scripts/04_export.py`. Jurisdiction, projection and legal exclusions live on the `Region`
record in `scripts/region.py` — only Utah ships, but every state-specific fact is in that one
place. Who counts as public, and what may be taken off each agency's ground, lives on the
`Owner` records in `scripts/ownership.py`.

## Caveats

This is a screening tool, not an authorization. It does not model ACEC boundaries, grazing or
mineral leases, rights-of-way, sage-grouse habitat closures, developed recreation sites,
riparian buffers, or cultural-resource restrictions. LANDFIRE EVT is *modelled* cover and its
classes are communities rather than species. A permit from one agency is worth nothing on
another's ground — check whose parcel you are actually standing on.
