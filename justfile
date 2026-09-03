# Plants and fungi x Utah public land - where it grows, and who administers the ground
#
# Every recipe takes a species slug from scripts/species.py; `just species` lists them.
#   just all              # Utah juniper, the default
#   just all pinuedul     # two-needle pinyon
#   just all junioste square  # the same screen, drawn on a square lattice

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
# every registered taxon, with how each one is screened
species:
    @{{py}} scripts/species.py

# who administers ground in this region, and what may be taken off it
owners:
    @{{py}} scripts/ownership.py

# what a habitat condition is, and how a gate differs from a score
conditions:
    @{{py}} scripts/habitat.py

# the cell shapes the scouting grid can be cut from
grids:
    @{{py}} scripts/grid.py

# Which LANDFIRE EVT classes a keyword selects - use before adding a species.
evt-classes *keywords:
    @{{py}} scripts/evt_classes.py {{keywords}}

# Sources -> data/raw (cached).
fetch sp="junioste":
    {{py}} scripts/01_fetch.py {{sp}}

# EVT tiles -> data/work/<sp>/class.vrt.
landfire sp="junioste":
    {{py}} scripts/02_landfire.py {{sp}}

# The cross-reference -> out/<sp>/<sp>.gpkg. `grid` is hex or square; `just grids` lists them.
overlay sp="junioste" grid="hex":
    {{py}} scripts/03_overlay.py {{sp}} {{grid}}

# csv / md / kml / gpx -> out/<sp>/.
export sp="junioste":
    {{py}} scripts/04_export.py {{sp}}

# The QGIS project -> <sp>.qgs, and the portable copy -> out/<sp>/<sp>_qfield.qgz.
qgis sp="junioste":
    {{qgis_py}} scripts/05_qgis_project.py {{sp}}

# What to copy to the phone. Everything QField needs is in the one folder.
qfield sp="junioste": (qgis sp)
    @du -sh out/{{sp}}
    @echo "copy out/{{sp}}/ to the phone, open {{sp}}_qfield.qgz in QField"
    @echo "notes come back in out/{{sp}}/field_notes.gpkg - clean will not touch it"

# Full pipeline, in order.
all sp="junioste" grid="hex": (fetch sp) (landfire sp) (overlay sp grid) (export sp) (qgis sp)

# Every registered species, in registry order. Hours, not minutes.
all-species:
    #!/usr/bin/env bash
    # Stage 03 is the long pole and runs once per tree. The EVT download is cached
    # region-wide, so only the first species ever pays for it.
    set -euo pipefail
    for sp in $({{py}} -c 'import sys; sys.path.insert(0, "scripts"); import species; print(" ".join(species.TAXA))'); do
      echo "=========== $sp ==========="
      just all "$sp"
    done

# Every species' map, from the GeoPackages stage 03 already wrote - for styling changes.
qgis-all:
    #!/usr/bin/env bash
    set -euo pipefail
    for sp in $({{py}} -c 'import sys; sys.path.insert(0, "scripts"); import species; print(" ".join(species.TAXA))'); do
      just qgis "$sp"
    done

# Open the desktop map in QGIS.
open sp="junioste": (qgis sp)
    qgis {{sp}}.qgs

# Print the acreage funnel and summary.
summary sp="junioste":
    @cat out/{{sp}}/summary.md

# Delete generated outputs; keeps the data/ download cache and the field notes.
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    # field_notes.gpkg and the photos beside it are the only things under out/ that a
    # person made rather than this pipeline, and nothing can rebuild them. Dropping them
    # is `just clean-notes <sp>`, deliberately and one taxon at a time.
    if [ -d out ]; then
      find out -mindepth 2 -depth \
        ! -name field_notes.gpkg ! -name DCIM ! -path 'out/*/DCIM/*' -delete
      find out -mindepth 1 -type d -empty -delete
    fi
    rm -f *.qgs *.qgs~ *_attachments.zip symbology-style.db
    find scripts -name __pycache__ -type d -exec rm -rf {} +

# Drop one taxon's field notes and photos. Nothing else deletes them.
clean-notes sp="junioste":
    rm -rf out/{{sp}}/field_notes.gpkg out/{{sp}}/DCIM

# Delete outputs *and* the download cache - re-downloads everything; notes survive.
clean-all: clean
    rm -rf data
