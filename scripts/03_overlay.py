"""Cross-reference: BLM-administered surface x Little's range x LANDFIRE stands.

Three layers doing three different jobs:
  * BLM SMA          - jurisdiction (who issues the permit)
  * Little 1971      - species filter (is this the tree's country at all?)
  * LANDFIRE EVT     - stand locator (does it actually grow on this ground?)

Writes out/<slug>/<slug>_blm.gpkg.

    .venv/bin/python scripts/03_overlay.py [species-slug]
"""
from pathlib import Path
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from exactextract import exact_extract
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import CRS_LF  # noqa: E402

M2_PER_ACRE = 4046.8564224
MIN_SPECIES_ACRES = 10      # drop slivers with essentially none of the tree on them
HOTSPOT_KM2 = 1.0           # scouting cell area, km2 (flat-top hexagons)
HOTSPOT_MIN_PCT = 25.0      # a cell must be at least this much cover to be a hotspot
ZONAL_CHUNK = 2000          # features per exact_extract call - see zonal_species()
OFFBLM_MIN_ACRES = 25       # same sliver floor as the BLM grid, applied off-BLM


def acres(gdf):
    return gdf.geometry.area / M2_PER_ACRE


class Timer:
    """Elapsed-time reporting for the long steps, so a run that takes tens of minutes
    says what it is doing rather than sitting silent on one line."""

    def __init__(self, label):
        self.label = label
        self.t0 = time.time()
        print(f"{label} ...", flush=True)

    @property
    def elapsed(self):
        return time.time() - self.t0

    def progress(self, done, total):
        """One line per batch: where it is now and, from the rate so far, when it ends."""
        el = self.elapsed
        eta = (total - done) * el / done if done else 0
        print(f"  {self.label}: {done:,}/{total:,} ({done / total:.0%})  "
              f"{fmt(el)} elapsed, ~{fmt(eta)} left", flush=True)

    def done(self, note=""):
        print(f"  {self.label}: {fmt(self.elapsed)}{'  ' + note if note else ''}",
              flush=True)


def fmt(seconds):
    return f"{seconds:.0f}s" if seconds < 90 else f"{seconds / 60:.1f} min"


def hex_cells(bounds, area_m2):
    """Flat-top hexagons of the given area tiling `bounds`.

    Hexes rather than squares so every neighbour is the same distance away and the lattice
    has no dominant axis to read as an artifact over the terrain.
    """
    r = np.sqrt(2 * area_m2 / (3 * np.sqrt(3)))     # centre -> vertex
    dx, dy = 1.5 * r, np.sqrt(3) * r                # column pitch, row pitch
    corners = [(r * np.cos(a), r * np.sin(a)) for a in np.arange(6) * (np.pi / 3)]
    xmin, ymin, xmax, ymax = bounds
    cells = []
    for i, cx in enumerate(np.arange(np.floor(xmin / dx) * dx - dx, xmax + dx, dx)):
        offset = dy / 2 if i % 2 else 0.0
        for cy in np.arange(np.floor(ymin / dy) * dy - dy, ymax + dy, dy):
            cells.append(Polygon([(cx + ox, cy + offset + oy) for ox, oy in corners]))
    return cells


def zonal_species(gdf, codes, vrt, label="zonal"):
    """Per-feature cover fraction and dominant community for the species' EVT classes.

    Run in batches of ZONAL_CHUNK: exact_extract has no progress hook of its own, and a
    single call over fifty thousand cells is a quarter-hour of silence. The batching costs
    one call per 2000 features, which is lost in the raster reads.
    """
    lf = gdf.to_crs(CRS_LF)[["geometry"]]
    n_codes = max(codes) + 1
    frac = np.zeros((len(lf), n_codes))
    count = np.zeros(len(lf))
    timer = Timer(label)
    for start in range(0, len(lf), ZONAL_CHUNK):
        batch = lf.iloc[start:start + ZONAL_CHUNK]
        stats = exact_extract(str(vrt), batch, ["unique", "frac", "count"],
                              output="pandas")
        for i, (uniq, fr) in enumerate(zip(stats["unique"], stats["frac"])):
            u = np.asarray(uniq, dtype=int)
            keep = u < n_codes
            frac[start + i, u[keep]] = np.asarray(fr, dtype=float)[keep]
        count[start:start + len(batch)] = stats["count"].to_numpy()
        timer.progress(min(start + ZONAL_CHUNK, len(lf)), len(lf))

    species_frac = frac[:, 1:].sum(axis=1)
    dominant = np.where(species_frac > 0, frac[:, 1:].argmax(axis=1) + 1, 0)
    return pd.DataFrame(
        {
            "species_pct": species_frac * 100,
            "dominant_code": dominant,
            "evt_px": count,
        },
        index=gdf.index,
    ), frac


