"""Download and cache every source layer used by the overlay.

Jurisdiction layers are per-region, the range layer is per-taxon, and the LANDFIRE
attribute table is neither. Everything is cached on disk; re-runs are free unless you
delete data/raw.

Where the range comes from is the taxon's `range_source`: Little's atlas for a tree, GBIF
occurrence records for a plant nobody drew a range map for, or nothing at all when the
region is the range. See scripts/species.py.

A taxon carrying habitat conditions also pulls the layers those need - fire perimeters,
hydrography - and only the kinds it actually uses, the same way stage 02 is a no-op for a
taxon LANDFIRE cannot see.

    .venv/bin/python scripts/01_fetch.py [species-slug]
"""
from pathlib import Path
import sys

import geopandas as gpd
import json

sys.path.insert(0, str(Path(__file__).resolve().parent))
import habitat  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import esri_count, esri_features, get  # noqa: E402

EVT_CSV = "https://landfire.gov/sites/default/files/CSV/LF2023/LF23_EVT_240.csv"
GBIF_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_PAGE = 300     # the API's own maximum


def save(gdf, path, layer=None):
    gdf.to_file(path, layer=layer, driver="GPKG")
    print(f"  -> {path.name}:{layer or path.stem}  n={len(gdf)}  crs={gdf.crs.to_string()}")


def fetch_layer(url, path, layer, crs, where="1=1", bbox=None, expect=None):
    """One layer, downloaded or read from cache.

    `expect` is the server's current feature count, and it is checked only on the paths
    that actually download - it guards against *paging* dropping features mid-fetch. A
    cached layer is deliberately not compared against it: these services are republished,
    and Utah's SMA layer has gone from 11,687 polygons to 11,672 since this cache was
    written. That is upstream drift, not a lost page, and it is `just clean-all`'s job to
    pick it up rather than a reason to refuse to run.
    """
    if path.exists():
        try:
            existing = gpd.read_file(path, layer=layer)
            drift = "" if expect in (None, len(existing)) else f" (server now {expect})"
            print(f"  cached {path.name}:{layer} n={len(existing)}{drift}")
            return existing
        except Exception:
            pass
    print(f"  querying {layer} ...", flush=True)
    fc = esri_features(url, where=where, bbox=bbox)
    if not fc["features"]:
        # e.g. Utah has no mapped wild & scenic river corridor polygons
        print(f"  {layer}: service returned 0 features - writing empty layer")
        gdf = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    else:
        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326").to_crs(crs)
    assert expect in (None, len(gdf)), f"paging lost features: {len(gdf)} != {expect}"
    save(gdf, path, layer)
    return gdf


def fetch_region(reg):
    raw = paths.raw_dir(reg)

    # The whole surface-management picture, every administrator, not just BLM's share of
    # it. Ownership is a column downstream; filtering it away here would make the other
    # nine tenths of the state invisible rather than merely unscreened.
    print(f"surface management agency ({reg.sma_where})")
    expected = esri_count(reg.sma, reg.sma_where)
    print(f"  server reports {expected} polygons")
    sma = fetch_layer(reg.sma, raw / "sma.gpkg", "sma", reg.crs, where=reg.sma_where,
                      expect=expected)
    if reg.owner_field in sma.columns:
        counts = sma[reg.owner_field].value_counts()
        print("  administrators: "
              + ", ".join(f"{k} {v}" for k, v in counts.items()))

    print("BLM administrative units")
    fetch_layer(reg.admu_boundary, raw / "admu.gpkg", "boundary", reg.crs)
    fetch_layer(reg.admu_office, raw / "admu.gpkg", "office", reg.crs)

    print("NLCS special designations")
    for name, url in reg.nlcs.items():
        fetch_layer(url, raw / "nlcs.gpkg", name, reg.crs)

    print(f"{reg.name} counties")
    fetch_layer(reg.counties, raw / "counties.gpkg", "counties", reg.crs,
                where=reg.counties_where)


