"""Download and cache every source layer used by the overlay.

Everything is cached on disk; re-runs are free unless you delete data/raw.
"""
from pathlib import Path
import sys

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CRS, RAW, esri_count, esri_features, get  # noqa: E402

BLM_SMA = "https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0"
ADMU_BOUNDARY = (
    "https://gis.blm.gov/utarcgis/rest/services/AdminBoundaries/BLM_UT_ADMU/FeatureServer/0"
)
ADMU_OFFICE = (
    "https://gis.blm.gov/utarcgis/rest/services/AdminBoundaries/BLM_UT_ADMU/FeatureServer/1"
)
# Little (1971) Atlas of United States Trees, Juniperus osteosperma. Same polygons as
# the Data Basin dataset 9fc1ef07b9c74de2940d4d9a43cfc362, but downloadable anonymously.
JUNIPER_URL = (
    "https://raw.githubusercontent.com/wpetry/USTreeAtlas/master/geojson/junioste.geojson"
)
# NLCS services publish an "(Arc)" boundary-line layer first; the polygon layer we want
# is layer 1 (layer 2 for wild & scenic river corridors).
NLCS = {
    "wilderness": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WLD/FeatureServer/1",
    "wsa": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSA/FeatureServer/1",
    "nm_nca": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_NMNCA/FeatureServer/1",
    "lwc": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_LWC/FeatureServer/1",
    "wsr": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSR/FeatureServer/2",
}
COUNTIES = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1"
EVT_CSV = "https://landfire.gov/sites/default/files/CSV/LF2023/LF23_EVT_240.csv"


def save(gdf, path, layer=None):
    gdf.to_file(path, layer=layer, driver="GPKG")
    print(f"  -> {path.name}:{layer or path.stem}  n={len(gdf)}  crs={gdf.crs.to_string()}")


def fetch_layer(url, path, layer, where="1=1"):
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
        gdf = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=CRS)
    else:
        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326").to_crs(CRS)
    save(gdf, path, layer)
    return gdf


def main():
    print("BLM surface management agency (ADMIN='BLM')")
    expected = esri_count(BLM_SMA, "ADMIN='BLM'")
    print(f"  server reports {expected} BLM-administered polygons")
    blm = fetch_layer(BLM_SMA, RAW / "blm_sma.gpkg", "blm", where="ADMIN='BLM'")
    assert len(blm) == expected, f"paging lost features: {len(blm)} != {expected}"

    print("BLM administrative units")
    fetch_layer(ADMU_BOUNDARY, RAW / "admu.gpkg", "boundary")
    fetch_layer(ADMU_OFFICE, RAW / "admu.gpkg", "office")

    print("NLCS special designations")
    for name, url in NLCS.items():
        fetch_layer(url, RAW / "nlcs.gpkg", name)

    print("Utah counties")
    fetch_layer(COUNTIES, RAW / "counties.gpkg", "counties", where="STATE='49'")

    print("Little (1971) Juniperus osteosperma range")
    jpath = RAW / "junioste.geojson"
    if not jpath.exists():
        jpath.write_bytes(get(JUNIPER_URL).content)
    juni = gpd.read_file(jpath)
    print(f"  source polygons: {len(juni)}  crs={juni.crs.to_string()}")
    assert len(juni) == 161, f"unexpected juniper polygon count: {len(juni)}"
    # CODE 1 = inside the species range, 0 = interior hole (lake/gap) in Little's maps.
    print("  CODE counts:", juni["CODE"].value_counts().to_dict())
    juni = juni[juni["CODE"] == 1].to_crs(CRS)
    save(juni, RAW / "juniper_range.gpkg", "range")

    print("LANDFIRE EVT attribute table")
    csv = RAW / "LF23_EVT_240.csv"
    if not csv.exists():
        csv.write_bytes(get(EVT_CSV).content)
    print(f"  -> {csv.name} ({csv.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
