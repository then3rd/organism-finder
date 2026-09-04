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

    if sp.needs_landfire:
        print("LANDFIRE EVT attribute table")
        csv = paths.evt_csv()
        if not csv.exists():
            csv.write_bytes(get(EVT_CSV).content)
        print(f"  -> {csv.name} ({csv.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
