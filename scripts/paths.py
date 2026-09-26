"""Every path the pipeline reads or writes.

Standard library only: stage 05 imports this under the system interpreter, where the
venv's packages do not exist. Directories are created on demand rather than at import,
so importing this module from a read-only context is harmless.

Region scoping is not cosmetic. Anything keyed by a tile index, a county extent or a
state-scoped query has to carry the region in its path, because the *same* index means
different ground in a different state and these caches all return early when the file
exists. The bug that rule prevents is silent: an Idaho run reusing Utah's remapped
rasters draws a perfectly good map of the wrong state.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
WORK = DATA / "work"
OUT = ROOT / "out"


def _mk(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- downloads ---------------------------------------------------------------
def raw_dir(region):
    """Jurisdiction layers, which are per-state: data/raw/<region>/"""
    return _mk(RAW / region.key)


def species_raw(species):
    """Little's range polygons. CONUS-wide, so region-independent."""
    return _mk(RAW / "species") / f"{species.slug}.geojson"


def occurrence_raw(species, region):
    """GBIF occurrence records. Region-scoped, because the query is."""
    return _mk(RAW / "occurrence") / f"{species.slug}_{region.key}.geojson"


def fire_gpkg(region):
    """Fire perimeter history, clipped to the region envelope at fetch time."""
    return raw_dir(region) / "fire.gpkg"


def water_gpkg(region):
    """NHD perennial flowlines and waterbodies, ditto."""
    return raw_dir(region) / "water.gpkg"


def campsites_gpkg(region):
    """Campsite points from OpenStreetMap, USFS and BLM, raw and merged. Region-scoped and
    taxon-free: which sites exist does not depend on what the map is for."""
    return raw_dir(region) / "campsites.gpkg"


def roads_gpkg(region):
    """OpenStreetMap roads and tracks, classed paved / graded / rough. Camp mode only."""
    return raw_dir(region) / "roads.gpkg"


def forests_gpkg(region):
    """National forest boundaries, for naming the unit a Forest Service cell is in."""
    return raw_dir(region) / "usfs_forests.gpkg"


def evt_csv():
    """The LANDFIRE EVT attribute table. One file for the whole country."""
    return _mk(RAW / "evt") / "LF23_EVT_240.csv"


# --- intermediate ------------------------------------------------------------
def evt_tile_dir(region):
    """Raw S16 EVT tiles, cached before the species remap so a second species is free.

    Deliberately *not* under `work_dir`: these are taxon-free, so the first taxon in a
    region pays the download and every later one is local work. That split is the point
    of stage 02, not an optimisation.
    """
    return _mk(WORK / "evt" / region.key)


def evt_vrt(region):
    """A VRT over the raw EVT tiles, for reading real class values rather than a
    taxon's remap. Taxon-free, so it sits with the tiles it points at."""
    return evt_tile_dir(region) / "evt.vrt"


def slope_tile_dir(region):
    """Raw 3DEP slope-in-degrees tiles, before the flat/not-flat threshold. Taxon-free
    for the same reason the raw EVT tiles are: a second slope threshold is local work."""
    return _mk(WORK / "slope" / region.key)


def water_buffer_gpkg(region, metres):
    """The dissolved water buffer. Region-scoped and taxon-free, like the raw EVT tiles:
    buffering 65,000 NHD features and unioning them is minutes of work, and every taxon
    asking for the same distance should pay for it once."""
    return _mk(WORK / "water" / region.key) / f"buffer_{int(metres)}m.gpkg"


def work_dir(species, region):
    """data/work/<region>/<slug>/ - the remapped class tiles, the VRT, the range.

    Region-scoped because everything under it is. The class tiles are keyed by a tile
    index over *this region's* county extent, and `class_tile()` returns early when the
    file exists, so a shared directory would hand Idaho Utah's rasters without a word.
    """
    return _mk(WORK / region.key / species.slug)


def range_gpkg(species, region):
    return work_dir(species, region) / "range.gpkg"


def vrt_path(species, region):
    return work_dir(species, region) / "class.vrt"


def codes_path(species, region):
    return work_dir(species, region) / "evt_codes.csv"


# --- deliverables ------------------------------------------------------------
def out_dir(species, region):
    """out/<region>/<slug>/ - everything a person is handed."""
    return _mk(OUT / region.key / species.slug)


# The `_blm` suffix these two carried was accurate when BLM was the only ground screened
# and is a lie now that every public owner is in the file.
def gpkg_path(species, region):
    return out_dir(species, region) / f"{species.slug}.gpkg"


def qgs_path(species, region):
    """out/<region>/<slug>/<slug>.qgs - the desktop project, beside its data.

    It keeps absolute datasources, so unlike the portable project it would work from
    anywhere; it lives here anyway because QGIS writes its litter next to the project
    file - the `.qgs~` backup and the `<name>_attachments.zip` the attachment widget
    creates - and at the repo root that was 26 taxa's worth of debris in the working
    tree. No region suffix: the directory already carries the region, which is why
    `<slug>_qfield.qgz` beside it never needed one either.
    """
    return out_dir(species, region) / f"{species.slug}.qgs"


def grid_marker(species, region):
    """Which lattice stage 03 laid: out/<region>/<slug>/grid.txt.

    Stages 04 and 05 read the GeoPackage, which does not say what shape its cells are,
    and re-running them must not have to repeat the argument stage 03 was given.
    """
    return out_dir(species, region) / "grid.txt"


def about_gpkg(species, region):
    """out/<region>/<slug>/about.gpkg - the fact sheet stage 05 regenerates each run.

    Beside <slug>.gpkg rather than inside it: stage 03 owns that file, and a later stage
    writing into an earlier stage's artifact inverts the one-way ordering everything else
    here depends on.
    """
    return out_dir(species, region) / "about.gpkg"


def notes_gpkg(species, region):
    """out/<region>/<slug>/field_notes.gpkg - what the person carrying the phone wrote.

    The only file under out/ that this pipeline will not recreate, which is why it is a
    file of its own: stage 03 rewrites <slug>.gpkg wholesale, so a notes layer inside it
    would be destroyed by the next overlay run. Stage 05 creates this one only when it is
    missing and never opens it for writing again.
    """
    return out_dir(species, region) / "field_notes.gpkg"


def qfield_path(species, region):
    """out/<region>/<slug>/<slug>_qfield.qgz - the portable project, beside its data.

    Relative paths only work if the project ships with its data, so this one lives in the
    directory you copy to the phone rather than at the repo root with the desktop map.
    """
    return out_dir(species, region) / f"{species.slug}_qfield.qgz"


if __name__ == "__main__":
    # So the justfile can ask for a path instead of spelling one. CLAUDE.md's rule is
    # that nothing else builds a path by concatenation, and `just summary` was quietly
    # breaking it; every deliverable path stays spelled here and nowhere else.
    import sys
    import region as region_mod
    import species as species_mod

    _WHAT = {
        "dir": out_dir,
        "gpkg": gpkg_path,
        "qgs": qgs_path,
        "qfield": qfield_path,
        "notes": notes_gpkg,
        "summary": lambda sp, reg: out_dir(sp, reg) / "summary.md",
        "work": work_dir,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in _WHAT:
        raise SystemExit(f"usage: paths.py <{'|'.join(_WHAT)}> [slug] [region]")
    _sp = species_mod.resolve(sys.argv[1:])
    _reg = region_mod.resolve(sys.argv[1:])
    print(_WHAT[sys.argv[1]](_sp, _reg))
