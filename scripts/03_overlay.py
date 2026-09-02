"""Cross-reference: public surface x the plant's range x where it actually grows.

Three layers doing three different jobs:
  * SMA polygons     - jurisdiction (who administers this, and what that permits)
  * range            - is this the plant's country at all? Little 1971 for a tree,
                       buffered GBIF records for a plant nobody mapped, nothing when
                       the region is the range
  * cover            - does it grow on *this* ground? LANDFIRE EVT for a woody plant,
                       distance to a record for everything else
  * conditions       - and is the ground itself right? A burn window, a distance to
                       water. Empty for every plant; for a fungus it is half the screen,
                       because its cover class names the host stand rather than the
                       organism. See scripts/habitat.py.

Ownership is a column, not a filter. Every administrator the SMA layer names is carried
through to the GeoPackage so the map can show the whole public estate; what the taxon's
`mode` decides is only which of those owners a *candidate* may sit on. A collect-mode
tree is screened on ground where a plant can lawfully leave, so national parks and
Wilderness come out; an observe-mode orchid is screened on anything a person may stand
on, so they stay in.

Writes out/<slug>/<slug>.gpkg.

    .venv/bin/python scripts/03_overlay.py [taxon-slug]
"""
from pathlib import Path
import datetime
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
import habitat  # noqa: E402
import ownership  # noqa: E402
import paths  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402
from common import CRS_LF  # noqa: E402

M2_PER_ACRE = 4046.8564224
MIN_SPECIES_ACRES = 10      # drop slivers with essentially none of the plant on them
HOTSPOT_KM2 = 1.0           # scouting cell area, km2 (flat-top hexagons)
HOTSPOT_MIN_PCT = 25.0      # a cell must be at least this much cover to be a hotspot
ZONAL_CHUNK = 2000          # features per exact_extract call - see zonal_evt()
OTHER_MIN_ACRES = 25        # same sliver floor as the public grid, applied off it


def acres(gdf):
    return gdf.geometry.area / M2_PER_ACRE


def repair(geoms):
    """Fix self-intersecting rings in place, quietly.

    GEOS refuses set operations on invalid input with a "side location conflict", and a
    handful of the published SMA polygons are invalid. A taxon with a range filter never
    noticed, because intersecting with the range rebuilds the geometry on the way through;
    one with `range_source=None` hands the raw polygons straight to `difference` and falls
    over. Repairing on load is cheaper than reasoning about which paths are safe.
    """
    bad = ~geoms.is_valid
    if bad.any():
        geoms = geoms.copy()
        geoms[bad] = geoms[bad].make_valid()
        print(f"  repaired {int(bad.sum())} invalid geometries")
    return geoms


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


# --- cover: two ways of asking "does it grow here" ----------------------------
# Both return a frame indexed like their input with the same three columns, so every
# caller downstream is method-agnostic:
#   species_pct  0-100, how much of the feature the plant plausibly occupies
#   evidence     one short string naming what said so
#   samples      how much evidence there was, for the reader's own judgement

def zonal_evt(gdf, codes, code_name, vrt, label="zonal"):
    """Per-feature cover fraction and dominant community from the LANDFIRE class grid.

    Run in batches of ZONAL_CHUNK: exact_extract has no progress hook of its own, and a
    single call over fifty thousand cells is a quarter-hour of silence. The batching costs
    one call per 2000 features, which is lost in the raster reads.
    """
    from exactextract import exact_extract

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
            "evidence": pd.Series(dominant).map(code_name).to_numpy(),
            "samples": count,
        },
        index=gdf.index,
    )


def occurrence_buffers(occ, sp):
    """One polygon per record, sized by how well that record was located.

    A record's own stated `coordinateUncertaintyInMeters` is the floor: a sighting placed
    to within 800 m does not become more precise by being buffered to 1000, and one placed
    to within 1800 m should not pretend to the same 1000 m. Records that state nothing get
    the taxon default, which is the assumption stage 01 already declined to make.
    """
    unc = occ["uncertainty_m"].fillna(sp.occurrence_buffer_m)
    radius = np.maximum(unc.to_numpy(), sp.occurrence_buffer_m)
    return gpd.GeoDataFrame(
        {"radius_m": radius},
        geometry=occ.geometry.buffer(radius),
        crs=occ.crs,
    )


