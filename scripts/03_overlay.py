"""Cross-reference: BLM-administered surface x Little's juniper range x LANDFIRE juniper stands.

Three layers doing three different jobs:
  * BLM SMA          - jurisdiction (who issues the permit)
  * Little 1971      - species filter (is this Juniperus osteosperma country?)
  * LANDFIRE EVT     - stand locator (does juniper actually grow on this ground?)

Writes out/juniper_blm.gpkg.
"""
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from exactextract import exact_extract
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CRS, CRS_LF, OUT, RAW, WORK  # noqa: E402

M2_PER_ACRE = 4046.8564224
MIN_JUNIPER_ACRES = 10      # drop slivers with essentially no juniper on them
HOTSPOT_KM2 = 1.0           # scouting cell area, km2 (flat-top hexagons)
HOTSPOT_MIN_PCT = 25.0      # a cell must be at least this much juniper to be a hotspot
# Designations where collecting live plants is off the table or needs a different process.
EXCLUDED_DESIG = {"Wilderness", "National Monument", "National Recreation Area"}
VRT = WORK / "juniper_class.vrt"


def acres(gdf):
    return gdf.geometry.area / M2_PER_ACRE


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


def zonal_juniper(gdf, codes):
    """Per-feature juniper fraction and dominant juniper community."""
    lf = gdf.to_crs(CRS_LF)[["geometry"]].copy()
    stats = exact_extract(str(VRT), lf, ["unique", "frac", "count"], output="pandas")
    n_codes = max(codes) + 1
    frac = np.zeros((len(stats), n_codes))
    for i, (uniq, fr) in enumerate(zip(stats["unique"], stats["frac"])):
        u = np.asarray(uniq, dtype=int)
        keep = u < n_codes
        frac[i, u[keep]] = np.asarray(fr, dtype=float)[keep]
    juniper_frac = frac[:, 1:].sum(axis=1)
    dominant = np.where(
        juniper_frac > 0, frac[:, 1:].argmax(axis=1) + 1, 0
    )
    return pd.DataFrame(
        {
            "juniper_pct": juniper_frac * 100,
            "dominant_code": dominant,
            "evt_px": stats["count"].to_numpy(),
        },
        index=gdf.index,
    ), frac


