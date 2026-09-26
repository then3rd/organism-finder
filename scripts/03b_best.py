"""The best camping spots: ~100k qualifying cells down to about ten per unit.

Camp mode only - for every other taxon this is a no-op, the way stage 02 is for a plant
LANDFIRE cannot see. It runs after stage 03 and reads that stage's `hotspots`, so tuning
the rules below costs a minute rather than the twenty the overlay takes; it writes
`best_cells` and `best_spots` back into the same GeoPackage, which stage 03 rewrites
wholesale - so `just all` always runs this after it.

The overlay answers "where may I camp, and is it flat". Almost every qualifying cell is
100 % flat and a good share are 100 % near water, so its ranking cannot separate them.
This step asks the questions that do:

  * gates   - free dispersed ground only (BLM, USFS); flat; not playa or salt flat; not
              developed; a drivable track within reach; away from pavement.
  * score   - near water, shade, variety of habitat, and quiet, equally weighted, with a
              small bonus where the way in is a rough 4x4 track rather than a graded road.

Vegetation comes from the raw LANDFIRE EVT tiles stage 02 already cached for the region
(real class values, not a taxon's remap), roads from OpenStreetMap (stage 01), and the
unit a Forest Service cell belongs to from the national forest boundaries (stage 01) -
the SMA layer says only "USFS", which would make "ten per forest" ten in all.

    .venv/bin/python scripts/03b_best.py [taxon-slug] [region]
"""
from pathlib import Path
import csv
import math
import subprocess
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import nearest_points

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ownership  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import CRS_LF, spaced  # noqa: E402

# --- gates -----------------------------------------------------------------------
MIN_FLAT_PCT = 50       # half the cell at or under the slope limit, not a quarter
MAX_PLAYA_PCT = 10      # salt flat and dry lakebed: flat, "near water", and awful
MAX_DEV_PCT = 5         # towns, pads, pits
MAX_ACCESS_M = 400      # a drivable track within this of the cell's centre
MIN_PAVED_M = 1500      # and pavement no nearer than this

# --- score -----------------------------------------------------------------------
# Each component is 0-1; the score is their weighted mean plus the access bonus.
WEIGHTS = {"water": 0.25, "shade": 0.25, "variety": 0.25, "quiet": 0.25}
SHADE_FULL_PCT = 40     # tree cover at which a cell counts as fully shaded
VARIETY_FULL = 5        # effective number of habitat types that scores 1
QUIET_FULL_M = 8000     # distance from pavement that scores 1
ROUGH_BONUS = 0.05      # a 4x4 track in, rather than a graded road

# --- pick ------------------------------------------------------------------------
PER_UNIT = 10
MIN_SEP_M = 3000
ZONAL_CHUNK = 2000

FUNNEL = []


def step(label, gdf):
    FUNNEL.append((label, len(gdf)))
    print(f"  {label:<48} {len(gdf):>7,}", flush=True)


def evt_lookup():
    """EVT value -> (lifeform, is_playa). Lifeform is LANDFIRE's own EVT_LF column."""
    out = {}
    for r in csv.DictReader(open(paths.evt_csv())):
        out[int(r["VALUE"])] = (r["EVT_LF"], "playa" in r["EVT_NAME"].lower())
    return out


def evt_vrt(reg):
    vrt = paths.evt_vrt(reg)
    if not vrt.exists():
        tiles = sorted(paths.evt_tile_dir(reg).glob("tile_*.tif"))
        if not tiles:
            raise SystemExit(f"no raw EVT tiles under {paths.evt_tile_dir(reg)} - run "
                             "stage 02 for any EVT taxon in this region first")
        subprocess.run(["gdalbuildvrt", "-overwrite", str(vrt), *map(str, tiles)],
                       check=True, capture_output=True)
    return vrt


def vegetation(cells, reg):
    """tree_pct, playa_pct, developed_pct and variety per cell, from raw EVT.

    `variety` is the effective number of habitat types - exp of the Shannon entropy over
    the tree, shrub and herb classes in the cell. Two classes half and half is 2; one
    class is 1; bare rock and water contribute nothing. It is a habitat-heterogeneity
    proxy for plant and wildlife variety, not a species count.
    """
    from exactextract import exact_extract

    look = evt_lookup()
    lf = cells.to_crs(CRS_LF)[["geometry"]]
    rows = []
    for start in range(0, len(lf), ZONAL_CHUNK):
        batch = lf.iloc[start:start + ZONAL_CHUNK]
        stats = exact_extract(str(evt_vrt(reg)), batch, ["unique", "frac"],
                              output="pandas")
        for uniq, frac in zip(stats["unique"], stats["frac"]):
            tree = playa = dev = 0.0
            veg = []
            for v, f in zip(np.asarray(uniq, dtype=int), np.asarray(frac, dtype=float)):
                life, is_playa = look.get(int(v), ("NA", False))
                if is_playa:
                    playa += f
                elif life == "Developed":
                    dev += f
                elif life in ("Tree", "Shrub", "Herb"):
                    veg.append(f)
                    tree += f if life == "Tree" else 0.0
            total = sum(veg)
            h = -sum((f / total) * math.log(f / total) for f in veg if f > 0) if total else 0
            rows.append((tree * 100, playa * 100, dev * 100,
                         math.exp(h) if total else 0.0))
        print(f"    vegetation: {min(start + ZONAL_CHUNK, len(lf)):,}/{len(lf):,}",
              flush=True)
    v = pd.DataFrame(rows, columns=["tree_pct", "playa_pct", "developed_pct", "variety"],
                     index=cells.index)
    return cells.join(v)


