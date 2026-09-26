"""Turn out/<slug>/<slug>.gpkg into the tabular and field deliverables.

The map itself is <slug>.qgs - see scripts/05_qgis_project.py.

  out/<slug>/candidates.csv     one row per eligible public parcel
  out/<slug>/hotspots.csv       one row per scouting cell
  out/<slug>/summary.md         per-owner rollup, framed by the taxon's mode
  out/<slug>/scouting.kml/.gpx  spatially spread waypoints for a phone GPS
  out/<slug>/campsites.kml/.gpx every established campsite on open ground (camp mode only)

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
import habitat  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import spaced  # noqa: E402

N_WAYPOINTS = 50
HOTSPOT_MIN_PCT = 25.0      # mirrors 03_overlay.py, for the no-cells wording only
N_TABLE = 25                # how many of those waypoints summary.md lists
MIN_SEPARATION_M = 8000     # keep waypoints spread out instead of 50 adjacent cells


def spread(hot, n=N_WAYPOINTS, sep=MIN_SEPARATION_M, by_rank=False):
    """Pick strong cells that are spread out and cover every managing unit.

    Thousands of cells tie at 100 % cover, so a plain greedy pass returns 50 neighbours in
    one canyon. Instead: round-robin over managing units, each time taking that unit's best
    remaining cell that is at least `sep` from everything already picked. Round-robining
    over units rather than owners also keeps one large agency from taking every slot.

    `by_rank` takes each unit's cells in stage 03's rank order rather than by cover acres.
    It is for a taxon with scoring conditions, whose rank leads with the score - the
    camping screen ranks by nearness to water first, and acres of flat ground alone would
    hand back the driest cells on the map.
    """
    if not len(hot):
        return hot
    pts = hot.representative_point()
    xy = np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])
    if by_rank:
        order = np.argsort(hot["rank"].to_numpy(), kind="stable")
    else:
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


def ranked(sp):
    """Whether waypoints follow stage 03's rank rather than cover acres - see spread()."""
    return bool(habitat.scores(sp.conditions))


def tables(cand, hot, funnel, sp, reg, out, sites=None, best=None):
    year = datetime.date.today().year
    # Named rather than assumed: stage 03 chose the lattice and this stage only reports it.
    shape = grid_mod.recall(paths.grid_marker(sp, reg))
    # `reg.desig_field` rather than a literal: a region whose SMA layer names the column
    # something else still exports it, and one that has no such column exports nothing.
    # The comprehension below already drops whatever stage 03 did not write.
    cols = ["rank", "owner", "owner_name", "managing_unit", "county", reg.desig_field,
            "in_lwc", "in_excluded", "land_acres", "species_acres", "species_pct",
            "evidence", "unit_url"]
    cols += [c for c in sp.condition_columns + ["burn_year", "campsites"] if c not in cols]
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

    lines += factsheet.owner_note(sp, reg, list(cand["owner"].unique()))

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

    picks = spread(hot, by_rank=ranked(sp)).head(N_TABLE)
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
        ]
        # The camping screen's table carries its ranking score and the campsite count;
        # every other taxon's table is the one it always had.
        extra = [c for c in ("water_pct", "campsites")
                 if sp.mode == species_mod.CAMP and c in picks.columns]
        head = {"water_pct": "near water %", "campsites": "primitive sites"}
        lines += [
            f"| rank | administrator | unit | county | {sp.short} % | {sp.short} acres | "
            + "".join(f"{head[c]} | " for c in extra) + "evidence | lat | lon |",
            "|---:|---|---|---|---:|---:|" + "---:|" * len(extra) + "---|---:|---:|",
        ]
        for _, r in picks.iterrows():
            lines.append(
                f"| {r['rank']} | {r['owner']} | {r['managing_unit']} | {r['county']} | "
                f"{r['species_pct']:.0f} | {r['species_acres']:.0f} | "
                + "".join(f"{r[c]:.0f} | " for c in extra)
                + f"{r['evidence']} | {r['lat']:.5f} | {r['lon']:.5f} |"
            )

    if best is not None:
        lines += best_section(best, out)
    if sites is not None:
        lines += campsite_section(sites, hot, reg)
    lines += factsheet.caveats(sp, reg)
    (out / "summary.md").write_text("\n".join(lines))
    print("  -> candidates.csv, hotspots.csv, summary.md")
    return rollup