def zonal_occurrence(gdf, occ, sp, label="occurrence"):
    """How much of each feature falls within reach of a documented record.

    This is a far weaker claim than the EVT pass and the numbers should be read that way:
    `species_pct` here is "share of this polygon somebody has plausibly found the plant
    in", not modelled cover. Absence of records is absence of *records* - these plants are
    small, briefly visible and unevenly looked for.
    """
    t = Timer(label)
    buf = occurrence_buffers(occ, sp)
    buf_union = buf.union_all()

    # only the features that touch a buffer need the expensive intersection
    inter = np.zeros(len(gdf))
    hit = gdf.geometry.intersects(buf_union).to_numpy()
    if hit.any():
        inter[hit] = gdf.geometry[hit].intersection(buf_union).area.to_numpy()

    # how many records actually sit inside the feature, which is what a reader wants to
    # know: one 1975 herbarium sheet and twelve recent photographs are not the same cell
    reps = gpd.GeoDataFrame(geometry=occ.geometry, crs=occ.crs)
    joined = gpd.sjoin(reps, gdf[["geometry"]], how="inner", predicate="within")
    counts = joined.groupby("index_right").size()
    samples = np.zeros(len(gdf))
    pos = gdf.index.get_indexer(counts.index)
    samples[pos[pos >= 0]] = counts.to_numpy()[pos >= 0]

    area = gdf.geometry.area.to_numpy()
    pct = np.divide(inter, area, out=np.zeros_like(inter), where=area > 0) * 100
    t.done(f"{int(hit.sum()):,} features within reach of a record")
    return pd.DataFrame(
        {
            "species_pct": np.clip(pct, 0, 100),
            "evidence": [f"{int(n)} record{'s' if n != 1 else ''} in feature"
                         if n else "within reach of a nearby record" for n in samples],
            "samples": samples,
        },
        index=gdf.index,
    )


# --- conditions: and is the ground itself right ------------------------------
# The cover pass asks what grows here. These ask what happened here, which for a fungus
# is the half of the question its host class cannot answer. Masks are built once in
# main() and then used two ways: a gate that cuts the ground before the expensive zonal
# pass, and a score that only ranks.


def fire_years(fire, reg):
    """Fire year per perimeter, from the service's discovery timestamp.

    WFIGS publishes epoch milliseconds even through the GeoJSON endpoint, so this is a
    conversion rather than a read. Normalised once on load so everything downstream sees
    a plain year and no other function has to know how this service spells a date.
    """
    raw = fire[reg.fire_date_field]
    return pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms").dt.year


def burn_window(fire, cond, year):
    """Perimeters whose fire year falls inside the condition's window.

    The window is resolved against the calendar at run time rather than baked into the
    download, so a cached perimeter file stays correct into next season instead of
    quietly describing last year's fires.
    """
    lo, hi = year - cond.seasons[1], year - cond.seasons[0]
    return fire[fire["fire_year"].between(lo, hi)].copy()


def water_buffer(reg, cond):
    """Perennial flowlines and waterbodies, buffered and dissolved.

    Cached on disk per region and distance: 65,000 NHD features is minutes of buffering
    and unioning, it is taxon-free, and morcescu and pleuostr ask for exactly the same
    400 m. The same split that makes the raw EVT tile cache worth having.
    """
    cache = paths.water_buffer_gpkg(reg, cond.metres)
    if cache.exists():
        try:
            return gpd.read_file(cache, layer="buffer").to_crs(reg.crs)
        except Exception:
            pass
    t = Timer(f"buffering perennial water to {cond.metres} m")
    src = paths.water_gpkg(reg)
    parts = []
    for layer in ("flowlines", "waterbodies"):
        try:
            g = gpd.read_file(src, layer=layer).to_crs(reg.crs)
        except Exception:
            continue
        if len(g):
            parts.append(g.geometry)
    if not parts:
        raise SystemExit(f"{src} has no water in it - re-run stage 01")
    geoms = pd.concat(parts, ignore_index=True)
    merged = gpd.GeoSeries(geoms, crs=reg.crs).buffer(cond.metres).union_all()
    buf = gpd.GeoDataFrame(geometry=[merged], crs=reg.crs)
    buf.to_file(cache, layer="buffer", driver="GPKG")
    t.done(f"{len(geoms):,} features -> {acres(buf).sum():,.0f} acres")
    return buf


def condition_layers(sp, reg):
    """`{kind: GeoDataFrame}` for every condition kind this taxon uses.

    Read once and shared: morcescu asking for water twice would buffer it twice.
    """
    out = {}
    for kind in habitat.kinds(sp.conditions):
        if kind == habitat.BURN:
            fire = gpd.read_file(paths.fire_gpkg(reg), layer="perimeters").to_crs(reg.crs)
            fire["geometry"] = repair(fire.geometry)
            fire["fire_year"] = fire_years(fire, reg)
            fire["fire_name"] = fire[reg.fire_name_field]
            fire["fire_acres"] = pd.to_numeric(fire[reg.fire_acres_field], errors="coerce")
            out[kind] = fire[["fire_year", "fire_name", "fire_acres", "geometry"]]
        elif kind == habitat.WATER:
            out[kind] = water_buffer(reg, [c for c in sp.conditions
                                           if c.kind == habitat.WATER][0])
    return out


