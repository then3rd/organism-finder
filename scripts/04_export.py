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
import textwrap

import geopandas as gpd
import gpxpy.gpx
import numpy as np
import pandas as pd
import simplekml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import habitat  # noqa: E402
import ownership  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402

N_WAYPOINTS = 50
HOTSPOT_MIN_PCT = 25.0      # mirrors 03_overlay.py, for the no-cells wording only
N_TABLE = 25                # how many of those waypoints summary.md lists
MIN_SEPARATION_M = 8000     # keep waypoints spread out instead of 50 adjacent cells


def title(sp):
    return sp.common_name[:1].upper() + sp.common_name[1:]


WHAT = {
    "collect": "transplant permit screening",
    "observe": "where to go and look",
    "forage": "where to go and pick",
}


def heading(sp, reg):
    return f"# {title(sp)} on {reg.name} public land - {WHAT[sp.mode]}"


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


def owner_note(sp, codes):
    """The per-owner authority paragraph. This is the part that changes with ownership.

    Which of the two taking fields is consulted is the mode's to decide, and the two give
    different answers on the same ground: the Forest Service will not let you dig a tree
    without paperwork and will let you fill a bag with morels without any.
    """
    field = ownership.taking(sp.mode) or "collect"
    question = ("may mushrooms be taken?" if sp.mode == species_mod.FORAGE
                else "may a plant be taken?")
    lines = ["", "## Who administers it, and what that means", "",
             f"| administrator | ground | {question} |", "|---|---|---|"]
    for _, o in ownership.summarize(codes):
        verb = {ownership.FREE: "yes, personal use, no permit",
                ownership.PERMIT: "yes, with a permit",
                ownership.ASK: "case by case - ask first",
                ownership.PROHIBITED: "no"}[getattr(o, field)]
        lines.append(f"| {o.name} ({o.short}) | {o.tenure} | {verb} |")
    lines.append("")
    if sp.mode == species_mod.OBSERVE:
        # The column is still worth printing in observe mode - it is the reason this is
        # an observe-mode taxon on half these owners - but it is not what the map is for.
        lines += [
            f"The last column is context, not an invitation: {title(sp)} is screened in",
            "observe mode and nothing here is a suggestion to take one. See *Before you go*.",
            "",
        ]
    if sp.mode == species_mod.FORAGE:
        lines += [
            "Personal-use limits and free-use permit rules are set forest by forest and",
            "field office by field office, and they change from year to year. The bullets",
            "below say who to ask; none of them is a substitute for asking.",
            "",
        ]
    for _, o in ownership.summarize(codes):
        lines.append(textwrap.fill(
            f"* **{o.short}** - {ownership.authority_for(o, sp.mode)}.",
            width=90, subsequent_indent="  ",
            break_on_hyphens=False, break_long_words=False))
    return lines