def waypoints(hot, sp, out):
    picks = spread(hot, by_rank=ranked(sp))
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


# --- established campsites (mode="camp") -------------------------------------
CLASS_ORDER = ("primitive", "developed", "unclassified")
CLASS_MARK = {"primitive": "P", "developed": "D", "unclassified": "?"}


def open_sites(sites):
    """Sites on ground this screen is open to, primitive first. The rest stay in the
    GeoPackage and on the map, faded, but a waypoint file is a list of places to drive to."""
    s = sites[sites["on_screened"].astype(bool)].copy()
    s["_class"] = s["site_class"].map({c: i for i, c in enumerate(CLASS_ORDER)})
    return s.sort_values(["_class", "site_id"]).drop(columns="_class")


def best_sites(sites, hot, n=N_TABLE, sep=MIN_SEPARATION_M):
    """Primitive sites in the best-ranked cells, spread out the way the cells are.

    A site takes the rank of the cell it sits in, sites in no qualifying cell go last,
    and each pick drops everything within `sep` of it - the same reasoning as spread():
    without it the list is twenty pads along one creek.
    """
    prim = sites[sites["site_class"] == "primitive"]
    if not len(prim):
        return prim
    j = gpd.sjoin(prim, hot[["rank", "geometry"]].rename(columns={"rank": "cell_rank"}),
                  how="left", predicate="within")
    j = j[~j.index.duplicated()].drop(columns="index_right")
    j = j.sort_values(["cell_rank", "site_id"], na_position="last")
    return j.iloc[spaced(j.geometry.x, j.geometry.y, sep, n)]


def campsite_section(sites, hot, reg):
    """summary.md: how many established sites, on whose ground, and the best of them."""
    ok = open_sites(sites)
    lines = [
        "",
        "## Established campsites",
        "",
        f"{len(sites):,} campsites are mapped in {reg.name} (OpenStreetMap, USFS, BLM); "
        f"{len(ok):,} sit on",
        "ground where camping is allowed. Counts are by who administers the ground under",
        "each site, not by who the source says runs it.",
        "",
        "| administrator | " + " | ".join(CLASS_ORDER) + " |",
        "|---|" + "---:|" * len(CLASS_ORDER),
    ]
    table = (ok.groupby(["owner_name", "site_class"]).size().unstack(fill_value=0)
             .reindex(columns=list(CLASS_ORDER), fill_value=0))
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    for name, r in table.iterrows():
        lines.append(f"| {name} | " + " | ".join(f"{int(r[c]):,}" for c in CLASS_ORDER)
                     + " |")
    best = best_sites(ok, hot)
    if len(best):
        pts = best.to_crs(4326)
        lines += [
            "",
            f"### {len(best)} primitive sites in the best-ranked cells",
            "",
            f"Taken in cell rank order and spread at least {MIN_SEPARATION_M // 1000} km "
            "apart. Every open site",
            "is in `campsites.gpx` / `campsites.kml`, not just these.",
            "",
            "| cell rank | site | administrator | source | notes | lat | lon |",
            "|---:|---|---|---|---|---:|---:|",
        ]
        for (_, r), p in zip(best.iterrows(), pts.geometry):
            rank = "" if pd.isna(r["cell_rank"]) else f"{int(r['cell_rank'])}"
            name = r["name"] if isinstance(r["name"], str) and r["name"] else "(unnamed)"
            detail = (r["detail"] or "") if isinstance(r["detail"], str) else ""
            lines.append(f"| {rank} | {name} | {r['owner']} | {r['source']} | "
                         f"{detail} | {p.y:.5f} | {p.x:.5f} |")
    return lines


