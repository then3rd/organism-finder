"""Fetch LANDFIRE Existing Vegetation Type over the region and reduce it to a class grid.

The EVT ImageServer is tiled with exportImage (30 m, EPSG:5070). Tiles are cached twice:
the raw S16 EVT once per region, then remapped through the species LUT to a small uint8
code (0 = not the species' community, 1..N = which one). The raw cache is what makes a
second species cheap - the remap is local, only the download is not.

    .venv/bin/python scripts/02_landfire.py [species-slug]
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import csv
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import CRS_LF, get  # noqa: E402

EVT_IMAGESERVER = (
    "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2023/LF2023_EVT_CONUS/ImageServer"
)
TILE_PX = 4096          # 122.88 km per tile at 30 m
RES = 30


def evt_classes(sp):
    """EVT VALUE -> name for every LANDFIRE class this species' keywords select."""
    rows = list(csv.DictReader(open(paths.evt_csv())))
    hits = {int(r["VALUE"]): r["EVT_NAME"] for r in rows if sp.matches(r["EVT_NAME"])}
    if not hits:
        raise SystemExit(
            f"{sp.slug}: no LANDFIRE EVT class name matches {list(sp.evt_include)}.\n"
            "Nothing downstream can find this tree. Run `just evt-classes <keyword>` to "
            "see what the attribute table actually calls it, then fix evt_include in "
            "scripts/species.py. Some plants have no EVT class of their own at all, and "
            "nothing herbaceous does - those need cover='occurrence' instead."
        )
    return dict(sorted(hits.items()))


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


def raw_tile(idx, box, reg):
    """Download one EVT tile as-is. Species-independent, so cached per region."""
    out = paths.evt_tile_dir(reg) / f"tile_{idx:03d}.tif"
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
    profile.update(
        driver="GTiff", dtype="int16", count=1, compress="deflate", zlevel=9,
        predictor=2, tiled=True, blockxsize=512, blockysize=512,
    )
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(evt.astype("int16"), 1)
    print(f"  tile {idx:03d}  downloaded", flush=True)
    return out


def class_tile(idx, box, reg, sp, lut):
    """Remap one cached EVT tile to the species' uint8 class codes."""
    out = paths.work_dir(sp) / f"class_{idx:03d}.tif"
    if out.exists():
        return out
    with rasterio.open(raw_tile(idx, box, reg)) as src:
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
    print(f"  tile {idx:03d}  {sp.short} px={int((coded > 0).sum()):,}", flush=True)
    return out


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()

    if not sp.needs_landfire:
        print(f"{sp.common_name}: cover='{sp.cover}', so LANDFIRE has nothing to say "
              "about it - skipping stage 02.")
        return

    hits = evt_classes(sp)
    codes = {v: i + 1 for i, v in enumerate(hits)}
    print(f"{sp.common_name}: {len(hits)} EVT classes")
    for value, name in hits.items():
        print(f"  {codes[value]:>2}  {value}  {name}")
    paths.codes_path(sp).write_text(
        "code,evt_value,evt_name\n"
        + "".join(f"{codes[v]},{v},{n}\n" for v, n in hits.items())
    )

    lut = np.zeros(10000, dtype="uint8")
    for value, code in codes.items():
        lut[value] = code

    # The AOI is the region, not one agency's holdings. It used to be BLM's extent, which
    # in Utah happens to approximate the state - but only happens to; any grid laid over
    # ground BLM does not administer would have read nodata outside that bounding box.
    counties = gpd.read_file(paths.raw_dir(reg) / "counties.gpkg",
                             layer="counties").to_crs(CRS_LF)
    bounds = np.array(counties.total_bounds) + np.array([-3000, -3000, 3000, 3000])
    boxes = tile_bounds(bounds, TILE_PX * RES)
    print(f"AOI {bounds.round(0).tolist()}  ->  {len(boxes)} tiles")

    with ThreadPoolExecutor(max_workers=4) as pool:
        tiles = list(pool.map(
            lambda ib: class_tile(ib[0], ib[1], reg, sp, lut), enumerate(boxes)
        ))

    import subprocess
    vrt = paths.vrt_path(sp)
    subprocess.run(
        ["gdalbuildvrt", "-overwrite", str(vrt), *[str(t) for t in tiles]],
        check=True, capture_output=True,
    )
    size = sum(t.stat().st_size for t in tiles) / 1e6
    print(f"-> {vrt.name} from {len(tiles)} tiles ({size:.1f} MB on disk)")


if __name__ == "__main__":
    main()
