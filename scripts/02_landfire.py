"""Fetch the cover raster over the region and reduce it to a class grid.

Two rasters feed this stage, chosen by the taxon's `cover`:

  * "evt"   - LANDFIRE Existing Vegetation Type, remapped through the taxon's class LUT.
  * "slope" - USGS 3DEP slope in degrees, thresholded at `slope_max_deg` to one class,
              "flat enough". Fetched in the same EPSG:5070 30 m grid and the same tiles,
              so stage 03's `zonal_evt()` reads it without knowing it is not vegetation.

The EVT ImageServer is tiled with exportImage (30 m, EPSG:5070). Tiles are cached twice:
the raw S16 EVT once per region, then remapped through the species LUT to a small uint8
code (0 = not the species' community, 1..N = which one). The raw cache is what makes a
second species cheap - the remap is local, only the download is not.

    .venv/bin/python scripts/02_landfire.py [species-slug]
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import csv
import json
import sys

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.transform
from rasterio import features
from shapely.geometry import box as box_geom
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


SLOPE_CHUNK_PX = 1024    # 3DEP answers a 1024 px slope request in seconds; 4096 is a 500


def raw_slope_tile(idx, box, reg):
    """One 3DEP slope-in-degrees tile. Taxon-free, cached per region like raw EVT.

    Fetched as a grid of SLOPE_CHUNK_PX sub-requests and stitched here, because the
    service computes slope on the fly and a full 4096 px tile - 123 km on a side - fails
    with an internal server error rather than arriving slowly. The tile it assembles is
    the same tile the EVT grid uses, so the class rasters still line up pixel for pixel.
    """
    out = paths.slope_tile_dir(reg) / f"tile_{idx:03d}.tif"
    if out.exists():
        return out
    xmin, ymin, xmax, ymax = box
    n = TILE_PX // SLOPE_CHUNK_PX
    step = SLOPE_CHUNK_PX * RES
    slope = np.full((TILE_PX, TILE_PX), np.nan, dtype="float32")
    for row in range(n):
        # rows count down from the top edge, the way the array is laid out
        top = ymax - row * step
        for col in range(n):
            left = xmin + col * step
            params = {
                "bbox": f"{left},{top - step},{left + step},{top}",
                "bboxSR": 5070,
                "imageSR": 5070,
                "size": f"{SLOPE_CHUNK_PX},{SLOPE_CHUNK_PX}",
                "format": "tiff",
                "pixelType": "F32",
                "renderingRule": json.dumps({"rasterFunction": "Slope Degrees"}),
                "f": "image",
            }
            blob = get(f"{reg.dem_imageserver}/exportImage", params=params,
                       timeout=300).content
            with MemoryFile(blob) as mem, mem.open() as src:
                part = src.read(1).astype("float32")
                if src.nodata is not None:
                    part[part == src.nodata] = np.nan
            slope[row * SLOPE_CHUNK_PX:(row + 1) * SLOPE_CHUNK_PX,
                  col * SLOPE_CHUNK_PX:(col + 1) * SLOPE_CHUNK_PX] = part
    profile = {
        "driver": "GTiff", "dtype": "float32", "count": 1, "width": TILE_PX,
        "height": TILE_PX, "crs": CRS_LF, "nodata": np.nan,
        "transform": rasterio.transform.from_origin(xmin, ymax, RES, RES),
        "compress": "deflate", "zlevel": 6, "predictor": 3, "tiled": True,
        "blockxsize": 512, "blockysize": 512,
    }
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(slope, 1)
    print(f"  tile {idx:03d}  slope downloaded ({n * n} chunks)", flush=True)
    return out


def water_mask(reg):
    """Mapped lakes and reservoirs in the LANDFIRE grid's CRS, for flat_tile().

    A water surface has a slope of zero, so without this the Great Salt Lake, Utah Lake
    and Lake Powell are the flattest ground in the state and, being water, also the
    nearest to it - the top of a camping map would be the middle of a lake. The same NHD
    waterbodies the water score already fetched, so this is a read, not a download.
    """
    try:
        w = gpd.read_file(paths.water_gpkg(reg), layer="waterbodies")
    except Exception as exc:
        raise SystemExit(f"{paths.water_gpkg(reg)} has no waterbodies layer - run stage "
                         "01 first; slope cover needs it to mask open water") from exc
    return w[["geometry"]].to_crs(CRS_LF)


def flat_tile(idx, box, reg, sp, water):
    """1 where the ground is at or below the taxon's slope limit, 0 elsewhere.

    Nodata (off the DEM) is 0: not known to be flat is not flat, the same way an unknown
    owner is closed. Open water is 0 too - see water_mask().
    """
    out = paths.work_dir(sp, reg) / f"class_{idx:03d}.tif"
    if out.exists():
        return out
    with rasterio.open(raw_slope_tile(idx, box, reg)) as src:
        slope = src.read(1)
        nodata = src.nodata
        profile = src.profile
    ok = np.isfinite(slope) & (slope >= 0)
    if nodata is not None:
        ok &= slope != nodata
    coded = (ok & (slope <= sp.slope_max_deg)).astype("uint8")
    hits = water.iloc[water.sindex.query(box_geom(*box), predicate="intersects")]
    if len(hits):
        wet = features.rasterize(
            ((g, 1) for g in hits.geometry), out_shape=coded.shape,
            transform=profile["transform"], fill=0, dtype="uint8")
        coded[wet == 1] = 0
    profile.update(
        driver="GTiff", dtype="uint8", count=1, compress="deflate", zlevel=9,
        predictor=2, tiled=True, blockxsize=512, blockysize=512, nodata=None,
    )
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(coded, 1)
    print(f"  tile {idx:03d}  {sp.short} px={int(coded.sum()):,}", flush=True)
    return out


def class_tile(idx, box, reg, sp, lut):
    """Remap one cached EVT tile to the species' uint8 class codes."""
    out = paths.work_dir(sp, reg) / f"class_{idx:03d}.tif"
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
    reg = region_mod.resolve(sys.argv)

    if not sp.needs_raster:
        print(f"{sp.common_name}: cover='{sp.cover}', so LANDFIRE has nothing to say "
              "about it - skipping stage 02.")
        return

    if sp.cover == species_mod.SLOPE:
        # One class. The name is what lands in `evidence`, so it states the threshold.
        paths.codes_path(sp, reg).write_text(
            f"code,evt_value,evt_name\n1,,slope <= {sp.slope_max_deg:g} deg\n")
        water = water_mask(reg)
        make = lambda ib: flat_tile(ib[0], ib[1], reg, sp, water)  # noqa: E731
        print(f"{sp.common_name}: 3DEP slope <= {sp.slope_max_deg:g} degrees")
    else:
        make = evt_class_maker(sp, reg)
    build(sp, reg, make)