def campsite_waypoints(sites, out):
    """Every established site on open ground, as its own waypoint file.

    Separate from scouting.gpx on purpose: those are cells to go and look at, these are
    places somebody has already camped, and a phone should be able to show one without
    the other.
    """
    ok = open_sites(sites)
    if not len(ok):
        print("  -> no campsites on open ground, skipping campsites.kml / campsites.gpx")
        return
    kml = simplekml.Kml(name="Established campsites")
    folders = {c: kml.newfolder(name=f"{c} sites") for c in CLASS_ORDER}
    gpx = gpxpy.gpx.GPX()
    pts = ok.to_crs(4326)
    for (_, r), p in zip(ok.iterrows(), pts.geometry):
        name = r["name"] if isinstance(r["name"], str) and r["name"] else "unnamed"
        label = f"{CLASS_MARK.get(r['site_class'], '?')} {name}"
        desc = "\n".join(x for x in (
            f"{r['site_class']} site ({r['source']})",
            r["detail"] if isinstance(r["detail"], str) else "",
            f"fee: {r['fee']}" if isinstance(r["fee"], str) and r["fee"] else "",
            f"{r['owner_name']} - camping: {r['camp']}",
            r["url"] if isinstance(r["url"], str) else "",
        ) if x)
        folders.get(r["site_class"], folders["unclassified"]).newpoint(
            name=label, description=desc, coords=[(p.x, p.y)])
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
            p.y, p.x, name=label, description=desc,
            symbol="Campground" if r["site_class"] == "developed" else "Flag, Green"))
    kml.save(str(out / "campsites.kml"))
    (out / "campsites.gpx").write_text(gpx.to_xml())
    counts = ok["site_class"].value_counts()
    print("  -> campsites.kml, campsites.gpx ("
          + ", ".join(f"{counts.get(c, 0)} {c}" for c in CLASS_ORDER) + ")")


