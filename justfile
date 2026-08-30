# Utah juniper x BLM land - transplant permit screening

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

# Sources -> data/raw (cached).
fetch:
    {{py}} scripts/01_fetch.py

# EVT tiles -> data/work/juniper_class.vrt.
landfire:
    {{py}} scripts/02_landfire.py

# The cross-reference -> out/juniper_blm.gpkg.
overlay:
    {{py}} scripts/03_overlay.py

# csv / md / kml / gpx -> out/.
export:
    {{py}} scripts/04_export.py

# The QGIS project -> juniper_blm.qgs.
qgis:
    {{qgis_py}} scripts/05_qgis_project.py

# Full pipeline, in order.
all: fetch landfire overlay export qgis

# Open the map in QGIS.
open: qgis
    qgis juniper_blm.qgs

# Print the acreage funnel and summary.
summary:
    @cat out/summary.md

# Delete generated outputs; keeps the data/ download cache.
clean:
    rm -rf out juniper_blm.qgs juniper_blm.qgs~ juniper_blm_attachments.zip
    find scripts -name __pycache__ -type d -exec rm -rf {} +

# Delete outputs *and* the download cache - next run re-downloads everything.
clean-all: clean
    rm -rf data