def fetch_occurrences(sp, reg):
    """Georeferenced GBIF records for one taxon in one state.

    The range layer for a plant nobody mapped. Each record becomes a point, and stage 03
    buffers it by the taxon's `occurrence_buffer_m` to get something polygonal to
    intersect - so this is a statement about where the plant has been *seen*, which is a
    weaker and more honest claim than a drawn range.
    """
    print(f"GBIF occurrence records for {sp.binomial} (taxonKey {sp.gbif_key})")
    cache = paths.occurrence_raw(sp, reg)
    if not cache.exists():
        feats, offset = [], 0
        while True:
            data = get(GBIF_SEARCH, params={
                "taxonKey": sp.gbif_key,
                "country": "US",
                "stateProvince": reg.gbif_state,
                "hasCoordinate": "true",
                "hasGeospatialIssue": "false",
                "occurrenceStatus": "PRESENT",
                "limit": GBIF_PAGE,
                "offset": offset,
            }).json()
            for r in data["results"]:
                feats.append({
                    "type": "Feature",
                    "geometry": {"type": "Point",
                                 "coordinates": [r["decimalLongitude"],
                                                 r["decimalLatitude"]]},
                    "properties": {
                        "gbif_id": r.get("key"),
                        "year": r.get("year"),
                        # often absent; see the null handling below
                        "uncertainty_m": r.get("coordinateUncertaintyInMeters"),
                        "basis": r.get("basisOfRecord"),
                        "dataset": r.get("datasetName") or r.get("publishingOrgKey"),
                    },
                })
            offset += len(data["results"])
            print(f"    ... {offset}/{data['count']} records", flush=True)
            if data.get("endOfRecords") or not data["results"]:
                break
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    occ = gpd.read_file(cache)
    print(f"  {len(occ)} records in {reg.name}")
    if occ.empty:
        raise SystemExit(
            f"{sp.slug}: GBIF has no georeferenced {reg.name} records for taxonKey "
            f"{sp.gbif_key}. Either the key is wrong or the plant is not recorded here - "
            "check https://api.gbif.org/v1/species/{key} before assuming the former."
        )

    # A record with no stated uncertainty is kept rather than dropped: iNaturalist
    # research-grade observations routinely omit the field, and they are the bulk of the
    # modern records for these plants. Stage 03 buffers a null by the taxon default, so
    # keeping it asserts no more precision than dropping it would have denied.
    loose = occ["uncertainty_m"].notna() & (occ["uncertainty_m"] > sp.max_uncertainty_m)
    print(f"  dropping {int(loose.sum())} records looser than "
          f"{sp.max_uncertainty_m} m; {int(occ['uncertainty_m'].isna().sum())} state none")
    occ = occ[~loose].to_crs(reg.crs)
    if occ.empty:
        raise SystemExit(
            f"{sp.slug}: every {reg.name} record is looser than "
            f"{sp.max_uncertainty_m} m. Raise max_uncertainty_m if a coarser map is still "
            "worth having, but know that is what you are making."
        )
    save(occ, paths.range_gpkg(sp, reg), "occurrences")


def fetch_little(sp, reg):
    """Little (1971) Atlas of United States Trees, one species' range polygons.

    Same polygons as the Data Basin datasets, but downloadable anonymously.
    """
    print(f"Little (1971) {sp.binomial} range")
    geojson = paths.species_raw(sp)
    if not geojson.exists():
        try:
            geojson.write_bytes(get(sp.range_url).content)
        except RuntimeError as exc:
            raise SystemExit(
                f"{exc}\nNo Little range map for slug {sp.slug!r}. Slugs are genus4+species4 "
                "as published by github.com/wpetry/USTreeAtlas - check the slug in "
                "scripts/species.py against that repo's geojson/ directory."
            ) from exc
    rng = gpd.read_file(geojson)
    print(f"  source polygons: {len(rng)}  crs={rng.crs.to_string()}")
    if sp.little_polygons is not None:
        assert len(rng) == sp.little_polygons, (
            f"unexpected polygon count for {sp.slug}: {len(rng)} != {sp.little_polygons}"
        )
    # CODE 1 = inside the species range, 0 = interior hole (lake/gap) in Little's maps.
    print("  CODE counts:", rng["CODE"].value_counts().to_dict())
    rng = rng[rng["CODE"] == 1].to_crs(reg.crs)
    if rng.empty:
        raise SystemExit(f"{sp.slug}: Little's map has no in-range polygons - nothing to screen")
    save(rng, paths.range_gpkg(sp, reg), "range")


