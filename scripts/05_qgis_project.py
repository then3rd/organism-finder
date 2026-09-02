"""Build <slug>.qgs from out/<slug>/<slug>.gpkg.

Run with the system interpreter (PyQGIS is not in the project venv):
    /usr/bin/python3 scripts/05_qgis_project.py [species-slug]

paths/species/region/ownership are standard-library only precisely so this stage can
import them.
"""
from pathlib import Path
import datetime
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsSymbol,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSingleSymbolRenderer,
    QgsFillSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext, QgsDatumTransform,
    QgsShapeburstFillSymbolLayer, QgsSimpleLineSymbolLayer, QgsUnitTypes,
    QgsCategorizedSymbolRenderer, QgsRendererCategory,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import habitat  # noqa: E402
import paths  # noqa: E402
import ownership  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402

# Single-hue magenta ramp: nothing in desert aerial imagery is this colour, so the
# fills stay separable from both canopy and bare ground. Cyan is the one accent.
#
# The ramp belongs to the 1 km² hex cells; every one of them clears HOTSPOT_MIN_PCT (25 %)
# by construction, so the breaks start there instead of at zero.
RAMP_CELLS = [
    (25, 40, "#ffb3e0", "cell 25-40 % {short}"),
    (40, 55, "#ff3fb0", "cell 40-55 %"),
    (55, 70, "#a80078", "cell 55-70 %"),
    (70, 85, "#6b0050", "cell 70-85 %"),
    (85, 101, "#4d0038", "cell 85 %+"),
]
# The parcel gradient uses the same idea one hue over: light cyan for a parcel that is
# mostly bare of it, deep teal-blue for one that is mostly the tree. Steps are spread wide
# because cyan has a narrow gamut - these clear the ~15 dE adjacent-pair floor.
RAMP_PARCELS = [
    (0, 10, "#d6faff", "parcel 0-10 % {short}"),
    (10, 25, "#5cd6f2", "parcel 10-25 %"),
    (25, 40, "#00a3c9", "parcel 25-40 %"),
    (40, 60, "#00647f", "parcel 40-60 %"),
    (60, 101, "#00303f", "parcel 60 %+"),
]
# how far the cyan gradient reaches in from a parcel boundary, in ground metres:
# it follows the contour at working zoom and thins to the outline at statewide zoom,
# where a fixed screen width would instead flood every small parcel solid cyan
SHAPEBURST_M = 400
# The context grid, over ground this screen cannot act on. This ramp is red, and exclusions
# took the neutral slot it used to hold - the grid is the larger area by far and an
# achromatic wash disappeared into snow, playa and pale rock. What carries the ramp is still
# lightness, not hue (L* ~ 92 / 58 / 36 / 4): hue is the channel colour blindness takes away
# and lightness the one it leaves, so red is a tint over a lightness ramp rather than the
# ramp itself. Saturated reds at even lightness steps collapse to ~8 dE under simulated
# tritanopia; these hold 21.4 dE adjacent in normal vision and 16.5 under CVD. Four classes
# rather than five because five cannot span the lightness range and still clear the floor.
# The first step is as saturated as L* 92 allows - the sRGB gamut runs out above that, and
# buying more chroma by darkening it costs the pair its margin against the magenta cells.
RAMP_OTHER = [
    (25, 45, "#ffdede", "unavailable cell 25-45 % {short}"),
    (45, 65, "#dc6670", "unavailable cell 45-65 %"),
    (65, 85, "#9d283b", "unavailable cell 65-85 %"),
    (85, 101, "#230404", "unavailable cell 85 %+"),
]
# Near-black rather than red: exclusions and the context grid genuinely overlap - stage 03
# subtracts screenable *owners* from that grid, not exclusion geometry, so all NPS and USFWS
# Wilderness lies under both - and red is now the grid's. Hatch texture over a solid fill is
# what tells the two apart, so the hue only has to stay out of the way.
EXCLUDED = "#141414"
# Burn perimeters. Outline-only at full alpha, which is why an orange is affordable at
# all: the four ramps own magenta, cyan, red and violet as *fills*, and the ownership
# wash owns amber at alpha 56, so a hard 0.7 mm line at full saturation reads as its own
# thing against BLM's translucent yellow rather than competing with it. It is the only
# new hue this file has spent since the ramps were fixed, and it buys the one thing a
# forager needs to see at a glance: which side of the fire edge they are on.
BURN = "#e65100"
RANGE = "#7c4dff"
CASING = "#ffffff"
# a white hairline vanishes into salt flat and pale playa, so the BLM boundary gets a
# dark casing under it - the pair reads on bright ground and on canopy alike
CASING_UNDER = "#101010"
# The ownership wash: one colour per administering agency, taken from the registry in
# scripts/ownership.py so the map and the tables cannot disagree.
#
# This is the one place the hue budget documented above is deliberately overspent, and the
# reason it is safe here: the wash is the bottom vector layer at alpha 56, and agency
# identity is carried by the *opaque* casing line rather than by the translucent fill. So
# ownership may use green, brown and blue - hues the ramps avoid because at full alpha
# they collide with canopy, dirt and the cyan parcel ramp - without competing with any
# ramp step. Amber stays BLM's, which is the convention BLM's own land-status maps use.
# What is not negotiable is that the ramps above keep magenta, cyan, red and violet to
# themselves; adding a ninth agency means finding a hue outside those four, not borrowing
# one.
LAND_WASH_ALPHA = 56  # 0-255
# QGIS provider strings percent-encode the inner URL: '=' -> %3D, '&' -> %26.
BASEMAP_CRS = "EPSG:3857"
GOOGLE_SAT = (
    "type=xyz&url=https://mt1.google.com/vt/lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D"
    "%26z%3D%7Bz%7D&zmax=20&zmin=0"
)