def roads(cells, reg):
    """Distance to pavement, and the nearest drivable unpaved road and where to leave it."""
    r = gpd.read_file(paths.roads_gpkg(reg), layer="roads").to_crs(reg.crs)
    pts = gpd.GeoDataFrame(geometry=cells.geometry.representative_point(), crs=reg.crs,
                           index=cells.index)
    paved = r[r["road_class"] == "paved"][["geometry"]]
    near = gpd.sjoin_nearest(pts, paved, how="left", distance_col="dist_paved_m")
    near = near[~near.index.duplicated()]
    cells["dist_paved_m"] = near["dist_paved_m"].reindex(cells.index).fillna(1e6)

    drive = r[r["road_class"] != "paved"][["road_class", "name", "geometry"]]
    near = gpd.sjoin_nearest(pts, drive, how="left", distance_col="dist_access_m")
    near = near[~near.index.duplicated()]
    cells["dist_access_m"] = near["dist_access_m"].reindex(cells.index).fillna(1e6)
    cells["access_class"] = near["road_class"].reindex(cells.index)
    cells["access_name"] = near["name"].reindex(cells.index)
    cells["_road"] = near["index_right"].reindex(cells.index)
    return cells, drive


def access_points(cells, drive, reg):
    """Where to leave the track: the nearest point on it to the cell's centre."""
    pts = cells.geometry.representative_point()
    out = []
    for p, ri in zip(pts, cells["_road"]):
        if pd.isna(ri):
            out.append(p)
        else:
            out.append(nearest_points(drive.geometry.loc[int(ri)], p)[0])
    ll = gpd.GeoSeries(out, crs=reg.crs).to_crs(4326)
    cells["access_lat"] = ll.y.round(5).to_numpy()
    cells["access_lon"] = ll.x.round(5).to_numpy()
    return cells


def units(cells, reg):
    """BLM keeps its field office; Forest Service cells get their national forest."""
    try:
        forests = gpd.read_file(paths.forests_gpkg(reg), layer="forests").to_crs(reg.crs)
    except Exception as exc:
        raise SystemExit("no national forest boundaries - run stage 01 again") from exc
    pts = gpd.GeoDataFrame(geometry=cells.geometry.representative_point(), crs=reg.crs,
                           index=cells.index)
    # Nearest rather than within: Forest Service ground outside every forest's
    # boundary polygon (Bankhead-Jones tracts, odd parcels the SMA files under "Other") is
    # still administered by the nearest forest, and left alone it forms units of one.
    j = gpd.sjoin_nearest(pts, forests[["forestname", "geometry"]], how="left")
    j = j[~j.index.duplicated()]
    name = j["forestname"].reindex(cells.index)
    usfs = cells["owner"].eq("USFS") & name.notna()
    cells["unit"] = np.where(usfs, name, cells["managing_unit"])
    cells["unit"] = cells["unit"].fillna(cells["owner_name"])
    return cells


def score(cells):
    cells["s_water"] = (cells["water_pct"] / 100).clip(0, 1)
    cells["s_shade"] = (cells["tree_pct"] / SHADE_FULL_PCT).clip(0, 1)
    cells["s_variety"] = ((cells["variety"] - 1) / (VARIETY_FULL - 1)).clip(0, 1)
    cells["s_quiet"] = (cells["dist_paved_m"] / QUIET_FULL_M).clip(0, 1)
    total = sum(WEIGHTS.values())
    cells["score"] = (sum(w * cells[f"s_{k}"] for k, w in WEIGHTS.items()) / total
                      + np.where(cells["access_class"].eq("rough"), ROUGH_BONUS, 0.0))
    return cells


def nearest_site(cells, gpkg, reg):
    """Distance to the nearest established primitive site open to camping. Information
    only - a spot next to a known site is not better for it, just better documented."""
    try:
        sites = gpd.read_file(gpkg, layer="campsites").to_crs(reg.crs)
    except Exception:
        cells["nearest_site_m"] = np.nan
        return cells
    sites = sites[(sites["site_class"] == "primitive") & sites["on_screened"].astype(bool)]
    pts = gpd.GeoDataFrame(geometry=cells.geometry.representative_point(), crs=reg.crs,
                           index=cells.index)
    near = gpd.sjoin_nearest(pts, sites[["geometry"]], how="left", distance_col="d")
    cells["nearest_site_m"] = near[~near.index.duplicated()]["d"].reindex(cells.index)
    return cells