def region_bbox(reg):
    """The region envelope in WGS84, from the counties layer stage 01 has already got.

    Read off the data rather than written down, so it is right for the next region
    without anybody remembering to type in a bounding box.
    """
    counties = gpd.read_file(paths.raw_dir(reg) / "counties.gpkg", layer="counties")
    return tuple(counties.to_crs("EPSG:4326").total_bounds)


def fetch_conditions(sp, reg):
    """The layers this taxon's habitat conditions need, and no others."""
    kinds = habitat.kinds(sp.conditions)
    if not kinds:
        return
    bbox = region_bbox(reg)

    if habitat.BURN in kinds:
        print("fire perimeter history (NIFC)")
        # National service, so the envelope is doing real work here. Every year is
        # fetched, not just the current window: the window moves with the calendar and a
        # cache keyed to this year's answer would quietly go stale next spring.
        fire = fetch_layer(
            reg.fire_perims, paths.fire_gpkg(reg), "perimeters", reg.crs,
            where=reg.fire_where, bbox=bbox,
        )
        if not len(fire):
            raise SystemExit(
                f"{reg.name}: fire perimeter service returned nothing - a burn condition "
                "cannot be screened without it"
            )

    if habitat.WATER in kinds:
        print("NHD perennial hydrography")
        lines = fetch_layer(
            reg.hydro_flowline, paths.water_gpkg(reg), "flowlines", reg.crs,
            where=reg.flowline_where, bbox=bbox,
        )
        bodies = fetch_layer(
            reg.hydro_waterbody, paths.water_gpkg(reg), "waterbodies", reg.crs,
            where=reg.waterbody_where, bbox=bbox,
        )
        if not len(lines) and not len(bodies):
            raise SystemExit(
                f"{reg.name}: no perennial water returned - check Region.flowline_where"
            )


# --- campsites (mode="camp") ---------------------------------------------------
# Three sources, because none of them is the answer alone. OpenStreetMap is the only one
# that maps individual primitive sites - the pads people actually use - and it is
# crowd-sourced. The agency layers are authoritative and nearly all developed
# campgrounds: 16 USFS "dispersed camping" markers cover the whole of Utah, and those mark
# an area rather than a site. So OSM leads and the agencies fill in, and every row says
# which source it came from.

OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_TAGS = ("name", "camp_site", "backcountry", "fee", "reservation", "toilets",
            "drinking_water", "operator", "access", "tents", "capacity", "website")
# OSM access values meaning "not open to you". Dropped at fetch: a campground behind a
# gate is not a site on anybody's map but its owner's.
OSM_CLOSED = {"private", "customers", "no", "members"}
DEDUPE_M = 250      # an agency point this close to an OSM one is the same campground


def osm_class(tags):
    """primitive / developed / unclassified, from a camp_site's OSM tags.

    Backcountry and `camp_site=basic` win first: a wilderness pad that needs a permit and
    a fee is still a primitive site. Then anything with a fee, a reservation system or a
    service grade is developed, and a site explicitly marked free is primitive.
    """
    t = {k: str(v).lower() for k, v in tags.items() if v is not None}
    if t.get("backcountry") == "yes" or t.get("camp_site") == "basic":
        return "primitive"
    if (t.get("camp_site") in ("standard", "serviced", "deluxe")
            or t.get("fee") == "yes" or t.get("reservation") in ("yes", "required")):
        return "developed"
    if t.get("fee") == "no":
        return "primitive"
    return "unclassified"


