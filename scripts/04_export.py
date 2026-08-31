"""Turn out/<slug>/<slug>_blm.gpkg into the tabular and field deliverables.

The map itself is <slug>_blm.qgs - see scripts/05_qgis_project.py.

  out/<slug>/candidates.csv     one row per eligible BLM polygon
  out/<slug>/hotspots.csv       one row per scouting cell
  out/<slug>/summary.md         permit-oriented summary + per-field-office rollup
  out/<slug>/scouting.kml/.gpx  spatially spread waypoints for a phone GPS

    .venv/bin/python scripts/04_export.py [species-slug]
"""
from pathlib import Path
import sys
import textwrap

import geopandas as gpd
import gpxpy.gpx
import numpy as np
import pandas as pd
import simplekml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402

N_WAYPOINTS = 50
N_TABLE = 25                # how many of those waypoints summary.md lists
MIN_SEPARATION_M = 8000     # keep waypoints spread out instead of 50 adjacent cells


def title(sp):
    return sp.common_name[:1].upper() + sp.common_name[1:]


def spread(hot, n=N_WAYPOINTS, sep=MIN_SEPARATION_M):
    """Pick strong cells that are spread out and cover every field office.

    Thousands of cells tie at 100 % cover, so a plain greedy pass returns 50 neighbours in
    one canyon. Instead: round-robin over field offices, each time taking that office's best
    remaining cell that is at least `sep` from everything already picked.
    """
    pts = hot.representative_point()
    xy = np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])
    order = np.argsort(-hot["species_acres"].to_numpy(), kind="stable")
    by_office = {}
    for i in order:
        by_office.setdefault(hot["field_office"].iat[i], []).append(i)
    queues = sorted(by_office.values(), key=len, reverse=True)

    chosen, used = [], np.zeros(len(hot), dtype=bool)
    while len(chosen) < n and any(queues):
        progressed = False
        for q in queues:
            if len(chosen) >= n:
                break
            while q:
                i = q.pop(0)
                if used[i]:
                    continue
                chosen.append(i)
                used |= np.hypot(xy[:, 0] - xy[i, 0], xy[:, 1] - xy[i, 1]) < sep
                progressed = True
                break
        if not progressed:
            break
    return hot.iloc[chosen].reset_index(drop=True)