def main():
    codes = pd.read_csv(WORK / "juniper_codes.csv")
    code_name = dict(zip(codes["code"], codes["evt_name"]))
    code_name[0] = "none"

    blm = gpd.read_file(RAW / "blm_sma.gpkg", layer="blm").to_crs(CRS)
    blm["blm_id"] = np.arange(1, len(blm) + 1)
    funnel = [("BLM-administered surface (SMA ADMIN='BLM')", len(blm), acres(blm).sum())]

    # --- species-range filter -------------------------------------------------
    rng = gpd.read_file(RAW / "juniper_range.gpkg", layer="range").to_crs(CRS)
    rng_union = rng.union_all()
    cand = blm[blm.intersects(rng_union)].copy()
    cand["geometry"] = cand.geometry.intersection(rng_union)
    cand = cand[~cand.geometry.is_empty]
    funnel.append(("... inside Little's J. osteosperma range", len(cand), acres(cand).sum()))

    # --- legal exclusions -----------------------------------------------------
    excl_parts = []
    for layer in ("wilderness", "wsa", "nm_nca"):
        g = gpd.read_file(RAW / "nlcs.gpkg", layer=layer).to_crs(CRS)
        if len(g):
            g = g[["geometry"]].copy()
            g["excl_type"] = layer
            excl_parts.append(g)
    desig_excl = blm[blm["DESIG"].isin(EXCLUDED_DESIG)][["geometry"]].copy()
    desig_excl["excl_type"] = "sma_designation"
    excl_parts.append(desig_excl)
    exclusions = gpd.GeoDataFrame(pd.concat(excl_parts, ignore_index=True), crs=CRS)
    excl_union = exclusions.union_all()

    cand["geometry"] = cand.geometry.difference(excl_union)
    cand = cand[~cand.geometry.is_empty].copy()
    cand = cand.explode(index_parts=False).reset_index(drop=True)
    cand = cand[cand.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    cand = cand[acres(cand) >= 1]      # discard difference slivers
    funnel.append(("... minus Wilderness / WSA / NM-NCA", len(cand), acres(cand).sum()))

    # lands with wilderness characteristics: a flag, not a bar
    lwc = gpd.read_file(RAW / "nlcs.gpkg", layer="lwc").to_crs(CRS)
    lwc_union = lwc.union_all()
    cand["in_lwc"] = cand.intersects(lwc_union)

    # --- LANDFIRE stand locator ----------------------------------------------
    print("zonal stats over LANDFIRE juniper grid ...", flush=True)
    stats, _ = zonal_juniper(cand, sorted(code_name))
    cand = pd.concat([cand.reset_index(drop=True), stats.reset_index(drop=True)], axis=1)
    cand = gpd.GeoDataFrame(cand, geometry="geometry", crs=CRS)
    cand["blm_acres"] = acres(cand)
    cand["juniper_acres"] = cand["blm_acres"] * cand["juniper_pct"] / 100
    cand = cand[cand["juniper_acres"] >= MIN_JUNIPER_ACRES].copy()
    cand["dominant_evt"] = cand["dominant_code"].map(code_name)
    funnel.append(
        ("... with mapped juniper (>=%d ac)" % MIN_JUNIPER_ACRES,
         len(cand), cand["juniper_acres"].sum())
    )

    # --- who issues the permit -----------------------------------------------
    admu = gpd.read_file(RAW / "admu.gpkg", layer="boundary").to_crs(CRS)
    admu = admu[["ADMU_NAME", "ADM_UNIT_CD", "ADMU_ST_URL", "geometry"]]
    counties = gpd.read_file(RAW / "counties.gpkg", layer="counties").to_crs(CRS)
    counties = counties[["NAME", "geometry"]].rename(columns={"NAME": "county"})

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

    cand = cand.sort_values("juniper_acres", ascending=False).reset_index(drop=True)
    cand["rank"] = np.arange(1, len(cand) + 1)
    cand = cand[[
        "rank", "blm_id", "field_office", "office_url", "county", "DESIG", "in_lwc",
        "blm_acres", "juniper_acres", "juniper_pct", "dominant_evt", "geometry",
    ]]

    # --- scouting grid --------------------------------------------------------
    print("building %g km2 hex scouting grid ..." % HOTSPOT_KM2, flush=True)
    hotspots = build_hotspots(cand, code_name)

    print("writing out/juniper_blm.gpkg")
    cand.to_file(OUT / "juniper_blm.gpkg", layer="candidates", driver="GPKG")
    hotspots.to_file(OUT / "juniper_blm.gpkg", layer="hotspots", driver="GPKG")
    blm.to_file(OUT / "juniper_blm.gpkg", layer="blm_all", driver="GPKG")
    rng.to_file(OUT / "juniper_blm.gpkg", layer="juniper_range", driver="GPKG")
    exclusions.to_file(OUT / "juniper_blm.gpkg", layer="exclusions", driver="GPKG")
    admu.to_file(OUT / "juniper_blm.gpkg", layer="field_offices", driver="GPKG")
    gpd.read_file(RAW / "admu.gpkg", layer="office").to_crs(CRS).to_file(
        OUT / "juniper_blm.gpkg", layer="office_points", driver="GPKG"
    )

    funnel.append((
        "hex cells (%g km2) >= %g%% juniper" % (HOTSPOT_KM2, HOTSPOT_MIN_PCT),
        len(hotspots), hotspots["juniper_acres"].sum(),
    ))
    pd.DataFrame(funnel, columns=["stage", "features", "acres"]).to_csv(
        OUT / "funnel.csv", index=False
    )
    print("\nfunnel:")
    for label, n, ac in funnel:
        print(f"  {label:<45} {n:>6}  {ac:>12,.0f} acres")


def build_hotspots(cand, code_name):
    """Grid the eligible land so there are concrete places to scout, not million-acre blocks."""
    eligible = cand.union_all()
    cells = hex_cells(cand.total_bounds, HOTSPOT_KM2 * 1e6)
    grid = gpd.GeoDataFrame(geometry=cells, crs=CRS)
    grid = grid[grid.intersects(eligible)].reset_index(drop=True)
    grid["geometry"] = grid.geometry.intersection(eligible)
    grid = grid[~grid.geometry.is_empty]
    grid = grid[acres(grid) >= 25].reset_index(drop=True)   # ignore edge slivers
    print(f"  {len(grid)} candidate cells", flush=True)

    stats, _ = zonal_juniper(grid, sorted(code_name))
    grid = gpd.GeoDataFrame(
        pd.concat([grid.reset_index(drop=True), stats.reset_index(drop=True)], axis=1),
        geometry="geometry", crs=CRS,
    )
    grid["blm_acres"] = acres(grid)
    grid["juniper_acres"] = grid["blm_acres"] * grid["juniper_pct"] / 100
    grid = grid[grid["juniper_pct"] >= HOTSPOT_MIN_PCT].copy()
    grid["dominant_evt"] = grid["dominant_code"].map(code_name)

    reps = grid.copy()
    reps["geometry"] = grid.representative_point()
    joined = gpd.sjoin(reps, cand[["field_office", "county", "geometry"]],
                       how="left", predicate="within")
    joined = joined[~joined.index.duplicated()]
    grid["field_office"] = joined["field_office"].to_numpy()
    grid["county"] = joined["county"].to_numpy()

    grid = grid.sort_values(["juniper_pct", "juniper_acres"], ascending=False)
    grid = grid.reset_index(drop=True)
    grid["rank"] = np.arange(1, len(grid) + 1)
    centroids = grid.representative_point().to_crs(4326)
    grid["lon"] = centroids.x
    grid["lat"] = centroids.y
    return grid[[
        "rank", "field_office", "county", "blm_acres", "juniper_acres",
        "juniper_pct", "dominant_evt", "lat", "lon", "geometry",
    ]]


if __name__ == "__main__":
    main()