def fetch_osm_campsites(reg, path):
    try:
        cached = gpd.read_file(path, layer="osm")
        print(f"  cached {path.name}:osm n={len(cached)}")
        return cached
    except Exception:
        pass
    if not reg.osm_area:
        raise SystemExit(f"{reg.name}: Region.osm_area is not set - cannot query OSM")
    print(f"  querying OpenStreetMap ({reg.osm_area}) ...", flush=True)
    query = (f'[out:json][timeout:180];area["ISO3166-2"="{reg.osm_area}"]->.a;'
             'nwr["tourism"="camp_site"](area.a);out tags center;')
    data = get(OVERPASS, params={"data": query}, timeout=240).json()
    feats = []
    for e in data["elements"]:
        where = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if where.get("lat") is None:
            continue
        tags = e.get("tags", {})
        props = {k: tags.get(k) for k in OSM_TAGS}
        props["osm_id"] = f"{e['type']}/{e['id']}"
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Point",
                                   "coordinates": [where["lon"], where["lat"]]}})
    if not feats:
        raise SystemExit(f"{reg.name}: OpenStreetMap returned no campsites - check "
                         "Region.osm_area and the Overpass service")
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326").to_crs(reg.crs)
    save(gdf, path, "osm")
    return gdf


def merge_campsites(osm, usfs, blm, reg):
    """One point layer, one row per site, with `source` and `site_class` on every row."""
    import pandas as pd

    closed = osm["access"].fillna("").str.lower().isin(OSM_CLOSED)
    print(f"  dropping {int(closed.sum())} OSM sites marked private or closed")
    osm = osm[~closed]
    rows = [gpd.GeoDataFrame({
        "name": osm["name"],
        "source": "osm",
        "site_class": [osm_class(r) for r in osm[list(OSM_TAGS)].to_dict("records")],
        "fee": osm["fee"],
        # pd.notna, not truthiness: a missing tag comes back from the GeoPackage as NaN,
        # which is truthy and would print "camp_site=nan" into every waypoint.
        "detail": osm.apply(lambda r: ", ".join(
            f"{k}={r[k]}" for k in ("camp_site", "backcountry", "toilets",
                                    "drinking_water", "operator")
            if pd.notna(r[k]) and r[k] != ""), axis=1),
        "url": osm["website"],
        "source_id": osm["osm_id"],
    }, geometry=osm.geometry, crs=reg.crs)]

    agency = []
    if len(usfs):
        dispersed = usfs["markeractivity"].eq("Dispersed Camping")
        agency.append(gpd.GeoDataFrame({
            "name": usfs["recareaname"],
            "source": "usfs",
            "site_class": ["primitive" if d else "developed" for d in dispersed],
            "fee": usfs["feedescription"].fillna("").str.slice(0, 120),
            "detail": [f"{a} - marks an area, not a pad" if d else a
                       for a, d in zip(usfs["markeractivity"], dispersed)],
            "url": usfs["recareaurl"],
            "source_id": usfs["recareaid"].astype(str),
        }, geometry=usfs.geometry, crs=reg.crs))
    if len(blm):
        agency.append(gpd.GeoDataFrame({
            "name": blm["FacilityName"],
            "source": "blm",
            # recreation.gov listings: somebody built and runs these
            "site_class": "developed",
            "fee": blm["FacilityUseFeeDescription"].fillna("").str.slice(0, 120),
            "detail": blm["FacilityTypeDescription"],
            "url": blm["BLMFacURL"],
            "source_id": blm["FacilityID"].astype(str),
        }, geometry=blm.geometry, crs=reg.crs))

    sites = rows[0]
    sites["also_in"] = None
    for ag in agency:
        ag = ag.drop_duplicates("source_id")
        near = gpd.sjoin_nearest(ag[["geometry"]], sites[["geometry"]], how="left",
                                 max_distance=DEDUPE_M)
        near = near[~near.index.duplicated()]
        dup = near["index_right"].notna()
        src = ag["source"].iat[0] if len(ag) else ""
        hit = near.loc[dup, "index_right"].astype(int).to_numpy()
        sites.loc[sites.index[sites.index.isin(hit)], "also_in"] = src
        print(f"  {src}: {len(ag)} sites, {int(dup.sum())} already in OSM within "
              f"{DEDUPE_M} m")
        ag = ag[~dup.to_numpy()].copy()
        ag["also_in"] = None
        sites = gpd.GeoDataFrame(pd.concat([sites, ag], ignore_index=True), crs=reg.crs)
    sites.insert(0, "site_id", range(1, len(sites) + 1))
    return sites