def best_operation(src_crs, dst_crs):
    """The most accurate NAD83 -> WGS 84 pipeline this machine can actually run.

    What QGIS stores per CRS pair is the *whole* transformation, not just the datum shift,
    so this has to be a full proj pipeline - a bare `+proj=noop` would skip the UTM-to-
    Mercator projection step too and drop Utah into the Mediterranean. Asking QGIS for the
    pipeline rather than writing one out also means the answer improves by itself the day
    somebody installs the NADCON5 grids: the highest-accuracy operation here today is the
    4 m gridless one, and the 2 m `us_noaa_uthpgn` variant would win as soon as it exists.
    Either is far inside a 30 m LANDFIRE pixel.
    """
    src = QgsCoordinateReferenceSystem(src_crs)
    dst = QgsCoordinateReferenceSystem(dst_crs)
    # enumerating the operations makes PROJ complain on stderr about every grid that is not
    # installed, which is the normal case and not something the run should report
    with open(os.devnull, "w") as null:
        saved = os.dup(2)
        os.dup2(null.fileno(), 2)
        try:
            ops = [o for o in QgsDatumTransform.operations(src, dst) if o.isAvailable]
        finally:
            os.dup2(saved, 2)
            os.close(saved)
    if not ops:
        raise RuntimeError(f"no available {src_crs} -> {dst_crs} operation")
    best = min(ops, key=lambda o: o.accuracy if o.accuracy > 0 else float("inf"))
    print(f"  datum transform: {best.name} ({best.accuracy} m)")
    return best.proj


def fill(color, outline=CASING, width=0.4, style="solid", opacity=1.0):
    sym = QgsFillSymbol.createSimple(
        {"color": color, "outline_color": outline, "outline_width": str(width),
         "style": style}
    )
    sym.setOpacity(opacity)
    return sym


def cell_fill(color):
    """Flat class colour with a hairline casing - 17 k cells, so keep the stroke light."""
    return fill(color, width=0.2)


def shapeburst(color, outline=None, width=0.5, distance_m=SHAPEBURST_M):
    """Gradient that starts at the polygon's outer contour and fades inward to nothing.

    Keeps parcel interiors as bare imagery, so this can sit under the filled cell layer
    without the two washes stacking.
    """
    edge = QColor(color)
    centre = QColor(color)
    centre.setAlpha(0)
    sb = QgsShapeburstFillSymbolLayer()
    sb.setColor(edge)
    sb.setColor2(centre)
    sb.setUseWholeShape(False)
    sb.setMaxDistance(distance_m)
    sb.setDistanceUnit(QgsUnitTypes.RenderMetersInMapUnits)
    sb.setBlurRadius(3)

    sym = QgsFillSymbol()
    sym.changeSymbolLayer(0, sb)
    # the boundary takes the class colour too, so a parcel reads light-to-dark at a
    # glance instead of every parcel wearing the same bright outline
    stroke = QgsSimpleLineSymbolLayer(QColor(outline or color))
    stroke.setWidth(width)
    sym.appendSymbolLayer(stroke)
    return sym


def washed(fill_color, alpha, line, width, under=CASING_UNDER, under_width=None):
    """Flat translucent fill, then a dark casing line, then a lighter line over that.

    Used for the ownership wash, which has to stay legible against imagery that swings
    from black canopy to white salt without ever competing with the ramps. The alpha rides
    on the fill colour rather than the symbol, so the boundary stays opaque - which is what
    lets one agency be told from another at low fill alpha.
    """
    c = QColor(fill_color)
    c.setAlpha(alpha)
    sym = QgsFillSymbol.createSimple({
        "color": f"{c.red()},{c.green()},{c.blue()},{c.alpha()}",
        "outline_style": "no",
    })
    beneath = QgsSimpleLineSymbolLayer(QColor(under))
    beneath.setWidth(under_width if under_width is not None else width * 2.5)
    sym.appendSymbolLayer(beneath)
    over = QgsSimpleLineSymbolLayer(QColor(line))
    over.setWidth(width)
    sym.appendSymbolLayer(over)
    return sym


