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


def evt_csv():
    """The LANDFIRE EVT attribute table. One file for the whole country."""
    return _mk(RAW / "evt") / "LF23_EVT_240.csv"


# --- intermediate ------------------------------------------------------------
def evt_tile_dir(region):
    """Raw S16 EVT tiles, cached before the species remap so a second species is free."""
    return _mk(WORK / "evt" / region.key)


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


def gpkg_path(species):
    return out_dir(species) / f"{species.slug}_blm.gpkg"


def qgs_path(species):
    return ROOT / f"{species.slug}_blm.qgs"