def tables(cand, hot, funnel, sp, reg, out):
    cols = ["rank", "field_office", "county", "DESIG", "in_lwc", "blm_acres",
            "species_acres", "species_pct", "dominant_evt", "office_url"]
    c = cand[cols].copy()
    pts = cand.representative_point().to_crs(4326)
    c["lat"], c["lon"] = pts.y.round(5).to_numpy(), pts.x.round(5).to_numpy()
    for col in ("blm_acres", "species_acres", "species_pct"):
        c[col] = c[col].round(1)
    c.to_csv(out / "candidates.csv", index=False)

    h = hot.drop(columns="geometry").copy()
    for col in ("blm_acres", "species_acres", "species_pct"):
        h[col] = h[col].round(1)
    h.to_csv(out / "hotspots.csv", index=False)

    rollup = (
        cand.groupby("field_office")
        .agg(parcels=("rank", "size"), blm_acres=("blm_acres", "sum"),
             species_acres=("species_acres", "sum"))
        .sort_values("species_acres", ascending=False)
        .round(0)
        .astype(int)
    )
    hot_roll = hot.groupby("field_office").size().rename("scouting_cells")
    rollup = rollup.join(hot_roll).fillna(0).astype(int)

    lines = [
        f"# {title(sp)} on BLM land - transplant permit screening",
        "",
        f"Cross-reference of three layers: **BLM {reg.name} Surface Management Agency** (who",
        f"issues the permit) x **Little 1971 *{sp.binomial}* range** (species filter) x",
        f"**LANDFIRE EVT 30 m** (where {sp.short} actually grows).",
        "",
        "## How the acreage narrows",
        "",
        "| stage | features | acres |",
        "|---|---:|---:|",
    ]
    for _, r in funnel.iterrows():
        lines.append(f"| {r['stage']} | {int(r['features']):,} | {r['acres']:,.0f} |")
    lines += [
        "",
        "## Where to apply",
        "",
        "Live-plant / vegetative-product permits are issued by the **field office** that",
        f"administers the ground, not by the state office. Acres below are {sp.short} acres on",
        "eligible (non-Wilderness, non-WSA, non-monument) BLM surface.",
        "",
        f"| field office | parcels | BLM acres | {sp.short} acres | 1 km² hex cells |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, r in rollup.iterrows():
        lines.append(
            f"| {name} | {r['parcels']:,} | {r['blm_acres']:,} | "
            f"{r['species_acres']:,} | {r['scouting_cells']:,} |"
        )
    lines += [
        "",
        f"## {N_TABLE} places to scout",
        "",
        "The same greedy pick as `scouting.gpx`: best hex cell first, then everything within",
        f"{MIN_SEPARATION_M // 1000} km of it dropped, repeat — so these are spread across",
        f"{reg.name} rather than {N_TABLE} adjacent cells in one canyon. Coordinates are WGS84",
        "and land inside the cell.",
        "",
        f"| rank | field office | county | {sp.short} % | {sp.short} acres | dominant type | lat | lon |",
        "|---:|---|---|---:|---:|---|---:|---:|",
    ]
    for _, r in spread(hot).head(N_TABLE).iterrows():
        lines.append(
            f"| {r['rank']} | {r['field_office']} | {r['county']} | {r['species_pct']:.0f} | "
            f"{r['species_acres']:.0f} | {r['dominant_evt']} | {r['lat']:.5f} | {r['lon']:.5f} |"
        )
    lines += [
        "",
        "## Before you dig",
        "",
        "* Call the field office first - this screening does not model ACEC boundaries, grazing",
        "  or mineral leases, rights-of-way, sage-grouse habitat closures, developed recreation",
        "  sites, riparian buffers, or cultural-resource restrictions.",
        "* Little's range map is 1:2,000,000 (1971) and LANDFIRE EVT is 30 m *modelled* cover.",
        # the caveat is per-species prose of unknown length, so wrap it to match the
        # hand-wrapped bullets around it
        textwrap.fill(
            "  Both are screening tools. Ground-truth the species before collecting - "
            + sp.ground_truth_caveat,
            width=90, initial_indent="", subsequent_indent="  ",
            # "pinyon-juniper" split across lines renders as "pinyon- juniper"
            break_on_hyphens=False, break_long_words=False,
        ),
        "* Lands with wilderness characteristics (`in_lwc`) are not closed, but expect scrutiny.",
        "* The `off_blm_cells` layer in the GeoPackage grids the same range x EVT screen over",
        "  ground BLM does *not* administer - private, state, tribal, other agencies. It shows",
        "  where the stands are, and there is no BLM permit to be had on any of it. Nothing in",
        "  this document is derived from it.",
        "",
    ]
    (out / "summary.md").write_text("\n".join(lines))
    print("  -> candidates.csv, hotspots.csv, summary.md")
    return rollup


def waypoints(hot, sp, out):
    picks = spread(hot)
    kml = simplekml.Kml(name=f"{title(sp)} scouting - BLM land")
    gpx = gpxpy.gpx.GPX()
    for _, r in picks.iterrows():
        label = f"{int(r['rank']):04d} {r['field_office']} {r['species_pct']:.0f}%"
        desc = (f"{r['dominant_evt']}\n{r['species_acres']:.0f} {sp.short} acres in cell\n"
                f"{r['county']} County - {r['field_office']}")
        kml.newpoint(name=label, description=desc, coords=[(r["lon"], r["lat"])])
        gpx.waypoints.append(
            gpxpy.gpx.GPXWaypoint(r["lat"], r["lon"], name=label, description=desc)
        )
    poly_folder = kml.newfolder(name="Cell boundaries")
    for _, r in picks.to_crs(4326).iterrows():
        geom = r.geometry
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            p = poly_folder.newpolygon(
                name=f"{int(r['rank']):04d}",
                outerboundaryis=[(x, y) for x, y in poly.exterior.coords],
            )
            p.style.polystyle.color = "7fffe500"   # cyan, KML is aabbggrr
            p.style.linestyle.color = "ffffe500"
    kml.save(str(out / "scouting.kml"))
    (out / "scouting.gpx").write_text(gpx.to_xml())
    print(f"  -> scouting.kml, scouting.gpx ({len(picks)} waypoints, "
          f">={MIN_SEPARATION_M/1000:g} km apart)")


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    out = paths.out_dir(sp)
    gpkg = paths.gpkg_path(sp)

    cand = gpd.read_file(gpkg, layer="candidates")
    hot = gpd.read_file(gpkg, layer="hotspots")
    funnel = pd.read_csv(out / "funnel.csv")
    print(f"{sp.common_name}: candidates={len(cand)}  hotspots={len(hot)}")
    tables(cand, hot, funnel, sp, reg, out)
    waypoints(hot, sp, out)


if __name__ == "__main__":
    main()