def fetch_campsites(reg):
    path = paths.campsites_gpkg(reg)
    print("campsites (OpenStreetMap, USFS, BLM)")
    bbox = region_bbox(reg)
    osm = fetch_osm_campsites(reg, path)
    usfs = fetch_layer(reg.usfs_rec, path, "usfs", reg.crs, where=reg.usfs_camp_where,
                       bbox=bbox)
    blm = fetch_layer(reg.blm_camp, path, "blm", reg.crs, bbox=bbox)
    # Rebuilt every run from the three cached layers: merging is seconds, and a change to
    # the classification rules above should not need a re-download to take effect.
    sites = merge_campsites(osm, usfs, blm, reg)
    # The agency layers were narrowed by the bbox, which takes in the neighbours' forests
    # along every border. Outside the region there is no SMA polygon to say who
    # administers the ground, so those sites would read as unknown owner - clip them.
    counties = gpd.read_file(paths.raw_dir(reg) / "counties.gpkg",
                             layer="counties").to_crs(reg.crs)
    inside = sites.within(counties.union_all())
    print(f"  dropping {int((~inside).sum())} sites outside {reg.name}")
    sites = sites[inside].reset_index(drop=True)
    sites["site_id"] = range(1, len(sites) + 1)
    if not len(sites):
        raise SystemExit(f"{reg.name}: no campsites from any source - nothing to mark")
    save(sites, path, "sites")
    print("  by class: " + ", ".join(
        f"{k} {v}" for k, v in sites["site_class"].value_counts().items()))
    print("  by source: " + ", ".join(
        f"{k} {v}" for k, v in sites["source"].value_counts().items()))


# --- roads (mode="camp") ----------------------------------------------------------
# For the best-spots step: is there a track to a cell, and how far is it from pavement.
# OpenStreetMap rather than the agencies' route layers because it is one schema for BLM
# and Forest Service ground alike, and it maps the two-tracks the agencies' layers omit.
ROAD_HIGHWAYS = ("track|unclassified|tertiary|tertiary_link|secondary|secondary_link|"
                 "primary|primary_link|trunk|trunk_link|motorway|motorway_link")
ROAD_TAGS = ("highway", "surface", "tracktype", "4wd_only", "access", "motor_vehicle",
             "name")
ROAD_GRID = 3           # the region bbox is queried as a GRID x GRID set of pieces
PAVED_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary",
                  "primary_link", "secondary", "secondary_link"}
PAVED_SURFACES = {"paved", "asphalt", "concrete", "concrete:plates", "paving_stones",
                  "chipseal"}
ROUGH_GRADES = {"grade3", "grade4", "grade5"}


def road_class(tags):
    """paved / graded / rough. A tertiary with no surface tag is taken as graded: in
    rural Utah a good share of them are gravel, and calling one paved would count it
    against a spot's quiet score for traffic it does not carry."""
    hw, surface = tags.get("highway"), (tags.get("surface") or "").lower()
    if hw in PAVED_HIGHWAYS or surface in PAVED_SURFACES:
        return "paved"
    if tags.get("4wd_only") == "yes" or tags.get("tracktype") in ROUGH_GRADES:
        return "rough"
    return "graded"


