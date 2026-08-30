"""Build juniper_blm.qgs from out/juniper_blm.gpkg.

Run with the system interpreter (PyQGIS is not in the project venv):
    /usr/bin/python3 scripts/05_qgis_project.py
"""
from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsSymbol,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSingleSymbolRenderer,
    QgsFillSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsCoordinateReferenceSystem,
    QgsShapeburstFillSymbolLayer, QgsSimpleLineSymbolLayer, QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GPKG = ROOT / "out" / "juniper_blm.gpkg"
QGS = ROOT / "juniper_blm.qgs"

# Single-hue magenta ramp: nothing in desert aerial imagery is this colour, so the
# fills stay separable from both canopy and bare ground. Cyan is the one accent.
#
# The ramp belongs to the 1 km² hex cells; every one of them clears HOTSPOT_MIN_PCT (25 %)
# by construction, so the breaks start there instead of at zero.
RAMP_CELLS = [
    (25, 40, "#ffb3e0", "cell 25-40 % juniper"),
    (40, 55, "#ff3fb0", "cell 40-55 %"),
    (55, 70, "#a80078", "cell 55-70 %"),
    (70, 85, "#6b0050", "cell 70-85 %"),
    (85, 101, "#4d0038", "cell 85 %+"),
]
# The parcel gradient uses the same idea one hue over: light cyan for a parcel that is
# mostly not juniper, deep teal-blue for one that mostly is. Steps are spread wide
# because cyan has a narrow gamut - these clear the ~15 dE adjacent-pair floor.
RAMP_PARCELS = [
    (0, 10, "#d6faff", "parcel 0-10 % juniper"),
    (10, 25, "#5cd6f2", "parcel 10-25 %"),
    (25, 40, "#00a3c9", "parcel 25-40 %"),
    (40, 60, "#00647f", "parcel 40-60 %"),
    (60, 101, "#00303f", "parcel 60 %+"),
]
# how far the cyan gradient reaches in from a parcel boundary, in ground metres:
# it follows the contour at working zoom and thins to the outline at statewide zoom,
# where a fixed screen width would instead flood every small parcel solid cyan
SHAPEBURST_M = 400
EXCLUDED = "#ff1744"
RANGE = "#7c4dff"
CASING = "#ffffff"
# a white hairline vanishes into salt flat and pale playa, so the BLM boundary gets a
# dark casing under it - the pair reads on bright ground and on canopy alike
CASING_UNDER = "#101010"
# Amber wash over every acre of BLM surface, following the convention BLM's own land-status
# maps use for its holdings. It is the one hue family the ramps left unclaimed - magenta,
# cyan, red and violet are all spoken for - and it is the bottom vector layer, so the alpha
# is set low enough that the two ramps still read cleanly through it.
BLM_WASH = "#ffb300"
BLM_WASH_ALPHA = 56  # 0-255
# QGIS provider strings percent-encode the inner URL: '=' -> %3D, '&' -> %26.
GOOGLE_SAT = (
    "type=xyz&url=https://mt1.google.com/vt/lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D"
    "%26z%3D%7Bz%7D&zmax=20&zmin=0"
)


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

    Used for the statewide BLM surface, which has to stay legible against imagery that
    swings from black canopy to white salt without ever competing with the ramps. The
    alpha rides on the fill colour rather than the symbol, so the boundary stays opaque.
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


def graduated(field, ramp, symbol=fill):
    ranges = [
        QgsRendererRange(lo, hi, symbol(color), label)
        for lo, hi, color, label in ramp
    ]
    return QgsGraduatedSymbolRenderer(field, ranges)


def add(project, layer, name):
    if not layer.isValid():
        raise RuntimeError(f"invalid layer: {name}")
    layer.setName(name)
    project.addMapLayer(layer)
    return layer


def gpkg_layer(name):
    return QgsVectorLayer(f"{GPKG}|layername={name}", name, "ogr")


def main():
    qgs = QgsApplication([], False)
    qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:26912"))
    project.setTitle("Utah juniper on BLM land - transplant permit screening")

    basemap = QgsRasterLayer(GOOGLE_SAT, "Google Satellite", "wms")
    if not basemap.isValid():
        raise RuntimeError("Google satellite basemap failed to load")
    project.addMapLayer(basemap)

    blm = add(project, gpkg_layer("blm_all"), "All BLM surface")
    blm.setRenderer(QgsSingleSymbolRenderer(
        washed(BLM_WASH, BLM_WASH_ALPHA, CASING, width=0.3)))

    rng = add(project, gpkg_layer("juniper_range"), "Little 1971 J. osteosperma range")
    rng.setRenderer(QgsSingleSymbolRenderer(
        fill("#00000000", outline=RANGE, width=0.6, style="no")))

    excl = add(project, gpkg_layer("exclusions"), "Excluded: Wilderness / WSA / NM-NCA")
    excl.setRenderer(QgsSingleSymbolRenderer(
        fill(EXCLUDED, outline=EXCLUDED, width=0.3, style="b_diagonal", opacity=0.6)))

    hot = add(project, gpkg_layer("hotspots"), "Scouting cells (1 km² hex, juniper %)")
    hot.setRenderer(graduated("juniper_pct", RAMP_CELLS, cell_fill))
    hot.setOpacity(0.65)

    # added after the cells so it draws on top: the gradient is transparent in the
    # middle, so it rims the parcel without hiding the cells inside it
    cand = add(project, gpkg_layer("candidates"), "Eligible BLM parcels (juniper %)")
    cand.setRenderer(graduated("juniper_pct", RAMP_PARCELS, shapeburst))

    offices = add(project, gpkg_layer("office_points"), "BLM offices")
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

    project.write(str(QGS))
    print(f"-> {QGS.name} with {len(project.mapLayers())} layers")
    qgs.exitQgis()


if __name__ == "__main__":
    main()
