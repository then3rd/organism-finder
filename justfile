# Plants and fungi x public land - where it grows, and who administers the ground.
# Plus one entry that is not an organism: where you may camp.
#
# Every recipe takes a species slug from scripts/species.py; `just species` lists them,
# and a trailing region key; `just owners <key>` lists those. Region defaults to `ut`.
#   just all                     # Utah juniper, in Utah
#   just all pinuedul            # two-needle pinyon
#   just all junioste square     # the same screen, drawn on a square lattice
#   just all junioste hex id     # the same taxon, screened in Idaho
#   just all campsite            # not a plant: where to camp, with campsites marked
#
# Note the grid must be spelled when naming a region on `overlay` and `all`, because
# they are positional: `just overlay junioste id` binds id to the grid slot and the
# script fails loudly rather than quietly screening Utah.

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
owners reg="ut":
    @{{py}} scripts/ownership.py {{reg}}

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
fetch sp="junioste" reg="ut":
    {{py}} scripts/01_fetch.py {{sp}} {{reg}}

# EVT tiles -> data/work/<reg>/<sp>/class.vrt.
landfire sp="junioste" reg="ut":
    {{py}} scripts/02_landfire.py {{sp}} {{reg}}

# The cross-reference -> out/<reg>/<sp>/<sp>.gpkg. `grid` is hex or square; `just grids` lists them.
overlay sp="junioste" grid="hex" reg="ut":
    {{py}} scripts/03_overlay.py {{sp}} {{grid}} {{reg}}

# Camp mode only: the best ~10 spots per BLM field office / national forest, from the
# overlay's cells -> best_cells / best_spots in the GeoPackage. A no-op for any plant.
best sp="junioste" reg="ut":
    {{py}} scripts/03b_best.py {{sp}} {{reg}}

# csv / md / kml / gpx -> out/<reg>/<sp>/.
export sp="junioste" reg="ut":
    {{py}} scripts/04_export.py {{sp}} {{reg}}

# The QGIS project -> out/<reg>/<sp>/<sp>.qgs, and the portable copy beside it
# -> out/<reg>/<sp>/<sp>_qfield.qgz.
qgis sp="junioste" reg="ut":
    {{qgis_py}} scripts/05_qgis_project.py {{sp}} {{reg}}

# What to copy to the phone. Everything QField needs is in the one folder.
qfield sp="junioste" reg="ut": (qgis sp reg)
    #!/usr/bin/env bash
    set -euo pipefail
    dir=$({{py}} scripts/paths.py dir {{sp}} {{reg}})
    du -sh "$dir"
    echo "copy $dir/ to the phone, open {{sp}}_qfield.qgz in QField"
    echo "notes come back in $dir/field_notes.gpkg - clean will not touch it"

# Full pipeline, in order.
all sp="junioste" grid="hex" reg="ut": (fetch sp reg) (landfire sp reg) (overlay sp grid reg) (best sp reg) (export sp reg) (qgis sp reg)

# Every registered species, in registry order. Hours, not minutes.
all-species reg="ut":
    #!/usr/bin/env bash
    # Stage 03 is the long pole and runs once per tree. The EVT download is cached
    # region-wide, so only the first species ever pays for it.
    set -euo pipefail
    for sp in $({{py}} -c 'import sys; sys.path.insert(0, "scripts"); import species; print(" ".join(species.TAXA))'); do
      echo "=========== $sp ==========="
      just all "$sp" hex "{{reg}}"
    done

# Every species' map, from the GeoPackages stage 03 already wrote - for styling changes.
qgis-all reg="ut":
    #!/usr/bin/env bash
    set -euo pipefail
    for sp in $({{py}} -c 'import sys; sys.path.insert(0, "scripts"); import species; print(" ".join(species.TAXA))'); do
      just qgis "$sp" "{{reg}}"
    done

# Open the desktop map in QGIS.
open sp="junioste" reg="ut": (qgis sp reg)
    qgis "$({{py}} scripts/paths.py qgs {{sp}} {{reg}})"

# Print the acreage funnel and summary.
summary sp="junioste" reg="ut":
    @cat "$({{py}} scripts/paths.py summary {{sp}} {{reg}})"

# Delete generated outputs; keeps the data/ download cache and the field notes.
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    # field_notes.gpkg and the photos beside it are the only things under out/ that a
    # person made rather than this pipeline, and nothing can rebuild them. Dropping them
    # is `just clean-notes <sp>`, deliberately and one taxon at a time.
    # -mindepth 3 because the tree is out/<region>/<slug>/. It also means a pre-region
    # out/<slug>/ layout sits at depth 2 and is left entirely alone, so running this
    # before migrating cannot destroy notes that have not been moved yet.
    if [ -d out ]; then
      find out -mindepth 3 -depth \
        ! -name field_notes.gpkg ! -name DCIM ! -path 'out/*/*/DCIM/*' -delete
      find out -mindepth 1 -type d -empty -delete
    fi
    # symbology-style.db is QGIS's, written into whatever directory stage 05 ran from.
    # The *.qgs sweep is for the old layout, when the projects lived at the repo root;
    # they are under out/ now and the find above already took them.
    rm -f *.qgs *.qgs~ *_attachments.zip symbology-style.db
    find scripts -name __pycache__ -type d -exec rm -rf {} +

# Drop one taxon's field notes and photos. Nothing else deletes them.
clean-notes sp="junioste" reg="ut":
    #!/usr/bin/env bash
    set -euo pipefail
    dir=$({{py}} scripts/paths.py dir {{sp}} {{reg}})
    rm -rf "$dir/field_notes.gpkg" "$dir/DCIM"

# Delete outputs *and* the download cache - re-downloads everything; notes survive.
clean-all: clean
    rm -rf data