def pick(cells):
    """Best PER_UNIT per unit by score, each at least MIN_SEP_M from the unit's others."""
    out = []
    for unit, g in cells.groupby("unit"):
        g = g.sort_values(["score", "species_pct"], ascending=False)
        pts = g.geometry.representative_point()
        chosen = g.iloc[spaced(pts.x, pts.y, MIN_SEP_M, PER_UNIT)].copy()
        chosen["best_rank"] = np.arange(1, len(chosen) + 1)
        out.append(chosen)
    best = pd.concat(out).sort_values(["unit", "best_rank"]).reset_index(drop=True)
    return gpd.GeoDataFrame(best, geometry="geometry", crs=cells.crs)


COLS = [
    "unit", "best_rank", "score", "owner", "owner_name", "county",
    "species_pct", "water_pct", "tree_pct", "variety", "dist_paved_m", "dist_access_m",
    "access_class", "access_name", "access_lat", "access_lon", "nearest_site_m",
    "playa_pct", "developed_pct", "s_water", "s_shade", "s_variety", "s_quiet",
    "rank", "lat", "lon", "geometry",
]


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve(sys.argv)
    if sp.mode != species_mod.CAMP:
        print(f"{sp.common_name}: not a camping screen - skipping the best-spots step.")
        return
    gpkg = paths.gpkg_path(sp, reg)
    if not gpkg.exists():
        print(f"{sp.common_name}: stage 03 wrote no GeoPackage - nothing to refine.")
        return

    print(f"== best {sp.short} spots on {reg.name} free dispersed ground ==")
    cells = gpd.read_file(gpkg, layer="hotspots").to_crs(reg.crs)
    step("qualifying camping cells (stage 03)", cells)
    free = [c for c in cells["owner"].unique()
            if ownership.owner(c, reg).camp == ownership.FREE]
    cells = cells[cells["owner"].isin(free)]
    step("... free dispersed ground (" + ", ".join(sorted(free)) + ")", cells)
    cells = cells[cells["species_pct"] >= MIN_FLAT_PCT]
    step(f"... at least {MIN_FLAT_PCT} % flat", cells)

    # Roads before vegetation: they are the cheap test and they cut the most.
    cells, drive = roads(cells.copy(), reg)
    cells = cells[cells["dist_access_m"] <= MAX_ACCESS_M]
    step(f"... a drivable track within {MAX_ACCESS_M} m", cells)
    cells = cells[cells["dist_paved_m"] >= MIN_PAVED_M]
    step(f"... pavement at least {MIN_PAVED_M / 1000:g} km away", cells)

    cells = vegetation(cells, reg)
    cells = cells[cells["playa_pct"] < MAX_PLAYA_PCT]
    step(f"... under {MAX_PLAYA_PCT} % playa or salt flat", cells)
    cells = cells[cells["developed_pct"] < MAX_DEV_PCT]
    step(f"... under {MAX_DEV_PCT} % developed", cells)
    if not len(cells):
        raise SystemExit("no cell survives the best-spot gates - loosen them at the top "
                         "of scripts/03b_best.py")

    cells = score(units(cells, reg))
    best = pick(cells)
    best = access_points(best, drive, reg)
    best = nearest_site(best, gpkg, reg)
    step(f"best {PER_UNIT} per unit, {MIN_SEP_M / 1000:g} km apart", best)

    best = best[[c for c in COLS if c in best.columns]]
    for col in ("score", "variety", "s_water", "s_shade", "s_variety", "s_quiet"):
        best[col] = best[col].round(3)
    for col in ("species_pct", "water_pct", "tree_pct", "playa_pct", "developed_pct",
                "dist_paved_m", "dist_access_m", "nearest_site_m"):
        best[col] = best[col].round(1)
    best.to_file(gpkg, layer="best_cells", driver="GPKG")
    spots = best.copy()
    spots["geometry"] = best.geometry.representative_point()
    spots.to_file(gpkg, layer="best_spots", driver="GPKG")
    pd.DataFrame(FUNNEL, columns=["stage", "cells"]).to_csv(
        paths.out_dir(sp, reg) / "best_funnel.csv", index=False)

    print("\nper unit:")
    for unit, g in best.groupby("unit"):
        print(f"  {unit:<44} {len(g):>3}  best score {g['score'].max():.2f}")
    print(f"-> {gpkg.relative_to(paths.ROOT)}: best_cells, best_spots ({len(best)})")


if __name__ == "__main__":
    main()