def ownership_renderer(layer, field="owner"):
    """One category per administering agency actually present in the layer.

    Driven off the data rather than off a fixed list: a region whose SMA layer names an
    agency the registry has not met still draws, in the registry's `UNKNOWN` grey, and
    reads as "nobody has checked this" - which is what it is.
    """
    idx = layer.fields().indexOf(field)
    if idx < 0:
        raise RuntimeError(f"{layer.name()}: no '{field}' field to categorise on")
    cats = []
    for code in sorted(layer.uniqueValues(idx), key=lambda c: str(c)):
        o = ownership.owner(code)
        sym = washed(o.color, LAND_WASH_ALPHA, o.color, width=0.3)
        access = "" if o.public else "  (closed)"
        cats.append(QgsRendererCategory(code, sym, f"{o.short} - {o.name}{access}"))
    return QgsCategorizedSymbolRenderer(field, cats)


def graduated(field, ramp, sp, symbol=fill):
    ranges = [
        QgsRendererRange(lo, hi, symbol(color), label.format(short=sp.short))
        for lo, hi, color, label in ramp
    ]
    return QgsGraduatedSymbolRenderer(field, ranges)


def hide(project, layer):
    """Add a layer to the project but leave it unchecked in the legend."""
    node = project.layerTreeRoot().findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(False)


def add(project, layer, name):
    if not layer.isValid():
        raise RuntimeError(f"invalid layer: {name}")
    layer.setName(name)
    project.addMapLayer(layer)
    return layer