def condition_mask(cond, layers, year, reg):
    """The ground satisfying one condition, as a frame of parts.

    Parts, not one merged geometry, and for the same reason `grid_over` takes frames: the
    water buffer dissolves to a single polygon with millions of vertices, and every
    `intersects` against it is an unindexed test over the whole thing. Kept as parts, the
    spatial index turns the same work from tens of minutes into seconds.
    """
    if cond.kind == habitat.BURN:
        sel = burn_window(layers[habitat.BURN], cond, year)
        if not len(sel):
            # An empty window is the setup being wrong - a window measured from the
            # wrong year, or a region with no fire history - not an answer about the
            # ground. Saying so here beats an empty map three stages later.
            raise SystemExit(
                f"no fire perimeters {cond.label(year)} - check the burn window in "
                "scripts/species.py, or re-run stage 01 to refresh the perimeters"
            )
        parts = sel[["geometry"]]
    else:
        parts = layers[habitat.WATER][["geometry"]]
    parts = parts.explode(index_parts=False).reset_index(drop=True)
    return gpd.GeoDataFrame(parts, geometry="geometry", crs=reg.crs)


def overlap_area(gdf, mask):
    """Area of each feature that falls inside `mask`, indexed like `gdf`.

    sjoin first so only the pairs that actually touch are intersected. Against a mask of
    tens of thousands of parts this is the difference between an indexed lookup and a
    full scan per feature.
    """
    out = pd.Series(0.0, index=gdf.index)
    pairs = gpd.sjoin(gdf[["geometry"]], mask, how="inner", predicate="intersects")
    if not len(pairs):
        return out
    left = gdf.geometry.loc[pairs.index].reset_index(drop=True)
    right = mask.geometry.loc[pairs["index_right"].to_numpy()].reset_index(drop=True)
    area = left.intersection(right, align=False).area
    return out.add(area.groupby(pairs.index.to_numpy()).sum(), fill_value=0).loc[gdf.index]


def clip_to(gdf, mask):
    """`gdf` cut to `mask`, one row per input feature that survives.

    `overlay` would return one row per (feature, mask part) pair - a parcel touching
    forty buffered stream reaches would come back forty times - so the pieces are
    dissolved back onto the feature they came from and the attributes ride along.
    """
    gdf = gdf.copy()
    gdf["_gid"] = np.arange(len(gdf))
    pieces = gpd.overlay(gdf, mask, how="intersection", keep_geom_type=True)
    if not len(pieces):
        return gdf.iloc[:0].drop(columns="_gid")
    merged = pieces.dissolve(by="_gid")
    out = gdf.drop(columns="geometry").set_index("_gid")
    out = out.join(merged[["geometry"]], how="inner")
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=gdf.crs)
    return out.reset_index(drop=True)


def condition_scores(gdf, ctx):
    """How much of each feature satisfies each condition, as `<kind>_pct` columns.

    Reported for gated conditions too, not just scoring ones: a parcel clipped to a burn
    perimeter is 100 % burned by construction, but a *cell* that straddles the edge is
    not, and "just inside the perimeter" and "wholly inside it" are different places to
    spend a morning.
    """
    sp = ctx["sp"]
    out = pd.DataFrame(index=gdf.index)
    if not sp.conditions:
        return out
    area = gdf.geometry.area.to_numpy()
    for cond in sp.conditions:
        inter = overlap_area(gdf, ctx["masks"][cond.kind]).to_numpy()
        pct = np.divide(inter, area, out=np.zeros_like(inter), where=area > 0) * 100
        out[cond.column] = np.clip(pct, 0, 100)
    if habitat.BURN in ctx["masks"]:
        # The year is what a forager actually plans around, so carry it rather than
        # making them read it back out of the percentage.
        out["burn_year"] = burn_years(gdf, ctx)
    return out


def burn_years(gdf, ctx):
    """Most recent fire year overlapping each feature, NaN where none does."""
    sel = ctx["burn_selection"]
    if sel is None or not len(sel):
        return np.full(len(gdf), np.nan)
    reps = gpd.GeoDataFrame(
        geometry=gdf.geometry.representative_point(), crs=gdf.crs, index=gdf.index
    )
    joined = gpd.sjoin(
        reps, sel[["fire_year", "geometry"]], how="left", predicate="within"
    )
    years = pd.to_numeric(joined["fire_year"], errors="coerce")
    return years.groupby(level=0).max().reindex(gdf.index).to_numpy()