def tables(cand, hot, funnel, sp, reg, out):
    year = datetime.date.today().year
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

    how = {
        (species_mod.LITTLE, species_mod.EVT):
            f"**Little 1971 *{sp.binomial}* range** (species filter) x "
            f"**LANDFIRE EVT 30 m** (where {sp.short} actually grows)",
        (None, species_mod.EVT):
            f"**LANDFIRE EVT 30 m** (where {sp.short} actually grows). There is no range "
            "filter: this plant occupies the region broadly enough that a range polygon "
            "would narrow nothing",
        (species_mod.GBIF, species_mod.OCCURRENCE):
            f"**{int(cand['samples'].sum()):,} georeferenced GBIF records** buffered by "
            f"their own stated accuracy (at least {sp.occurrence_buffer_m} m). This is a "
            "record of where people have *looked and found*, not modelled cover",
    }.get((sp.range_source, sp.cover), "the sources named in scripts/species.py")

    if sp.kind == "fungus":
        # The EVT class names the host stand, and saying "where the fungus grows" here
        # would be the one sentence in this document that is flatly untrue.
        how = (f"**LANDFIRE EVT 30 m** for the *host* community - the stand this fungus "
               f"fruits in, not the fungus, which no vegetation model maps")
    if sp.conditions:
        how += ", and " + " and ".join(
            f"**{c.label(year)}**" if c.required else f"scored by *{c.label(year)}*"
            for c in sp.conditions
        )

    lines = [
        heading(sp, reg),
        "",
        f"Cross-reference of **{reg.name} Surface Management Agency** polygons (who",
        f"administers the ground) x {how}.",
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

    lines += owner_note(sp, list(cand["owner"].unique()))

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
        verb = {"collect": "to scout", "observe": "to go and look",
                "forage": "to go and pick"}[sp.mode]
        lines += [
            "",
            f"## {len(picks)} place{'s' if len(picks) > 1 else ''} {verb}",
            "",
            "Best hex cell first, then everything within",
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

    lines += caveats(sp, reg)
    (out / "summary.md").write_text("\n".join(lines))
    print("  -> candidates.csv, hotspots.csv, summary.md")
    return rollup


def caveats(sp, reg):
    """The closing section. Says what the screening does not model, in either mode."""
    head = {"collect": "## Before you dig", "observe": "## Before you go",
            "forage": "## Before you pick"}[sp.mode]
    lines = ["", head, ""]
    if sp.mode == species_mod.FORAGE:
        lines += [
            "* **This map finds habitat, not mushrooms, and it identifies nothing.** Every",
            "  cell on it is a place the host stand and the conditions line up; whether",
            "  anything is fruiting there this week is weather, and what you have picked is a",
            "  question for a key and an expert, never for a map. *Gyromitra* comes up in the",
            "  same burns as black morels and has killed people; *Omphalotus* grows on the",
            "  same hardwood as oysters. Never eat a wild mushroom on the strength of where",
            "  you found it.",
            "* Personal use only. Every `free` in the table above means personal-use",
            "  quantities - selling what you pick is a different permit on every agency here,",
            "  and picking commercially without one is theft of public property.",
            "* Wilderness, WSAs and monuments are *included* rather than subtracted: picking",
            "  for the pot is lawful in them. What is closed to a forager is closed by",
            "  administrator instead, and those owners are already out of this document.",
            "* Cut or pinch, take what you will eat, and leave the duff and the dead wood",
            "  the way you found them - the organism is the ground, not what you carried out.",
        ]
    elif sp.mode == species_mod.COLLECT:
        lines += [
            "* Call the administering unit first - and note that a permit from one agency is",
            "  worth nothing on another's ground, so check whose parcel you are actually on.",
            "* This screening does not model ACEC boundaries, grazing or mineral leases,",
            "  rights-of-way, sage-grouse habitat closures, developed recreation sites,",
            "  riparian buffers, or cultural-resource restrictions.",
        ]
    else:
        lines += [
            "* **This is a looking map, not a collecting map.** Every taxon screened in",
            "  observe mode is here because taking it is either unlawful, futile, or both -",
            "  Utah's orchids depend on soil fungi that do not come up with the plant, so a",
            "  dug one dies whatever the paperwork says.",
            "* Wilderness, WSAs and national monuments are *included* here rather than",
            "  subtracted, because walking into them to look is exactly what they are for.",
            "  Ground rules still differ by unit - check before you drive.",
            "* Stay on the trail where there is one, photograph rather than pick, and do not",
            "  clear vegetation for the shot.",
        ]
    if any(c.kind == habitat.BURN for c in sp.conditions):
        lines += [
            "* **A recent burn is a hazard, not just habitat.** Standing dead trees come down",
            "  without warning in wind, ash pits stay hot under a crust for months, and the",
            "  road you can see on the imagery may have washed out in the first storm after",
            "  the fire. Many burns are also under a BAER closure order for one to three",
            "  seasons, which is exactly the window this map selects and which it does not",
            "  model - check the administering unit's closures before you drive.",
            "* The perimeter says a fire happened, not how hot it burned. This screening does",
            "  not read burn severity, and it counts unburned islands inside a perimeter as",
            "  burned, so treat a cell as a place to look rather than a place with morels.",
        ]
    for c in sp.conditions:
        if c.note:
            lines.append(textwrap.fill(
                f"* **{c.label(datetime.date.today().year)}** - {c.note}.",
                width=90, subsequent_indent="  ",
                break_on_hyphens=False, break_long_words=False))
    if sp.sensitive:
        lines += [
            f"* **{title(sp)} is flagged sensitive.** It is listed, dug, or both, and the",
            "  coordinates in these files are full precision, like every other taxon's.",
            "  Treat them accordingly: do not repost the waypoints, and do not lead anyone",
            "  to a patch you would not want dug.",
        ]
    src = ("LANDFIRE EVT is 30 m *modelled* cover, and for a fungus it is describing the "
           "host stand rather than the organism - the percentage on this map is host "
           "cover, and the fungus may be in none of it."
           if sp.kind == "fungus" else
           "Little's range map is 1:2,000,000 (1971) and LANDFIRE EVT is 30 m *modelled* "
           "cover. Both are screening tools."
           if sp.cover == species_mod.EVT else
           "Occurrence records say where somebody looked and found, which is not where the "
           "plant is. These species are small, briefly visible and unevenly searched - "
           "absence of records is absence of records.")
    lines.append(textwrap.fill(
        "* " + src + " Ground-truth "
        + ("this before acting on it - " if sp.kind == "fungus"
           else "the species before acting on this - ")
        + sp.ground_truth_caveat,
        width=90, subsequent_indent="  ",
        break_on_hyphens=False, break_long_words=False))
    lines += [
        "* Lands with wilderness characteristics (`in_lwc`) are not closed, but expect scrutiny.",
        "* The `other_cells` layer in the GeoPackage grids the same screen over ground this",
        "  document cannot act on - private, tribal, closed withdrawals, and any public owner",
        f"  this taxon's {sp.mode} mode rules out. It shows where the stands are and confers",
        "  nothing. Nothing above is derived from it.",
        "",
    ]
    return lines


def waypoints(hot, sp, out):
    picks = spread(hot)
    if not len(picks):
        print("  -> no cells over threshold, skipping scouting.kml / scouting.gpx")
        return
    what = {"collect": "scouting", "observe": "viewing", "forage": "foraging"}[sp.mode]
    kml = simplekml.Kml(name=f"{title(sp)} {what} - {sp.binomial}")
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
        heading(sp, reg),
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
        f"* {title(sp)} grows in {reg.name}. The screening says only that it does not grow",
        "  in mapped quantity on the public ground this taxon's mode opens up - the rest is",
        "  private, tribal, or held by an agency that will not permit what you are asking.",
        "  The `other_cells` layer of a completed run is where that ground would show.",
    ]
    lines += caveats(sp, reg)
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