def overpass_piece(query, cache, tries=6):
    """One Overpass query, cached to disk, retried on 429.

    The public Overpass server rate-limits by client, and nine large queries in a row
    trip it. A 429 is the server saying "later", not "wrong", so this waits and tries
    again - and each piece is cached, so a failure halfway costs only the pieces left.
    """
    import time

    if cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(tries):
        try:
            data = get(OVERPASS, params={"data": query}, timeout=360).json()
            cache.write_text(json.dumps(data))
            return data
        except RuntimeError as exc:
            if "HTTP 429" not in str(exc) and "HTTP 504" not in str(exc):
                raise
            wait = 60 * (attempt + 1)
            print(f"    overpass busy ({exc}); waiting {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit("Overpass kept refusing - try `just fetch` again later; finished "
                     "pieces are cached")


def fetch_roads(reg):
    path = paths.roads_gpkg(reg)
    try:
        roads = gpd.read_file(path, layer="roads")
        print(f"  cached {path.name}:roads n={len(roads)}")
        return
    except Exception:
        pass
    print(f"OpenStreetMap roads and tracks ({reg.osm_area})")
    minx, miny, maxx, maxy = region_bbox(reg)
    dx, dy = (maxx - minx) / ROAD_GRID, (maxy - miny) / ROAD_GRID
    pieces = paths.raw_dir(reg) / "roads_pieces"
    pieces.mkdir(exist_ok=True)
    feats, seen = [], set()
    for i in range(ROAD_GRID):
        for j in range(ROAD_GRID):
            s, w = miny + j * dy, minx + i * dx
            query = (f'[out:json][timeout:300];area["ISO3166-2"="{reg.osm_area}"]->.a;'
                     f'way["highway"~"^({ROAD_HIGHWAYS})$"]'
                     f'({s},{w},{s + dy},{w + dx})(area.a);out tags geom;')
            data = overpass_piece(query, pieces / f"piece_{i}_{j}.json")
            n = 0
            for e in data["elements"]:
                if e["id"] in seen or len(e.get("geometry", [])) < 2:
                    continue
                seen.add(e["id"])
                tags = e.get("tags", {})
                if (tags.get("access") in ("no", "private")
                        or tags.get("motor_vehicle") in ("no", "private")):
                    continue
                props = {k: tags.get(k) for k in ROAD_TAGS}
                props["road_class"] = road_class(tags)
                feats.append({"type": "Feature", "properties": props, "geometry": {
                    "type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in e["geometry"]]}})
                n += 1
            print(f"    piece {i * ROAD_GRID + j + 1}/{ROAD_GRID ** 2}: {n:,} ways",
                  flush=True)
    if not feats:
        raise SystemExit(f"{reg.name}: OpenStreetMap returned no roads")
    roads = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326").to_crs(reg.crs)
    save(roads, path, "roads")
    print("  by class: " + ", ".join(
        f"{k} {v}" for k, v in roads["road_class"].value_counts().items()))


def fetch_forests(reg):
    print("national forest boundaries")
    fetch_layer(reg.usfs_forests, paths.forests_gpkg(reg), "forests", reg.crs,
                bbox=region_bbox(reg))


def fetch_range(sp, reg):
    """Dispatch on the taxon's range source; None means the region is the range."""
    if sp.range_source == species_mod.LITTLE:
        fetch_little(sp, reg)
    elif sp.range_source == species_mod.GBIF:
        fetch_occurrences(sp, reg)
    else:
        print(f"no range filter for {sp.binomial} - the region is the range")
    if sp.cover == species_mod.OCCURRENCE and sp.range_source != species_mod.GBIF:
        # cover='occurrence' scores against the records themselves, so they have to exist
        fetch_occurrences(sp, reg)


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve(sys.argv)
    print(f"== {sp.common_name} ({sp.binomial}) on {reg.name} public land ==")

    fetch_region(reg)
    fetch_range(sp, reg)
    fetch_conditions(sp, reg)
    if sp.mode == species_mod.CAMP:
        fetch_campsites(reg)
        fetch_roads(reg)
        fetch_forests(reg)

    if sp.cover == species_mod.EVT:
        print("LANDFIRE EVT attribute table")
        csv = paths.evt_csv()
        if not csv.exists():
            csv.write_bytes(get(EVT_CSV).content)
        print(f"  -> {csv.name} ({csv.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