def apply_gates(cand, ctx, funnel, year):
    """Cut the ground to the taxon's required conditions, one funnel row each.

    Runs before the cover pass on purpose. A burn window over Utah removes something like
    99 % of the candidate parcels, and the zonal pass is the slow half of this stage; the
    order that reads as "narrow, then measure" is also the order that is fast.
    """
    for cond in habitat.gates(ctx["sp"].conditions):
        cand = clip_to(cand, ctx["masks"][cond.kind])
        funnel.append((f"... {cond.label(year)}", len(cand), acres(cand).sum()))
        check(cand, funnel, f"nothing in range is {cond.label(year)}")
    return cand


def rank_order(sp):
    """A cell's cover fraction barely varies under occurrence scoring - a 1 km2 cell next
    to a record is simply inside the buffer - so record count has to lead, or the ranking
    is a tie broken by area.

    Scoring conditions lead ahead of both. A taxon that merely prefers recent burns wants
    the recent burns at the top; a gated one has already had the question answered
    geometrically and adds nothing here.
    """
    lead = tuple(c.column for c in habitat.scores(sp.conditions))
    if sp.cover == species_mod.OCCURRENCE:
        return lead + ("samples", "species_pct", "species_acres")
    return lead + ("species_pct", "species_acres")


def cover_scores(gdf, ctx, label):
    """Score `gdf` by whichever cover method this taxon uses."""
    sp = ctx["sp"]
    if sp.cover == species_mod.EVT:
        return zonal_evt(gdf, sorted(ctx["code_name"]), ctx["code_name"], ctx["vrt"],
                         label=label)
    return zonal_occurrence(gdf, ctx["occ"], sp, label=label)


def report(funnel):
    print("\nfunnel:")
    for label, n, ac in funnel:
        print(f"  {label:<52} {n:>6}  {ac:>12,.0f} acres")


def write_funnel(sp, funnel):
    pd.DataFrame(funnel, columns=["stage", "features", "acres"]).to_csv(
        paths.out_dir(sp) / "funnel.csv", index=False
    )


def check(cand, funnel, step):
    """A jurisdiction or range filter emptying the frame is a configuration error.

    Reserved for the two geometric filters: if the range misses the region entirely, or
    the exclusions cover all of it, the registry entry or the region is wrong. The third
    narrowing - no parcel carrying enough mapped cover - is a screening *result* and is
    handled inline in main(), not here.
    """
    if len(cand):
        return
    report(funnel)
    raise SystemExit(
        f"nothing survived: {step}. The funnel above shows where it emptied - check the "
        "taxon's EVT keywords (`just evt-classes`) or GBIF key, and that its range "
        "actually overlaps this region."
    )


DERIVED = ("land_id", "owner", "owner_name", "tenure", "public", "collect")


def load_land(raw, reg):
    """Every SMA polygon, with its administrator resolved against the ownership registry."""
    land = gpd.read_file(raw / "sma.gpkg", layer="sma").to_crs(reg.crs)
    codes = land[reg.owner_field].fillna("?")
    # GeoPackage field names are case-insensitive, and Utah's SMA layer already publishes
    # an OWNER column (Federal/State/Tribal/Private) that `tenure` supersedes. Writing
    # both fails at export with a bare "error adding field", so drop the source column
    # rather than discover the clash three stages downstream.
    clash = [c for c in land.columns if c.lower() in DERIVED]
    if clash:
        land = land.drop(columns=clash)
    land["geometry"] = repair(land.geometry)
    land["land_id"] = np.arange(1, len(land) + 1)
    land["owner"] = codes.to_numpy()
    owners = land["owner"].map(ownership.owner)
    land["owner_name"] = [o.name for o in owners]
    land["tenure"] = [o.tenure for o in owners]
    land["public"] = [o.public for o in owners]
    land["collect"] = [o.collect for o in owners]
    return land


def load_range(sp, reg):
    """The range polygon this taxon is filtered by, or None when it has no range filter."""
    gpkg = paths.range_gpkg(sp)
    if sp.range_source == species_mod.LITTLE:
        return gpd.read_file(gpkg, layer="range").to_crs(reg.crs)
    if sp.range_source == species_mod.GBIF:
        occ = gpd.read_file(gpkg, layer="occurrences").to_crs(reg.crs)
        # the "range" of a plant screened from records is the reach of those records; it
        # is dissolved so the funnel counts patches of country, not sightings
        merged = occurrence_buffers(occ, sp).union_all()
        parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        return gpd.GeoDataFrame(geometry=parts, crs=reg.crs)
    return None


