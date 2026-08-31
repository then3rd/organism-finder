# Tree species x BLM land - transplant permit screening
#
# Every recipe takes a species slug from scripts/species.py; `just species` lists them.
#   just all              # Utah juniper, the default
#   just all pinuedul     # two-needle pinyon

py := ".venv/bin/python"
# PyQGIS lives in system python, not the venv.
qgis_py := "/usr/bin/python3"

default:
    @just --list

# Create the venv and install dependencies.
setup:
    uv venv
    uv pip install --python {{py}} \
      geopandas rasterio exactextract simplekml gpxpy requests pyogrio

# List the species this pipeline knows how to screen for.
species:
    @{{py}} scripts/species.py

# Which LANDFIRE EVT classes a keyword selects - use before adding a species.
evt-classes *keywords:
    @{{py}} scripts/evt_classes.py {{keywords}}

# Sources -> data/raw (cached).
fetch sp="junioste":
    {{py}} scripts/01_fetch.py {{sp}}

# EVT tiles -> data/work/<sp>/class.vrt.
landfire sp="junioste":
    {{py}} scripts/02_landfire.py {{sp}}

# The cross-reference -> out/<sp>/<sp>_blm.gpkg.
overlay sp="junioste":
    {{py}} scripts/03_overlay.py {{sp}}

# csv / md / kml / gpx -> out/<sp>/.
export sp="junioste":
    {{py}} scripts/04_export.py {{sp}}

# The QGIS project -> <sp>_blm.qgs.
qgis sp="junioste":
    {{qgis_py}} scripts/05_qgis_project.py {{sp}}

# Full pipeline, in order.
all sp="junioste": (fetch sp) (landfire sp) (overlay sp) (export sp) (qgis sp)

# Open the map in QGIS.
open sp="junioste": (qgis sp)
    qgis {{sp}}_blm.qgs

# Print the acreage funnel and summary.
summary sp="junioste":
    @cat out/{{sp}}/summary.md

# Delete generated outputs; keeps the data/ download cache.
clean:
    rm -rf out
    rm -f *_blm.qgs *_blm.qgs~ *_blm_attachments.zip
    find scripts -name __pycache__ -type d -exec rm -rf {} +

# Delete outputs *and* the download cache - next run re-downloads everything.
clean-all: clean
    rm -rf data
