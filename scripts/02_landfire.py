"""Fetch LANDFIRE Existing Vegetation Type over Utah and reduce it to a juniper class grid.

The EVT ImageServer is tiled with exportImage (30 m, EPSG:5070). Each tile is remapped
immediately to a small uint8 code (0 = not juniper, 1..N = juniper community) so the
statewide product stays a few tens of MB instead of ~1 GB.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import csv
import io
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CRS_LF, RAW, WORK, get  # noqa: E402

EVT_IMAGESERVER = (
    "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2023/LF2023_EVT_CONUS/ImageServer"
)
TILE_PX = 4096          # 122.88 km per tile at 30 m
RES = 30
MASK_VRT = WORK / "juniper_class.vrt"


def juniper_classes():
    """EVT VALUE -> name for every LANDFIRE class whose name mentions juniper."""
    rows = list(csv.DictReader(open(RAW / "LF23_EVT_240.csv")))
    juni = {
        int(r["VALUE"]): r["EVT_NAME"]
        for r in rows
        if "juniper" in r["EVT_NAME"].lower()
    }
    return dict(sorted(juni.items()))


def tile_bounds(bounds, tile_m):
    xmin, ymin, xmax, ymax = bounds
    # snap to the LANDFIRE 30 m grid so tiles line up exactly
    xmin, ymin = (np.floor(np.array([xmin, ymin]) / RES) * RES).tolist()
    xs = np.arange(xmin, xmax + tile_m, tile_m)
    ys = np.arange(ymin, ymax + tile_m, tile_m)
    return [
        (float(x), float(y), float(x + tile_m), float(y + tile_m))
        for y in ys[:-1]
        for x in xs[:-1]
    ]


def fetch_tile(idx, box, lut):
    out = WORK / f"juni_{idx:03d}.tif"
    if out.exists():
        return out
    xmin, ymin, xmax, ymax = box
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": 5070,
        "imageSR": 5070,
        "size": f"{TILE_PX},{TILE_PX}",
        "format": "tiff",
        "pixelType": "S16",
        "interpolation": "RSP_NearestNeighbor",
        "noDataInterpretation": "esriNoDataMatchAny",
        "f": "image",
    }
    blob = get(f"{EVT_IMAGESERVER}/exportImage", params=params, timeout=600).content
    with MemoryFile(blob) as mem, mem.open() as src:
        evt = src.read(1)
        profile = src.profile
    coded = lut[np.clip(evt, 0, lut.size - 1)]
    coded[evt < 0] = 0
    profile.update(
        driver="GTiff", dtype="uint8", count=1, compress="deflate", zlevel=9,
        predictor=2, tiled=True, blockxsize=512, blockysize=512, nodata=None,
    )
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(coded.astype("uint8"), 1)
    print(f"  tile {idx:03d}  juniper px={int((coded > 0).sum()):,}", flush=True)
    return out


def main():
    juni = juniper_classes()
    codes = {v: i + 1 for i, v in enumerate(juni)}
    print("juniper EVT classes:")
    for value, name in juni.items():
        print(f"  {codes[value]:>2}  {value}  {name}")
    (WORK / "juniper_codes.csv").write_text(
        "code,evt_value,evt_name\n"
        + "".join(f"{codes[v]},{v},{n}\n" for v, n in juni.items())
    )

    lut = np.zeros(10000, dtype="uint8")
    for value, code in codes.items():
        lut[value] = code

    blm = gpd.read_file(RAW / "blm_sma.gpkg", layer="blm").to_crs(CRS_LF)
    bounds = np.array(blm.total_bounds) + np.array([-3000, -3000, 3000, 3000])
    boxes = tile_bounds(bounds, TILE_PX * RES)
    print(f"AOI {bounds.round(0).tolist()}  ->  {len(boxes)} tiles")

    with ThreadPoolExecutor(max_workers=4) as pool:
        tiles = list(pool.map(lambda ib: fetch_tile(ib[0], ib[1], lut), enumerate(boxes)))

    import subprocess
    subprocess.run(
        ["gdalbuildvrt", "-overwrite", str(MASK_VRT), *[str(t) for t in tiles]],
        check=True, capture_output=True,
    )
    size = sum(t.stat().st_size for t in tiles) / 1e6
    print(f"-> {MASK_VRT.name} from {len(tiles)} tiles ({size:.1f} MB on disk)")


if __name__ == "__main__":
    main()
