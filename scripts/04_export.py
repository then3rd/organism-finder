"""Turn out/<slug>/<slug>.gpkg into the tabular and field deliverables.

The map itself is <slug>.qgs - see scripts/05_qgis_project.py.

  out/<slug>/candidates.csv     one row per eligible public parcel
  out/<slug>/hotspots.csv       one row per scouting cell
  out/<slug>/summary.md         per-owner rollup, framed by the taxon's mode
  out/<slug>/scouting.kml/.gpx  spatially spread waypoints for a phone GPS

`mode` decides whether this reads as a permit document, a place-to-go-look document or a
foraging one, and which of `Owner.collect` / `Owner.forage` the administrator table asks;
`sensitive` is carried through as a label on the output, the numbers themselves being the
same numbers every other taxon gets. Habitat conditions add their own columns, their own
`note` prose and, for a burn, a hazard block - a recent burn is the one thing this
pipeline points people at that can hurt them by being right.

    .venv/bin/python scripts/04_export.py [taxon-slug]
"""
from pathlib import Path
import datetime
import sys

import geopandas as gpd
import gpxpy.gpx
import numpy as np
import pandas as pd
import simplekml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factsheet  # noqa: E402
import grid as grid_mod  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402

N_WAYPOINTS = 50
HOTSPOT_MIN_PCT = 25.0      # mirrors 03_overlay.py, for the no-cells wording only
N_TABLE = 25                # how many of those waypoints summary.md lists
MIN_SEPARATION_M = 8000     # keep waypoints spread out instead of 50 adjacent cells