def gpkg_layer(gpkg, name):
    return QgsVectorLayer(f"{gpkg}|layername={name}", name, "ogr")


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve()
    gpkg = paths.gpkg_path(sp)
    qgs_path = paths.qgs_path(sp)
    if not gpkg.exists():
        # Stage 03 writes funnel.csv either way, so its presence separates "screened, and
        # no public ground qualified" - which has no map to draw - from "never run".
        if (paths.out_dir(sp) / "funnel.csv").exists():
            print(f"{sp.common_name}: nothing qualified, no map to draw "
                  f"(see out/{sp.slug}/summary.md)")
            return
        raise SystemExit(f"{gpkg} not found - run stages 03 and 04 for {sp.slug} first")
    tree = sp.common_name[:1].upper() + sp.common_name[1:]

    qgs = QgsApplication([], False)
    qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(reg.crs))
    # The layers are NAD83 and the XYZ basemap is WGS 84 pseudo-Mercator, and NAD83 ->
    # WGS 84 has a dozen published operations, so QGIS interrupts every open to ask which
    # one unless the project names one itself.
    ctx = QgsCoordinateTransformContext()
    ctx.addCoordinateOperation(
        QgsCoordinateReferenceSystem(reg.crs),
        QgsCoordinateReferenceSystem(BASEMAP_CRS),
        best_operation(reg.crs, BASEMAP_CRS),
    )
    project.setTransformContext(ctx)
    what = ("transplant permit screening" if sp.mode == species_mod.COLLECT
            else "where to go and look")
    project.setTitle(f"{tree} on {reg.name} public land - {what}")

    basemap = QgsRasterLayer(GOOGLE_SAT, "Google Satellite", "wms")
    if not basemap.isValid():
        raise RuntimeError("Google satellite basemap failed to load")
    project.addMapLayer(basemap)

    # Everything the SMA layer names, private included, so the map can answer "why does
    # the stand stop here". Off by default: it is the busiest layer in the file and its
    # only job is to be switched on when a boundary looks arbitrary.
    all_land = add(project, gpkg_layer(gpkg, "land_all"),
                   "All surface management (incl. private)")
    all_land.setRenderer(ownership_renderer(all_land))
    hide(project, all_land)

    public = add(project, gpkg_layer(gpkg, "public_land"),
                 "Public land (by administrator)")
    public.setRenderer(ownership_renderer(public))

    # A taxon with no range filter has no range layer, and a taxon screened from records
    # has one that means something different, so the layer is named for what it is.
    rng = gpkg_layer(gpkg, "species_range")
    if rng.isValid() and rng.featureCount():
        label_rng = (f"Little 1971 {sp.binomial} range"
                     if sp.range_source == species_mod.LITTLE
                     else f"Documented range of {sp.binomial} (buffered records)")
        add(project, rng, label_rng)
        rng.setRenderer(QgsSingleSymbolRenderer(
            fill("#00000000", outline=RANGE, width=0.6, style="no")))

    other = gpkg_layer(gpkg, "other_cells")
    if other.isValid() and other.featureCount():
        add(project, other, f"Cells off screened land (1 km² hex, {sp.short} %)")
        other.setRenderer(graduated("species_pct", RAMP_OTHER, sp, cell_fill))
        other.setOpacity(0.55)

    # added after the context grid so the hatch draws on top of it: the two overlap wherever
    # an exclusion sits on ground no screenable owner administers, and underneath a
    # translucent grid the hatch would be the thing that disappears
    excl_name = ("Excluded: Wilderness / WSA / NM-NCA" if sp.mode == species_mod.COLLECT
                 else "Wilderness / WSA / NM-NCA (open to visit)")
    excl = add(project, gpkg_layer(gpkg, "exclusions"), excl_name)
    excl.setRenderer(QgsSingleSymbolRenderer(
        fill(EXCLUDED, outline=EXCLUDED, width=0.3, style="b_diagonal", opacity=0.6)))

    # The water buffer is added and hidden, the way land_all is. Open water is already
    # legible on the imagery, and what the map has to *show* is the buffer's consequence -
    # which cells survived - not the buffer itself. Carrying it as a switchable layer means
    # a result can be inspected without spending a hue the ramps would then have to avoid.
    water = gpkg_layer(gpkg, "water_buffer")
    if water.isValid() and water.featureCount():
        w = add(project, water, "Perennial water buffer (screen input)")
        w.setRenderer(QgsSingleSymbolRenderer(
            fill("#00000000", outline="#0d47a1", width=0.3, style="no")))
        hide(project, w)

    # Under the cells rather than over them: the cells are the answer and the perimeter is
    # the reason, so the perimeter frames them instead of cutting across them.
    burns = gpkg_layer(gpkg, "burns")
    if burns.isValid() and burns.featureCount():
        window = next((c for c in sp.conditions if c.kind == habitat.BURN), None)
        # `label()` already reads as a phrase - "burned 1-3 season(s) ago (fire years
        # 2023-2025)" - so wrapping it in another bracket doubles them.
        add(project, burns,
            f"Burn perimeters: {window.label(datetime.date.today().year)}"
            if window else "Burn perimeters")
        burns.setRenderer(QgsSingleSymbolRenderer(
            fill("#00000000", outline=BURN, width=0.7, style="no")))

    cells_name = {"collect": "Scouting cells", "observe": "Viewing cells",
                  "forage": "Foraging cells"}[sp.mode]
    hot = add(project, gpkg_layer(gpkg, "hotspots"),
              f"{cells_name} (1 km² hex, {sp.short} %)")
    hot.setRenderer(graduated("species_pct", RAMP_CELLS, sp, cell_fill))
    hot.setOpacity(0.65)

    # added after the cells so it draws on top: the gradient is transparent in the
    # middle, so it rims the parcel without hiding the cells inside it
    cand = add(project, gpkg_layer(gpkg, "candidates"),
               f"Eligible public parcels ({sp.short} %)")
    cand.setRenderer(graduated("species_pct", RAMP_PARCELS, sp, shapeburst))

    # The records themselves, on top of everything they generated, so the reader can see
    # how thin the evidence for a cell actually is.
    occ = gpkg_layer(gpkg, "occurrences")
    if occ.isValid() and occ.featureCount():
        add(project, occ, f"GBIF records ({occ.featureCount()})")
        occ.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": RANGE, "outline_color": "#ffffff",
             "outline_width": "0.3", "size": "2"}
        )))

    offices = add(project, gpkg_layer(gpkg, "office_points"), "BLM offices")
    offices.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "#ffffff", "outline_color": "#000000",
         "outline_width": "0.4", "size": "3"}
    )))
    label = QgsPalLayerSettings()
    label.fieldName = "ADMU_NAME"
    label.enabled = True
    fmt = QgsTextFormat()
    fmt.setSize(9)
    fmt.setColor(QColor("#ffffff"))
    buf = fmt.buffer()
    buf.setEnabled(True)
    buf.setColor(QColor("#000000"))
    fmt.setBuffer(buf)
    label.setFormat(fmt)
    offices.setLabeling(QgsVectorLayerSimpleLabeling(label))
    offices.setLabelsEnabled(True)

    project.write(str(qgs_path))
    print(f"-> {qgs_path.name} with {len(project.mapLayers())} layers")
    qgs.exitQgis()


if __name__ == "__main__":
    main()