def evt_class_maker(sp, reg):
    hits = evt_classes(sp)
    codes = {v: i + 1 for i, v in enumerate(hits)}
    print(f"{sp.common_name}: {len(hits)} EVT classes")
    for value, name in hits.items():
        print(f"  {codes[value]:>2}  {value}  {name}")
    paths.codes_path(sp, reg).write_text(
        "code,evt_value,evt_name\n"
        + "".join(f"{codes[v]},{v},{n}\n" for v, n in hits.items())
    )

    lut = np.zeros(10000, dtype="uint8")
    for value, code in codes.items():
        lut[value] = code
    return lambda ib: class_tile(ib[0], ib[1], reg, sp, lut)


def build(sp, reg, make):
    """Tile the region, run `make` on every tile, and assemble the class VRT."""
    # The AOI is the region, not one agency's holdings. It used to be BLM's extent, which
    # in Utah happens to approximate the state - but only happens to; any grid laid over
    # ground BLM does not administer would have read nodata outside that bounding box.
    counties = gpd.read_file(paths.raw_dir(reg) / "counties.gpkg",
                             layer="counties").to_crs(CRS_LF)
    bounds = np.array(counties.total_bounds) + np.array([-3000, -3000, 3000, 3000])
    boxes = tile_bounds(bounds, TILE_PX * RES)
    print(f"AOI {bounds.round(0).tolist()}  ->  {len(boxes)} tiles", flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        tiles = list(pool.map(make, enumerate(boxes)))

    import subprocess
    vrt = paths.vrt_path(sp, reg)
    subprocess.run(
        ["gdalbuildvrt", "-overwrite", str(vrt), *[str(t) for t in tiles]],
        check=True, capture_output=True,
    )
    size = sum(t.stat().st_size for t in tiles) / 1e6
    print(f"-> {vrt.name} from {len(tiles)} tiles ({size:.1f} MB on disk)")


if __name__ == "__main__":
    main()