def report(funnel):
    print("\nfunnel:")
    for label, n, ac in funnel:
        print(f"  {label:<45} {n:>6}  {ac:>12,.0f} acres")


def check(cand, funnel, step):
    """An empty frame here means a filter took everything; say which one."""
    if len(cand):
        return
    report(funnel)
    raise SystemExit(
        f"nothing survived: {step}. The funnel above shows where it emptied - check the "
        "species' EVT keywords (`just evt-classes`) and that Little's range actually "
        "overlaps this region."
    )


def main():
    started = time.time()
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    print(f"== {sp.common_name} ({sp.binomial}) on BLM {reg.name} ==")
    raw = paths.raw_dir(reg)
    vrt = paths.vrt_path(sp)

    codes = pd.read_csv(paths.codes_path(sp))
    code_name = dict(zip(codes["code"], codes["evt_name"]))
    code_name[0] = "none"

    t = Timer("reading the jurisdiction layers")
    blm = gpd.read_file(raw / "blm_sma.gpkg", layer="blm").to_crs(reg.crs)
    blm["blm_id"] = np.arange(1, len(blm) + 1)
    counties = gpd.read_file(raw / "counties.gpkg", layer="counties").to_crs(reg.crs)
    counties = counties[["NAME", "geometry"]].rename(columns={"NAME": "county"})
    t.done(f"{len(blm):,} BLM polygons, {len(counties)} counties")
    funnel = [
        (f"BLM-administered surface (SMA {reg.sma_where})", len(blm), acres(blm).sum())
    ]

    # --- species-range filter -------------------------------------------------
    t = Timer(f"intersecting BLM surface with Little's {sp.binomial} range")
    rng = gpd.read_file(paths.range_gpkg(sp), layer="range").to_crs(reg.crs)
    rng_union = rng.union_all()
    cand = blm[blm.intersects(rng_union)].copy()
    cand["geometry"] = cand.geometry.intersection(rng_union)
    cand = cand[~cand.geometry.is_empty]
    t.done(f"{len(cand):,} parcels")
    funnel.append((f"... inside Little's {sp.binomial} range", len(cand), acres(cand).sum()))
    check(cand, funnel, "Little's range does not overlap BLM surface here")

    # --- legal exclusions -----------------------------------------------------
    t = Timer("subtracting Wilderness / WSA / NM-NCA")
    excl_parts = []
    for layer in ("wilderness", "wsa", "nm_nca"):
        g = gpd.read_file(raw / "nlcs.gpkg", layer=layer).to_crs(reg.crs)
        if len(g):
            g = g[["geometry"]].copy()
            g["excl_type"] = layer
            excl_parts.append(g)
    desig_excl = blm[blm["DESIG"].isin(reg.excluded_desig)][["geometry"]].copy()
    desig_excl["excl_type"] = "sma_designation"
    excl_parts.append(desig_excl)
    exclusions = gpd.GeoDataFrame(pd.concat(excl_parts, ignore_index=True), crs=reg.crs)
    excl_union = exclusions.union_all()

    cand["geometry"] = cand.geometry.difference(excl_union)
    cand = cand[~cand.geometry.is_empty].copy()
    cand = cand.explode(index_parts=False).reset_index(drop=True)
    cand = cand[cand.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    cand = cand[acres(cand) >= 1]      # discard difference slivers
    t.done(f"{len(cand):,} parcels")
    funnel.append(("... minus Wilderness / WSA / NM-NCA", len(cand), acres(cand).sum()))
    check(cand, funnel, "the legal exclusions cover everything in range")

    # lands with wilderness characteristics: a flag, not a bar
    t = Timer("flagging lands with wilderness characteristics")
    lwc = gpd.read_file(raw / "nlcs.gpkg", layer="lwc").to_crs(reg.crs)
    lwc_union = lwc.union_all()
    cand["in_lwc"] = cand.intersects(lwc_union)
    t.done(f"{int(cand['in_lwc'].sum()):,} flagged")

    # --- LANDFIRE stand locator ----------------------------------------------
    stats, _ = zonal_species(cand, sorted(code_name), vrt,
                             label=f"scoring {len(cand):,} parcels against the "
                                   f"LANDFIRE {sp.short} grid")
    cand = pd.concat([cand.reset_index(drop=True), stats.reset_index(drop=True)], axis=1)
    cand = gpd.GeoDataFrame(cand, geometry="geometry", crs=reg.crs)
    cand["blm_acres"] = acres(cand)
    cand["species_acres"] = cand["blm_acres"] * cand["species_pct"] / 100
    cand = cand[cand["species_acres"] >= MIN_SPECIES_ACRES].copy()
    cand["dominant_evt"] = cand["dominant_code"].map(code_name)
    funnel.append(
        (f"... with mapped {sp.short} (>={MIN_SPECIES_ACRES} ac)",
         len(cand), cand["species_acres"].sum())
    )
    check(cand, funnel, f"no parcel carries {MIN_SPECIES_ACRES} acres of mapped {sp.short}")

    # --- who issues the permit -----------------------------------------------
    t = Timer("attaching the field office that issues the permit")
    admu = gpd.read_file(raw / "admu.gpkg", layer="boundary").to_crs(reg.crs)
    admu = admu[["ADMU_NAME", "ADM_UNIT_CD", "ADMU_ST_URL", "geometry"]]

    reps = cand.copy()
    reps["geometry"] = cand.representative_point()
    cand["field_office"] = gpd.sjoin(reps, admu, how="left", predicate="within")[
        "ADMU_NAME"
    ].to_numpy()
    cand["office_url"] = gpd.sjoin(reps, admu, how="left", predicate="within")[
        "ADMU_ST_URL"
    ].to_numpy()
    cand["county"] = gpd.sjoin(reps, counties, how="left", predicate="within")[
        "county"
    ].to_numpy()

    t.done()
    cand = cand.sort_values("species_acres", ascending=False).reset_index(drop=True)
    cand["rank"] = np.arange(1, len(cand) + 1)
    cand = cand[[
        "rank", "blm_id", "field_office", "office_url", "county", "DESIG", "in_lwc",
        "blm_acres", "species_acres", "species_pct", "dominant_evt", "geometry",
    ]]

    # --- scouting grid --------------------------------------------------------
    print("\n-- %g km2 hex scouting grid on BLM surface --" % HOTSPOT_KM2, flush=True)
    hotspots = build_hotspots(cand, code_name, reg, vrt)

    # The same grid over everything that is *not* BLM surface. These cells carry no
    # jurisdiction - the ground under them is private, state, tribal or another agency's -
    # so they are context for reading the map, never a permit target. They are built from
    # the same range x EVT screen so the two grids are directly comparable.
    print("\n-- the same grid off BLM surface (context only) --", flush=True)
    off_blm = build_off_blm(blm, rng, counties, code_name, reg, vrt)

    gpkg = paths.gpkg_path(sp)
    t = Timer(f"writing {gpkg.relative_to(paths.ROOT)}")
    cand.to_file(gpkg, layer="candidates", driver="GPKG")
    hotspots.to_file(gpkg, layer="hotspots", driver="GPKG")
    off_blm.to_file(gpkg, layer="off_blm_cells", driver="GPKG")
    blm.to_file(gpkg, layer="blm_all", driver="GPKG")
    rng.to_file(gpkg, layer="species_range", driver="GPKG")
    exclusions.to_file(gpkg, layer="exclusions", driver="GPKG")
    admu.to_file(gpkg, layer="field_offices", driver="GPKG")
    gpd.read_file(raw / "admu.gpkg", layer="office").to_crs(reg.crs).to_file(
        gpkg, layer="office_points", driver="GPKG"
    )
    t.done()

    funnel.append((
        "hex cells (%g km2) >= %g%% %s" % (HOTSPOT_KM2, HOTSPOT_MIN_PCT, sp.short),
        len(hotspots), hotspots["species_acres"].sum(),
    ))
    # below the rule: not a narrowing step, and not permit-eligible ground
    funnel.append((
        "off-BLM hex cells (context only)",
        len(off_blm), off_blm["species_acres"].sum(),
    ))
    pd.DataFrame(funnel, columns=["stage", "features", "acres"]).to_csv(
        paths.out_dir(sp) / "funnel.csv", index=False
    )
    report(funnel)
    print(f"\nstage 03 took {fmt(time.time() - started)}")


def grid_over(domain, code_name, reg, vrt, min_pct, subtract=None, min_acres=25):
    """Hex-grid `domain`, score every cell for species cover, keep the ones over `min_pct`.

    Shared by the BLM scouting grid and the off-BLM context grid so the two are scored by
    exactly the same zonal pass and can be compared cell for cell. `domain` and `subtract`
    are frames, not merged geometries, and the clipping goes through `overlay` both times:
    an `intersection` against one unioned polygon is a quarter of a million unindexed
    pairwise tests against a shape with a million vertices, which is minutes, while the
    indexed pass is seconds.
    """
    t = Timer("laying the hex lattice")
    cells = hex_cells(domain.total_bounds, HOTSPOT_KM2 * 1e6)
    grid = gpd.GeoDataFrame({"cell": np.arange(len(cells))}, geometry=cells, crs=reg.crs)
    t.done(f"{len(cells):,} cells over the bounds")

    t = Timer("clipping cells to the domain")
    grid = gpd.overlay(grid, domain[["geometry"]], how="intersection", keep_geom_type=True)
    # a cell straddling two domain polygons comes back as one row per piece; only those
    # few need dissolving back together, and doing it to the whole frame would cost more
    # than the clip did
    split = grid["cell"].duplicated(keep=False)
    if split.any():
        grid = pd.concat([
            grid[~split].set_index("cell"),
            grid[split].dissolve(by="cell"),
        ])
        grid = gpd.GeoDataFrame(grid, geometry="geometry", crs=reg.crs)
    grid = grid.reset_index(drop=True)
    t.done(f"{len(grid):,} cells on the domain")

    if subtract is not None:
        t = Timer(f"punching out {len(subtract):,} BLM parcels")
        grid = gpd.overlay(grid, subtract[["geometry"]], how="difference",
                           keep_geom_type=True)
        t.done(f"{len(grid):,} cells left")
    grid = grid[~grid.geometry.is_empty]
    grid = grid[acres(grid) >= min_acres].reset_index(drop=True)   # ignore edge slivers

    stats, _ = zonal_species(grid, sorted(code_name), vrt,
                             label=f"scoring {len(grid):,} cells")
    grid = gpd.GeoDataFrame(
        pd.concat([grid.reset_index(drop=True), stats.reset_index(drop=True)], axis=1),
        geometry="geometry", crs=reg.crs,
    )
    grid["cell_acres"] = acres(grid)
    grid["species_acres"] = grid["cell_acres"] * grid["species_pct"] / 100
    grid = grid[grid["species_pct"] >= min_pct].copy()
    grid["dominant_evt"] = grid["dominant_code"].map(code_name)
    return grid.reset_index(drop=True)


def rank_and_locate(grid):
    """Rank by cover and stamp lat/lon on the representative point - both grids need it."""
    grid = grid.sort_values(["species_pct", "species_acres"], ascending=False)
    grid = grid.reset_index(drop=True)
    grid["rank"] = np.arange(1, len(grid) + 1)
    centroids = grid.representative_point().to_crs(4326)
    grid["lon"] = centroids.x
    grid["lat"] = centroids.y
    return grid


def attach(grid, source, cols):
    """Point-in-polygon the cells' representative points onto `source` for `cols`."""
    reps = grid.copy()
    reps["geometry"] = grid.representative_point()
    joined = gpd.sjoin(reps, source[cols + ["geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated()]
    for col in cols:
        grid[col] = joined[col].to_numpy()
    return grid


def build_hotspots(cand, code_name, reg, vrt):
    """Grid the eligible land so there are concrete places to scout, not million-acre blocks."""
    grid = grid_over(cand, code_name, reg, vrt, HOTSPOT_MIN_PCT)
    grid = attach(grid, cand, ["field_office", "county"])
    grid = rank_and_locate(grid).rename(columns={"cell_acres": "blm_acres"})
    return grid[[
        "rank", "field_office", "county", "blm_acres", "species_acres",
        "species_pct", "dominant_evt", "lat", "lon", "geometry",
    ]]


def build_off_blm(blm, rng, counties, code_name, reg, vrt):
    """The same grid over the ground BLM does not administer.

    Domain is region x Little's range minus every BLM SMA polygon - the exact complement of
    the BLM grid's universe, taken before any of the eligibility filters. No field office is
    attached because there is nobody to issue a permit: these cells say where the tree is,
    not where it can be dug.
    """
    domain = gpd.overlay(counties[["geometry"]], rng[["geometry"]], how="intersection",
                         keep_geom_type=True)
    if domain.empty:
        print("  the range does not reach this region")
        return gpd.GeoDataFrame(
            {c: [] for c in ("rank", "county", "cell_acres", "species_acres",
                             "species_pct", "dominant_evt", "lat", "lon")},
            geometry=gpd.GeoSeries([], crs=reg.crs), crs=reg.crs,
        )
    grid = grid_over(domain, code_name, reg, vrt, HOTSPOT_MIN_PCT,
                     subtract=blm, min_acres=OFFBLM_MIN_ACRES)
    grid = attach(grid, counties, ["county"])
    grid = rank_and_locate(grid)
    return grid[[
        "rank", "county", "cell_acres", "species_acres",
        "species_pct", "dominant_evt", "lat", "lon", "geometry",
    ]]


if __name__ == "__main__":
    main()
