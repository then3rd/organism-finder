"""Download and cache every source layer used by the overlay.

Jurisdiction layers are per-region, Little's range polygons are per-species, and the
LANDFIRE attribute table is neither. Everything is cached on disk; re-runs are free
unless you delete data/raw.

    .venv/bin/python scripts/01_fetch.py [species-slug]
"""
from pathlib import Path
import sys

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import esri_count, esri_features, get  # noqa: E402

EVT_CSV = "https://landfire.gov/sites/default/files/CSV/LF2023/LF23_EVT_240.csv"


def save(gdf, path, layer=None):
    gdf.to_file(path, layer=layer, driver="GPKG")
    print(f"  -> {path.name}:{layer or path.stem}  n={len(gdf)}  crs={gdf.crs.to_string()}")


def fetch_layer(url, path, layer, crs, where="1=1"):
    if path.exists():
        try:
            existing = gpd.read_file(path, layer=layer)
            print(f"  cached {path.name}:{layer} n={len(existing)}")
            return existing
        except Exception:
            pass
    print(f"  querying {layer} ...", flush=True)
    fc = esri_features(url, where=where)
    if not fc["features"]:
        # e.g. Utah has no mapped wild & scenic river corridor polygons
        print(f"  {layer}: service returned 0 features - writing empty layer")
        gdf = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    else:
        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326").to_crs(crs)
    save(gdf, path, layer)
    return gdf


def fetch_region(reg):
    raw = paths.raw_dir(reg)

    print(f"BLM surface management agency ({reg.sma_where})")
    expected = esri_count(reg.blm_sma, reg.sma_where)
    print(f"  server reports {expected} BLM-administered polygons")
    blm = fetch_layer(reg.blm_sma, raw / "blm_sma.gpkg", "blm", reg.crs, where=reg.sma_where)
    assert len(blm) == expected, f"paging lost features: {len(blm)} != {expected}"

    print("BLM administrative units")
    fetch_layer(reg.admu_boundary, raw / "admu.gpkg", "boundary", reg.crs)
    fetch_layer(reg.admu_office, raw / "admu.gpkg", "office", reg.crs)

    print("NLCS special designations")
    for name, url in reg.nlcs.items():
        fetch_layer(url, raw / "nlcs.gpkg", name, reg.crs)

    print(f"{reg.name} counties")
    fetch_layer(reg.counties, raw / "counties.gpkg", "counties", reg.crs,
                where=reg.counties_where)


def fetch_range(sp, reg):
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
    save(rng, paths.range_gpkg(sp), "range")


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    print(f"== {sp.common_name} ({sp.binomial}) on BLM {reg.name} ==")

    fetch_region(reg)
    fetch_range(sp, reg)

    print("LANDFIRE EVT attribute table")
    csv = paths.evt_csv()
    if not csv.exists():
        csv.write_bytes(get(EVT_CSV).content)
    print(f"  -> {csv.name} ({csv.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