def main():
    started = time.time()
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    raw = paths.raw_dir(reg)
    vrt = paths.vrt_path(sp)

    code_name = {0: "none"}
    occ = None
    if sp.cover == species_mod.EVT:
        codes = pd.read_csv(paths.codes_path(sp))
        code_name.update(dict(zip(codes["code"], codes["evt_name"])))
    else:
        occ = gpd.read_file(paths.range_gpkg(sp), layer="occurrences").to_crs(reg.crs)
    ctx = {"sp": sp, "code_name": code_name, "vrt": vrt, "occ": occ,
           "masks": {}, "burn_selection": None}

    year = datetime.date.today().year
    if sp.conditions:
        t = Timer("reading habitat conditions")
        layers = condition_layers(sp, reg)
        for cond in sp.conditions:
            ctx["masks"][cond.kind] = condition_mask(cond, layers, year, reg)
            if cond.kind == habitat.BURN:
                ctx["burn_selection"] = burn_window(layers[habitat.BURN], cond, year)
        t.done(", ".join(c.label(year) for c in sp.conditions))

    screenable = set(ownership.screenable(sp.mode))
    print(f"== {sp.common_name} ({sp.binomial}) on {reg.name} public land ==")
    print(f"   mode={sp.mode}  range={sp.range_source or 'none'}  cover={sp.cover}")
    if sp.conditions:
        print("   conditions: " + ", ".join(
            f"{'must be' if c.required else 'prefers'} {c.label(year)}"
            for c in sp.conditions))

    # --- jurisdiction ---------------------------------------------------------
    t = Timer("reading the jurisdiction layers")
    land = load_land(raw, reg)
    counties = gpd.read_file(raw / "counties.gpkg", layer="counties").to_crs(reg.crs)
    counties = counties[["NAME", "geometry"]].rename(columns={"NAME": "county"})
    public = land[land["public"]].copy()
    # Captured before any narrowing: the context grid is the complement of this layer's
    # *universe*, not of the parcels that survived eligibility. A BLM parcel that failed
    # the cover test is still ground with a BLM permit behind it, so it does not belong
    # in a grid whose whole meaning is "nobody here can help you".
    screened_land = land[land["owner"].isin(screenable)].copy()
    cand = screened_land.copy()
    t.done(f"{len(land):,} SMA polygons, {len(public):,} public, {len(counties)} counties")
    print("  screening on: " + ", ".join(
        f"{o.short}" for _, o in ownership.summarize(sorted(set(cand['owner'])))))

    funnel = [
        (f"{reg.name} surface management polygons", len(land), acres(land).sum()),
        ("... on public land", len(public), acres(public).sum()),
        (f"... open to an {sp.mode}-mode screen" if sp.mode[0] in "aeiou"
         else f"... open to a {sp.mode}-mode screen", len(cand), acres(cand).sum()),
    ]
    check(cand, funnel, "no public owner is open to this taxon's mode")

    # --- range filter ---------------------------------------------------------
    rng = load_range(sp, reg)
    if rng is None:
        print(f"no range filter for {sp.binomial} - the region is the range")
    else:
        src = ("Little's" if sp.range_source == species_mod.LITTLE
               else "the documented range of")
        t = Timer(f"intersecting public surface with {src} {sp.binomial} range")
        rng_union = rng.union_all()
        cand = cand[cand.intersects(rng_union)].copy()
        # Clipping to the range is right when the range and the cover come from different
        # data - Little's polygon knows nothing about LANDFIRE. It is circular when they
        # come from the same data: an occurrence-screened taxon's range *is* the union of
        # its cover buffers, so clipping to it would make every parcel 100 % covered by
        # construction and the ramps would carry no information at all. So select, and
        # only clip when there is a second opinion to clip against.
        if sp.cover != species_mod.OCCURRENCE:
            cand["geometry"] = cand.geometry.intersection(rng_union)
            cand = cand[~cand.geometry.is_empty]
        t.done(f"{len(cand):,} parcels")
        funnel.append((f"... inside the {sp.binomial} range", len(cand),
                       acres(cand).sum()))
        check(cand, funnel, "the range does not overlap public surface here")

    # --- special designations -------------------------------------------------
    # A bar in collect mode and a flag in observe mode. Wilderness is closed to digging
    # and open to walking, and an orchid map is about walking, so subtracting it would
    # remove the best ground on the map for the wrong reason.
    t = Timer("reading Wilderness / WSA / NM-NCA")
    excl_parts = []
    for layer in ("wilderness", "wsa", "nm_nca"):
        g = gpd.read_file(raw / "nlcs.gpkg", layer=layer).to_crs(reg.crs)
        if len(g):
            g = g[["geometry"]].copy()
            g["excl_type"] = layer
            excl_parts.append(g)
    desig_excl = land[land["DESIG"].isin(reg.excluded_desig)][["geometry"]].copy()
    desig_excl["excl_type"] = "sma_designation"
    excl_parts.append(desig_excl)
    exclusions = gpd.GeoDataFrame(pd.concat(excl_parts, ignore_index=True), crs=reg.crs)
    exclusions["geometry"] = repair(exclusions.geometry)
    excl_union = exclusions.union_all()
    t.done(f"{len(exclusions):,} polygons")

    if sp.mode == species_mod.COLLECT:
        t = Timer("subtracting Wilderness / WSA / NM-NCA")
        cand["geometry"] = cand.geometry.difference(excl_union)
        cand = cand[~cand.geometry.is_empty].copy()
        cand = cand.explode(index_parts=False).reset_index(drop=True)
        cand = cand[cand.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        cand = cand[acres(cand) >= 1]      # discard difference slivers
        cand["in_excluded"] = False
        t.done(f"{len(cand):,} parcels")
        funnel.append(("... minus Wilderness / WSA / NM-NCA", len(cand),
                       acres(cand).sum()))
        check(cand, funnel, "the legal exclusions cover everything in range")
    else:
        # observe and forage both keep them. Walking into a Wilderness to look at an
        # orchid is what a Wilderness is for, and picking mushrooms for the pot is
        # lawful there too - what is closed to a forager is closed by owner (NPS,
        # refuges) rather than by designation, and `screenable` has already cut that.
        cand = cand.explode(index_parts=False).reset_index(drop=True)
        cand["in_excluded"] = cand.intersects(excl_union)
        print(f"  {sp.mode} mode: keeping {int(cand['in_excluded'].sum()):,} parcels in "
              "Wilderness / WSA / monument rather than subtracting them")
        # Not a narrowing step, but the explode above splits multipart parcels, so
        # without a row here the feature count jumps with nothing to explain it.
        funnel.append(("... Wilderness / WSA / NM-NCA kept and flagged, parts split",
                       len(cand), acres(cand).sum()))

    # lands with wilderness characteristics: a flag, not a bar, in either mode
    t = Timer("flagging lands with wilderness characteristics")
    lwc = gpd.read_file(raw / "nlcs.gpkg", layer="lwc").to_crs(reg.crs)
    lwc_union = lwc.union_all()
    cand["in_lwc"] = cand.intersects(lwc_union)
    t.done(f"{int(cand['in_lwc'].sum()):,} flagged")

    # --- and is the ground itself right ---------------------------------------
    # Before the cover pass, not after: this is the cheap filter and the zonal pass is
    # the expensive one, so gating first is both the right reading order and the fast
    # one. A plant carries no conditions and this is a no-op.
    if habitat.gates(sp.conditions):
        t = Timer("applying habitat conditions")
        cand = apply_gates(cand, ctx, funnel, year)
        t.done(f"{len(cand):,} parcels")

    # --- does it actually grow here -------------------------------------------
    cand = cand.reset_index(drop=True)
    stats = cover_scores(cand, ctx, f"scoring {len(cand):,} parcels for {sp.short}")
    cond_stats = condition_scores(cand, ctx)
    cand = pd.concat([cand.reset_index(drop=True), stats.reset_index(drop=True),
                      cond_stats.reset_index(drop=True)], axis=1)
    cand = gpd.GeoDataFrame(cand, geometry="geometry", crs=reg.crs)
    cand["land_acres"] = acres(cand)
    cand["species_acres"] = cand["land_acres"] * cand["species_pct"] / 100
    cand = cand[cand["species_acres"] >= MIN_SPECIES_ACRES].copy()
    funnel.append(
        (f"... with mapped {sp.short} (>={MIN_SPECIES_ACRES} ac)",
         len(cand), cand["species_acres"].sum())
    )
    # Not check(): the filters all ran, and no public parcel carrying this plant is an
    # answer about the ground rather than a fault in the setup. Stages 04 and 05 read the
    # missing GeoPackage as that same answer, so `just all` stays green.
    if not len(cand):
        write_funnel(sp, funnel)
        report(funnel)
        print(f"\nnothing qualifies: no {reg.name} public parcel carries "
              f"{MIN_SPECIES_ACRES} acres of mapped {sp.short}. The range overlaps "
              f"{reg.name} and the cover data exists, so this is a screening result, not "
              "a misconfiguration.")
        print(f"\nstage 03 took {fmt(time.time() - started)}")
        return

    # --- who administers it ---------------------------------------------------
    t = Timer("attaching the administering unit")
    admu = gpd.read_file(raw / "admu.gpkg", layer="boundary").to_crs(reg.crs)
    admu = admu[["ADMU_NAME", "ADM_UNIT_CD", "ADMU_ST_URL", "geometry"]]
    cand = attach(cand, admu, ["ADMU_NAME", "ADMU_ST_URL"])
    cand = attach(cand, counties, ["county"])

    # Only BLM publishes a field-office layer, so only BLM parcels get a named unit. For
    # everyone else the honest answer is the agency plus what the SMA layer calls the
    # designation - "U.S. Forest Service / National Forest" - rather than a blank or, far
    # worse, the BLM field office whose polygon happens to overlap it.
    is_blm = cand["owner"].eq("BLM")
    cand["managing_unit"] = np.where(
        is_blm & cand["ADMU_NAME"].notna(),
        cand["ADMU_NAME"],
        cand["owner_name"] + np.where(
            cand["DESIG"].notna() & cand["DESIG"].ne("N/A"), " / " + cand["DESIG"], ""),
    )
    cand["unit_url"] = cand["ADMU_ST_URL"].where(is_blm)
    cand = cand.drop(columns=["ADMU_NAME", "ADMU_ST_URL"])
    t.done(f"{cand['managing_unit'].nunique()} distinct units")

    cand = cand.sort_values("species_acres", ascending=False).reset_index(drop=True)
    cand["rank"] = np.arange(1, len(cand) + 1)
    # Built rather than written out, so a taxon with no conditions carries no empty
    # columns and one with two carries both.
    cond_cols = [c for c in sp.condition_columns + ["burn_year"] if c in cand.columns]
    cand = cand[[
        "rank", "land_id", "owner", "owner_name", "tenure", "collect", "managing_unit",
        "unit_url", "county", "DESIG", "in_lwc", "in_excluded",
        "land_acres", "species_acres", "species_pct", "evidence", "samples",
    ] + cond_cols + ["geometry"]]

    # --- scouting grid --------------------------------------------------------
    print("\n-- %g km2 hex scouting grid on screened public surface --" % HOTSPOT_KM2,
          flush=True)
    hotspots = build_hotspots(cand, ctx, reg)

    # The same grid over everything that is *not* screened public surface - private,
    # tribal, closed withdrawals, and any public owner this taxon's mode rules out. These
    # cells carry no jurisdiction anyone can act on, so they are context for reading the
    # map, never a target. Built from the same range x cover screen so the two grids are
    # directly comparable.
    print("\n-- the same grid off screened surface (context only) --", flush=True)
    other = build_other(screened_land, rng, counties, ctx, reg)

    gpkg = paths.gpkg_path(sp)
    t = Timer(f"writing {gpkg.relative_to(paths.ROOT)}")
    cand.to_file(gpkg, layer="candidates", driver="GPKG")
    hotspots.to_file(gpkg, layer="hotspots", driver="GPKG")
    other.to_file(gpkg, layer="other_cells", driver="GPKG")
    public.to_file(gpkg, layer="public_land", driver="GPKG")
    land.to_file(gpkg, layer="land_all", driver="GPKG")
    if rng is not None:
        rng.to_file(gpkg, layer="species_range", driver="GPKG")
    if occ is not None:
        occ.to_file(gpkg, layer="occurrences", driver="GPKG")
    if ctx["burn_selection"] is not None and len(ctx["burn_selection"]):
        ctx["burn_selection"].to_file(gpkg, layer="burns", driver="GPKG")
    if habitat.WATER in ctx["masks"]:
        ctx["masks"][habitat.WATER].to_file(gpkg, layer="water_buffer", driver="GPKG")
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
    # below the rule: not a narrowing step, and not ground this screen can act on
    funnel.append((
        "hex cells off screened surface (context only)",
        len(other), other["species_acres"].sum(),
    ))
    write_funnel(sp, funnel)
    report(funnel)
    print(f"\nstage 03 took {fmt(time.time() - started)}")


def grid_over(domain, ctx, reg, min_pct, subtract=None, min_acres=25):
    """Hex-grid `domain`, score every cell for cover, keep the ones over `min_pct`.

    Shared by the scouting grid and the context grid so the two are scored by exactly the
    same pass and can be compared cell for cell. `domain` and `subtract` are frames, not
    merged geometries, and the clipping goes through `overlay` both times: an
    `intersection` against one unioned polygon is a quarter of a million unindexed
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

    if subtract is not None and len(subtract):
        t = Timer(f"punching out {len(subtract):,} screened parcels")
        grid = gpd.overlay(grid, subtract[["geometry"]], how="difference",
                           keep_geom_type=True)
        t.done(f"{len(grid):,} cells left")
    grid = grid[~grid.geometry.is_empty]
    grid = grid[acres(grid) >= min_acres].reset_index(drop=True)   # ignore edge slivers
    if not len(grid):
        return grid

    stats = cover_scores(grid, ctx, f"scoring {len(grid):,} cells")
    # Cells get the condition columns as well as the cover ones. A cell is a kilometre
    # across and a burn edge runs through it, so "how much of this cell burned" is real
    # information even where the parcel it sits on was clipped to the perimeter.
    cond_stats = condition_scores(grid, ctx)
    grid = gpd.GeoDataFrame(
        pd.concat([grid.reset_index(drop=True), stats.reset_index(drop=True),
                   cond_stats.reset_index(drop=True)], axis=1),
        geometry="geometry", crs=reg.crs,
    )
    grid["cell_acres"] = acres(grid)
    grid["species_acres"] = grid["cell_acres"] * grid["species_pct"] / 100
    grid = grid[grid["species_pct"] >= min_pct].copy()
    return grid.reset_index(drop=True)


def rank_and_locate(grid, by=("species_pct", "species_acres")):
    """Rank by cover and stamp lat/lon on the representative point - both grids need it."""
    grid = grid.sort_values(list(by), ascending=False)
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


HOTSPOT_COLS = [
    "rank", "owner", "owner_name", "managing_unit", "county", "cell_acres",
    "species_acres", "species_pct", "evidence", "samples", "lat", "lon", "geometry",
]


def with_conditions(cols, sp, grid):
    """`cols` plus whichever condition columns this taxon produced, before the geometry.

    The two column lists are constants because they are the layer contract; conditions
    are per taxon, so they are spliced in rather than written into either list.
    """
    extra = [c for c in sp.condition_columns + ["burn_year"]
             if c in grid.columns and c not in cols]
    at = cols.index("geometry")
    return cols[:at] + extra + cols[at:]


def empty_cells(reg, cols):
    return gpd.GeoDataFrame(
        {c: [] for c in cols if c != "geometry"},
        geometry=gpd.GeoSeries([], crs=reg.crs), crs=reg.crs,
    )


def build_hotspots(cand, ctx, reg):
    """Grid the eligible land so there are concrete places to go, not million-acre blocks."""
    grid = grid_over(cand, ctx, reg, HOTSPOT_MIN_PCT)
    if not len(grid):
        return empty_cells(reg, HOTSPOT_COLS)
    grid = attach(grid, cand, ["owner", "owner_name", "managing_unit", "county"])
    grid = rank_and_locate(grid, rank_order(ctx["sp"]))
    return grid[with_conditions(HOTSPOT_COLS, ctx["sp"], grid)]


def build_other(screened, rng, counties, ctx, reg):
    """The same grid over the ground this screen cannot act on.

    Domain is the region (narrowed by the range and by any required condition, when
    there are any) minus every screened parcel. No managing unit is attached because
    there is nobody to ask: these cells say where the plant is, not where you may go and
    get it.

    The gates have to be applied here as well as to the candidates. The context grid
    means "the same ground, off screenable surface"; without them it would silently widen
    to the whole host stand and stop being a comparison at all.
    """
    domain = counties[["geometry"]]
    if rng is not None:
        domain = gpd.overlay(domain, rng[["geometry"]], how="intersection",
                             keep_geom_type=True)
    for cond in habitat.gates(ctx["sp"].conditions):
        domain = gpd.overlay(domain, ctx["masks"][cond.kind], how="intersection",
                             keep_geom_type=True)
    if domain.empty:
        print("  the range does not reach this region")
        return empty_cells(reg, OTHER_COLS)
    grid = grid_over(domain, ctx, reg, HOTSPOT_MIN_PCT,
                     subtract=screened, min_acres=OTHER_MIN_ACRES)
    if not len(grid):
        return empty_cells(reg, OTHER_COLS)
    grid = attach(grid, counties, ["county"])
    grid = rank_and_locate(grid, rank_order(ctx["sp"]))
    return grid[with_conditions(OTHER_COLS, ctx["sp"], grid)]


OTHER_COLS = [
    "rank", "county", "cell_acres", "species_acres", "species_pct", "evidence",
    "samples", "lat", "lon", "geometry",
]


if __name__ == "__main__":
    main()