def spread(hot, n=N_WAYPOINTS, sep=MIN_SEPARATION_M):
    """Pick strong cells that are spread out and cover every managing unit.

    Thousands of cells tie at 100 % cover, so a plain greedy pass returns 50 neighbours in
    one canyon. Instead: round-robin over managing units, each time taking that unit's best
    remaining cell that is at least `sep` from everything already picked. Round-robining
    over units rather than owners also keeps one large agency from taking every slot.
    """
    if not len(hot):
        return hot
    pts = hot.representative_point()
    xy = np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])
    order = np.argsort(-hot["species_acres"].to_numpy(), kind="stable")
    by_unit = {}
    for i in order:
        by_unit.setdefault(hot["managing_unit"].iat[i], []).append(i)
    queues = sorted(by_unit.values(), key=len, reverse=True)

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
    year = datetime.date.today().year
    # Named rather than assumed: stage 03 chose the lattice and this stage only reports it.
    shape = grid_mod.recall(paths.grid_marker(sp))
    cols = ["rank", "owner", "owner_name", "managing_unit", "county", "DESIG", "in_lwc",
            "in_excluded", "land_acres", "species_acres", "species_pct", "evidence",
            "unit_url"]
    cols += [c for c in sp.condition_columns + ["burn_year"] if c not in cols]
    c = cand[[x for x in cols if x in cand.columns]].copy()
    pts = cand.representative_point().to_crs(4326)
    c["lat"], c["lon"] = pts.y.round(5).to_numpy(), pts.x.round(5).to_numpy()
    for col in ("land_acres", "species_acres", "species_pct", *sp.condition_columns):
        if col in c.columns:
            c[col] = c[col].round(1)
    c.to_csv(out / "candidates.csv", index=False)

    h = hot.drop(columns="geometry")
    for col in ("cell_acres", "species_acres", "species_pct", *sp.condition_columns):
        if col in h.columns:
            h[col] = h[col].round(1)
    h.to_csv(out / "hotspots.csv", index=False)

    # Rolled up by administrator first and unit second: "which agency" is the question
    # ownership made possible, and it is the one a reader has before "which office".
    rollup = (
        cand.groupby(["owner_name", "managing_unit"])
        .agg(parcels=("rank", "size"), land_acres=("land_acres", "sum"),
             species_acres=("species_acres", "sum"))
        .sort_values("species_acres", ascending=False)
        .round(0)
        .astype(int)
    )
    hot_roll = hot.groupby(["owner_name", "managing_unit"]).size().rename("cells")
    rollup = rollup.join(hot_roll).fillna(0).astype(int)
    by_owner = (
        cand.groupby("owner_name")
        .agg(parcels=("rank", "size"), land_acres=("land_acres", "sum"),
             species_acres=("species_acres", "sum"))
        .sort_values("species_acres", ascending=False).round(0).astype(int)
    )

    lines = [
        factsheet.heading(sp, reg),
        "",
        f"Cross-reference of **{reg.name} Surface Management Agency** polygons (who",
        f"administers the ground) x "
        f"{factsheet.how(sp, samples=int(cand['samples'].sum()), year=year)}.",
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
        "## By administrator",
        "",
        f"| administrator | parcels | acres | {sp.short} acres |",
        "|---|---:|---:|---:|",
    ]
    for name, r in by_owner.iterrows():
        lines.append(f"| {name} | {r['parcels']:,} | {r['land_acres']:,} | "
                     f"{r['species_acres']:,} |")

    lines += factsheet.owner_note(sp, list(cand["owner"].unique()))

    lines += [
        "",
        "## By managing unit",
        "",
        f"| administrator | unit | parcels | acres | {sp.short} acres | 1 km² cells |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for (owner_name, unit), r in rollup.iterrows():
        lines.append(
            f"| {owner_name} | {unit} | {r['parcels']:,} | {r['land_acres']:,} | "
            f"{r['species_acres']:,} | {r['cells']:,} |"
        )

    picks = spread(hot).head(N_TABLE)
    if not len(picks):
        lines += [
            "",
            "## Nowhere to scout",
            "",
            f"No 1 km² cell on screened public surface reaches {HOTSPOT_MIN_PCT:g} % "
            f"{sp.short}",
            "cover, so there is no waypoint list and `scouting.kml` / `scouting.gpx` were not",
            f"written. The parcels above do carry mapped {sp.short}, but too thinly spread to",
            "point at a spot on the ground - work from `candidates.csv` and the administrator.",
        ]
    else:
        verb = factsheet.GOING[sp.mode]
        lines += [
            "",
            f"## {len(picks)} place{'s' if len(picks) > 1 else ''} {verb}",
            "",
            f"Best {shape.label} cell first, then everything within",
            f"{MIN_SEPARATION_M // 1000} km of it dropped, round-robined over managing units",
            f"— so these are spread across {reg.name} and across agencies rather than",
            f"{len(picks)} adjacent cells in one canyon. Coordinates are WGS84.",
            "",
            f"| rank | administrator | unit | county | {sp.short} % | {sp.short} acres | evidence | lat | lon |",
            "|---:|---|---|---|---:|---:|---|---:|---:|",
        ]
        for _, r in picks.iterrows():
            lines.append(
                f"| {r['rank']} | {r['owner']} | {r['managing_unit']} | {r['county']} | "
                f"{r['species_pct']:.0f} | {r['species_acres']:.0f} | {r['evidence']} | "
                f"{r['lat']:.5f} | {r['lon']:.5f} |"
            )

    lines += factsheet.caveats(sp, reg)
    (out / "summary.md").write_text("\n".join(lines))
    print("  -> candidates.csv, hotspots.csv, summary.md")
    return rollup


def waypoints(hot, sp, out):
    picks = spread(hot)
    if not len(picks):
        print("  -> no cells over threshold, skipping scouting.kml / scouting.gpx")
        return
    what = factsheet.ACTIVITY[sp.mode]
    kml = simplekml.Kml(name=f"{factsheet.title(sp)} {what} - {sp.binomial}")
    gpx = gpxpy.gpx.GPX()
    for _, r in picks.iterrows():
        label = f"{int(r['rank']):04d} {r['owner']} {r['species_pct']:.0f}%"
        desc = (f"{r['evidence']}\n{r['species_acres']:.0f} {sp.short} acres in cell\n"
                f"{r['county']} County - {r['managing_unit']}")
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


def nothing_qualified(sp, reg, out):
    """Stage 03 found no eligible ground. Write the one thing there is to say.

    The funnel is still the deliverable - it shows how far the screening got and where it
    ran out - so summary.md carries it and the field deliverables are skipped.
    """
    # A previous run of this taxon may have qualified - a threshold moved, or the EVT
    # keywords changed. Leaving its deliverables next to a summary saying nothing qualified
    # is the most misleading state this stage can produce, so clear them.
    for stale in ("candidates.csv", "hotspots.csv", "scouting.kml", "scouting.gpx"):
        (out / stale).unlink(missing_ok=True)

    funnel = pd.read_csv(out / "funnel.csv")
    lines = [
        factsheet.heading(sp, reg),
        "",
        f"**No {reg.name} public ground qualified.** The *{sp.binomial}* range does reach",
        f"{reg.name} and the cover data for {sp.short} exists here, but no parcel open to a",
        f"{sp.mode}-mode screen carries enough mapped cover to be worth the drive.",
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
        "## What this does and does not mean",
        "",
        f"* {factsheet.title(sp)} grows in {reg.name}. The screening says only that it does not grow",
        "  in mapped quantity on the public ground this taxon's mode opens up - the rest is",
        "  private, tribal, or held by an agency that will not permit what you are asking.",
        "  The `other_cells` layer of a completed run is where that ground would show.",
    ]
    lines += factsheet.caveats(sp, reg)
    (out / "summary.md").write_text("\n".join(lines))
    print(f"{sp.common_name}: nothing qualified -> summary.md (no csv/kml/gpx)")


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    out = paths.out_dir(sp)
    gpkg = paths.gpkg_path(sp)

    if not gpkg.exists():
        return nothing_qualified(sp, reg, out)

    cand = gpd.read_file(gpkg, layer="candidates")
    hot = gpd.read_file(gpkg, layer="hotspots")
    funnel = pd.read_csv(out / "funnel.csv")
    print(f"{sp.common_name}: candidates={len(cand)}  hotspots={len(hot)}  "
          f"owners={cand['owner'].nunique()}")
    tables(cand, hot, funnel, sp, reg, out)
    waypoints(hot, sp, out)


if __name__ == "__main__":
    main()