# --- best spots (scripts/03b_best.py) ----------------------------------------------
def best_section(best, out):
    """summary.md: how the shortlist was cut, and the shortlist, one table per unit."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "best", Path(__file__).resolve().parent / "03b_best.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    funnel = pd.read_csv(out / "best_funnel.csv")
    units = best["unit"].nunique()
    lines = [
        "",
        f"## Best spots ({len(best)}, in {units} units)",
        "",
        "The cells above narrowed to a shortlist: free dispersed ground only, a drivable",
        "track close by, away from pavement, and no salt flat or development - then scored",
        "equally on nearness to water, shade, habitat variety and quiet, with a small bonus",
        f"where the way in is a rough 4x4 track. The best {b.PER_UNIT} in each BLM field",
        f"office and national forest, at least {b.MIN_SEP_M / 1000:g} km apart. The same",
        "spots are in `best_spots.gpx` / `.kml` and on the map as black hexagons.",
        "",
        "| step | cells |",
        "|---|---:|",
        *[f"| {r['stage']} | {int(r['cells']):,} |" for _, r in funnel.iterrows()],
        "",
        f"Score is 0-1, plus {b.ROUGH_BONUS:g} where the way in is a rough 4x4 track. "
        "Trees % is shade; variety is the effective number of habitat types "
        "in the cell (1 = one vegetation type); access is the nearest drivable unpaved road.",
    ]
    for unit, g in best.groupby("unit", sort=False):
        lines += [
            "",
            f"### {unit}",
            "",
            "| # | score | flat % | water % | trees % | variety | km to pavement | "
            "access | nearest known site | lat | lon |",
            "|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|",
        ]
        for _, r in g.iterrows():
            road = r["access_class"] or ""
            if isinstance(r["access_name"], str) and r["access_name"]:
                road += f" ({r['access_name']})"
            road += f", {r['dist_access_m']:.0f} m"
            site = ("" if pd.isna(r["nearest_site_m"])
                    else f"{r['nearest_site_m'] / 1000:.1f} km")
            lines.append(
                f"| {int(r['best_rank'])} | {r['score']:.2f} | {r['species_pct']:.0f} | "
                f"{r['water_pct']:.0f} | {r['tree_pct']:.0f} | {r['variety']:.1f} | "
                f"{r['dist_paved_m'] / 1000:.1f} | {road} | {site} | "
                f"{r['lat']:.5f} | {r['lon']:.5f} |")
    return lines


def best_waypoints(best, out):
    """best_spots.gpx / .kml - the shortlist, one KML folder per unit."""
    kml = simplekml.Kml(name="Best camping spots")
    gpx = gpxpy.gpx.GPX()
    for unit, g in best.groupby("unit", sort=False):
        folder = kml.newfolder(name=unit)
        for _, r in g.iterrows():
            label = f"{unit} #{int(r['best_rank'])}"
            road = r["access_class"] or "road"
            if isinstance(r["access_name"], str) and r["access_name"]:
                road += f" ({r['access_name']})"
            desc = "\n".join((
                f"score {r['score']:.2f}: water {r['s_water']:.2f}, shade {r['s_shade']:.2f}, "
                f"variety {r['s_variety']:.2f}, quiet {r['s_quiet']:.2f}",
                f"{r['species_pct']:.0f} % flat, {r['tree_pct']:.0f} % trees, "
                f"{r['dist_paved_m'] / 1000:.1f} km from pavement",
                f"leave the {road} at {r['access_lat']:.5f}, {r['access_lon']:.5f} "
                f"({r['dist_access_m']:.0f} m from here)",
                f"{r['owner_name']} - {r['county']}",
            ))
            folder.newpoint(name=label, description=desc, coords=[(r["lon"], r["lat"])])
            gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
                r["lat"], r["lon"], name=label, description=desc, symbol="Campground"))
    kml.save(str(out / "best_spots.kml"))
    (out / "best_spots.gpx").write_text(gpx.to_xml())
    print(f"  -> best_spots.kml, best_spots.gpx ({len(best)} spots)")


def nothing_qualified(sp, reg, out):
    """Stage 03 found no eligible ground. Write the one thing there is to say.

    The funnel is still the deliverable - it shows how far the screening got and where it
    ran out - so summary.md carries it and the field deliverables are skipped.
    """
    # A previous run of this taxon may have qualified - a threshold moved, or the EVT
    # keywords changed. Leaving its deliverables next to a summary saying nothing qualified
    # is the most misleading state this stage can produce, so clear them.
    for stale in ("candidates.csv", "hotspots.csv", "scouting.kml", "scouting.gpx",
                  "campsites.kml", "campsites.gpx", "best_spots.kml",
                  "best_spots.gpx", "best_funnel.csv"):
        (out / stale).unlink(missing_ok=True)

    funnel = pd.read_csv(out / "funnel.csv")
    intro, meaning = factsheet.nothing_qualified(sp, reg)
    lines = [
        factsheet.heading(sp, reg),
        "",
        *intro,
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
        *meaning,
    ]
    lines += factsheet.caveats(sp, reg)
    (out / "summary.md").write_text("\n".join(lines))
    print(f"{sp.common_name}: nothing qualified -> summary.md (no csv/kml/gpx)")


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve(sys.argv)
    out = paths.out_dir(sp, reg)
    gpkg = paths.gpkg_path(sp, reg)

    if not gpkg.exists():
        return nothing_qualified(sp, reg, out)

    cand = gpd.read_file(gpkg, layer="candidates")
    hot = gpd.read_file(gpkg, layer="hotspots")
    funnel = pd.read_csv(out / "funnel.csv")
    print(f"{sp.common_name}: candidates={len(cand)}  hotspots={len(hot)}  "
          f"owners={cand['owner'].nunique()}")
    sites = best = None
    if sp.mode == species_mod.CAMP:
        sites = gpd.read_file(gpkg, layer="campsites")
        try:
            best = gpd.read_file(gpkg, layer="best_spots")
        except Exception:
            print("  no best_spots layer - run `just best` for the shortlist")
    tables(cand, hot, funnel, sp, reg, out, sites, best)
    waypoints(hot, sp, out)
    if sites is not None:
        campsite_waypoints(sites, out)
    if best is not None:
        best_waypoints(best, out)


if __name__ == "__main__":
    main()
