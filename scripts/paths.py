"""Every path the pipeline reads or writes.

Standard library only: stage 05 imports this under the system interpreter, where the
venv's packages do not exist. Directories are created on demand rather than at import,
so importing this module from a read-only context is harmless.
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


def evt_csv():
    """The LANDFIRE EVT attribute table. One file for the whole country."""
    return _mk(RAW / "evt") / "LF23_EVT_240.csv"


# --- intermediate ------------------------------------------------------------
def evt_tile_dir(region):
    """Raw S16 EVT tiles, cached before the species remap so a second species is free."""
    return _mk(WORK / "evt" / region.key)


def water_buffer_gpkg(region, metres):
    """The dissolved water buffer. Region-scoped and taxon-free, like the raw EVT tiles:
    buffering 65,000 NHD features and unioning them is minutes of work, and every taxon
    asking for the same distance should pay for it once."""
    return _mk(WORK / "water" / region.key) / f"buffer_{int(metres)}m.gpkg"


def work_dir(species):
    return _mk(WORK / species.slug)


def range_gpkg(species):
    return work_dir(species) / "range.gpkg"


def vrt_path(species):
    return work_dir(species) / "class.vrt"


def codes_path(species):
    return work_dir(species) / "evt_codes.csv"


# --- deliverables ------------------------------------------------------------
def out_dir(species):
    """out/<slug>/ - becomes out/<region>/<slug>/ when a second region lands, and no
    caller has to change because nothing else builds this path."""
    return _mk(OUT / species.slug)


# The `_blm` suffix these two carried was accurate when BLM was the only ground screened
# and is a lie now that every public owner is in the file.
def gpkg_path(species):
    return out_dir(species) / f"{species.slug}.gpkg"


def qgs_path(species):
    return ROOT / f"{species.slug}.qgs"


def grid_marker(species):
    """Which lattice stage 03 laid: out/<slug>/grid.txt.

    Stages 04 and 05 read the GeoPackage, which does not say what shape its cells are,
    and re-running them must not have to repeat the argument stage 03 was given.
    """
    return out_dir(species) / "grid.txt"


def about_gpkg(species):
    """out/<slug>/about.gpkg - the one-feature fact sheet stage 05 regenerates each run.

    Beside <slug>.gpkg rather than inside it: stage 03 owns that file, and a later stage
    writing into an earlier stage's artifact inverts the one-way ordering everything else
    here depends on.
    """
    return out_dir(species) / "about.gpkg"


def notes_gpkg(species):
    """out/<slug>/field_notes.gpkg - what the person carrying the phone wrote down.

    The only file under out/ that this pipeline will not recreate, which is why it is a
    file of its own: stage 03 rewrites <slug>.gpkg wholesale, so a notes layer inside it
    would be destroyed by the next overlay run. Stage 05 creates this one only when it is
    missing and never opens it for writing again.
    """
    return out_dir(species) / "field_notes.gpkg"


def qfield_path(species):
    """out/<slug>/<slug>_qfield.qgz - the portable project, beside the data it names.

    Relative paths only work if the project ships with its data, so this one lives in the
    directory you copy to the phone rather than at the repo root with the desktop map.
    """
    return out_dir(species) / f"{species.slug}_qfield.qgz"
