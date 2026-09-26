"""Build <slug>.qgs from out/<slug>/<slug>.gpkg.

Run with the system interpreter (PyQGIS is not in the project venv):
    /usr/bin/python3 scripts/05_qgis_project.py [species-slug]

paths/species/region/ownership are standard-library only precisely so this stage can
import them.
"""
from pathlib import Path
import csv
import datetime
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    Qgis, QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsSymbol,
    QgsFields, QgsField, QgsFeature, QgsGeometry, QgsPointXY, QgsDefaultValue,
    QgsMemoryProviderUtils, QgsVectorFileWriter, QgsLayerMetadata, QgsProjectMetadata,
    QgsEditorWidgetSetup, QgsEditFormConfig, QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSingleSymbolRenderer,
    QgsFillSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext, QgsDatumTransform,
    QgsShapeburstFillSymbolLayer, QgsSimpleLineSymbolLayer, QgsUnitTypes,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsRuleBasedRenderer,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402
from qgis.PyQt.QtCore import QDate, QMetaType  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factsheet  # noqa: E402
import grid as grid_mod  # noqa: E402
import habitat  # noqa: E402
import paths  # noqa: E402
import ownership  # noqa: E402
import region as region_mod  # noqa: E402
import species as species_mod  # noqa: E402

# Single-hue magenta ramp: nothing in desert aerial imagery is this colour, so the
# fills stay separable from both canopy and bare ground. Cyan is the one accent.
#
# The ramp belongs to the 1 km² scouting cells; every one clears HOTSPOT_MIN_PCT (25 %)
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


# The About point and the Field notes points are achromatic by rule. Every hue is
# spoken for by the time they are added - four ramps, eight agencies, one burn edge - so
# the two layers carrying the reader's own material are told apart by *shape* and by
# filled-versus-hollow, which is the same texture-over-hue argument the exclusion hatch
# makes. A found note and a looked-and-did-not-find note differ by fill, not colour, so
# the distinction survives both colour blindness and a phone screen in the sun.
MARK = "#ffffff"

# The fact sheet, in the order it reads. `multiline` is the difference between a value
# QGIS shows in a one-line box and one it gives a paragraph box to, which for prose this
# long is the difference between readable and not.
#   name, type, alias (what the form calls it), multiline
ABOUT_FIELDS = (
    ("map_title", QMetaType.Type.QString, "Map", False),
    ("binomial", QMetaType.Type.QString, "Scientific name", False),
    ("common_name", QMetaType.Type.QString, "Common name", False),
    ("kind", QMetaType.Type.QString, "Kind", False),
    ("purpose", QMetaType.Type.QString, "What this map is for", False),
    ("region", QMetaType.Type.QString, "Region", False),
    ("range_source", QMetaType.Type.QString, "Range source", True),
    ("cover_source", QMetaType.Type.QString, "Cover source", True),
    ("conditions", QMetaType.Type.QString, "Habitat conditions", True),
    ("screen", QMetaType.Type.QString, "How the screen works", True),
    ("funnel", QMetaType.Type.QString, "How the acreage narrows", True),
    ("grid", QMetaType.Type.QString, "Scouting grid", True),
    ("owners", QMetaType.Type.QString, "Who administers it", True),
    ("ground_truth", QMetaType.Type.QString, "Ground-truth caveat", True),
    ("sensitive", QMetaType.Type.QString, "Sensitive", False),
    ("caveat", QMetaType.Type.QString, "Before you go", True),
    ("crs", QMetaType.Type.QString, "Working CRS", False),
    ("built_on", QMetaType.Type.QDate, "Built", False),
    ("built_by", QMetaType.Type.QString, "Built by", False),
)

# What a person in the field writes down. `found` is three-valued rather than a checkbox
# on purpose: "I looked and did not find" and "I am not sure what I saw" are different
# observations, and a boolean forces the second into the first - which is the same
# mistake as reading absence of records as absence of the plant.
NOTES_FIELDS = (
    ("noted_on", QMetaType.Type.QDateTime, "When"),
    ("found", QMetaType.Type.QString, "Did you find it?"),
    ("count", QMetaType.Type.Int, "How many"),
    ("confidence", QMetaType.Type.QString, "How sure are you"),
    ("photo", QMetaType.Type.QString, "Photo"),
    ("note", QMetaType.Type.QString, "Notes"),
)
FOUND = (("yes", "yes - I found it"), ("no", "no - I looked and did not"),
         ("unsure", "not sure what I saw"))

# The one thing this map cannot tell you that the summary does not have to: the imagery
# is fetched, not carried.
BASEMAP_NOTE = (
    "The aerial basemap is online tiles. Without a data connection the imagery will not "
    "draw and only the vector layers will - those are in the GeoPackages beside this "
    "project and need no signal."
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


def ownership_renderer(layer, reg, field="owner", symbol=None, label=None):
    """One category per administering agency actually present in the layer.

    Driven off the data rather than off a fixed list: a region whose SMA layer names an
    agency the registry has not met still draws, in the registry's `UNKNOWN` grey, and
    reads as "nobody has checked this" - which is what it is.

    `symbol` and `label` take an Owner and default to the translucent wash; the camping
    map passes its own to fill cells by owner with the same registry colours.
    """
    symbol = symbol or (lambda o: washed(o.color, LAND_WASH_ALPHA, o.color, width=0.3))
    label = label or (lambda o: f"{o.short} - {o.name}{'' if o.public else '  (closed)'}")
    idx = layer.fields().indexOf(field)
    if idx < 0:
        raise RuntimeError(f"{layer.name()}: no '{field}' field to categorise on")
    cats = []
    for code in sorted(layer.uniqueValues(idx), key=lambda c: str(c)):
        o = ownership.owner(code, reg)
        cats.append(QgsRendererCategory(code, symbol(o), label(o)))
    return QgsCategorizedSymbolRenderer(field, cats)


def graduated(field, ramp, sp, symbol=fill):
    ranges = [
        QgsRendererRange(lo, hi, symbol(color), label.format(short=sp.short))
        for lo, hi, color, label in ramp
    ]
    return QgsGraduatedSymbolRenderer(field, ranges)


def describe(layer, abstract, title=None):
    """Say what a layer is, where QGIS and QField both show it: the metadata panel.

    Most of these sentences were source comments. A comment explains the layer to whoever
    edits this file; the reader holding the map is the one who actually needs it.
    """
    m = layer.metadata()
    m.setTitle(title or layer.name())
    m.setIdentifier(layer.name())
    m.setAbstract(abstract)
    layer.setMetadata(m)
    return layer


def alias(layer, name, text):
    layer.setFieldAlias(layer.fields().indexOf(name), text)


def widget(layer, name, kind, cfg):
    layer.setEditorWidgetSetup(layer.fields().indexOf(name),
                               QgsEditorWidgetSetup(kind, cfg))


def form(layer, names, title):
    """An explicit form layout, so the fields read in the order they were written.

    Without one QGIS lays the form out by field order and gives `fid` a box of its own;
    on a phone, where the form *is* the layer, that is the whole reading experience.
    """
    cfg = layer.editFormConfig()
    cfg.setLayout(QgsEditFormConfig.EditorLayout.TabLayout)
    cfg.clearTabs()
    root = cfg.invisibleRootContainer()
    tab = QgsAttributeEditorContainer(title, root)
    for name in names:
        tab.addChildElement(
            QgsAttributeEditorField(name, layer.fields().indexOf(name), tab))
    root.addChildElement(tab)
    layer.setEditFormConfig(cfg)
    if layer.fields().indexOf("fid") >= 0:
        widget(layer, "fid", "Hidden", {})


def label_with(layer, text, expression=False, size=9):
    """White text on a black buffer - legible over imagery that swings light to dark."""
    label = QgsPalLayerSettings()
    label.fieldName = text
    label.isExpression = expression
    label.enabled = True
    fmt = QgsTextFormat()
    fmt.setSize(size)
    fmt.setColor(QColor("#ffffff"))
    buf = fmt.buffer()
    buf.setEnabled(True)
    buf.setColor(QColor("#000000"))
    fmt.setBuffer(buf)
    label.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(label))
    layer.setLabelsEnabled(True)


def marker(shape, color=MARK, outline=CASING_UNDER, size="3", width="0.4",
           style="solid"):
    return QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": shape, "color": color, "outline_color": outline,
         "outline_width": width, "size": size, "style": style}
    ))


def hide(project, layer):
    """Add a layer to the project but leave it unchecked in the legend."""
    node = project.layerTreeRoot().findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(False)


def add(project, layer, name, abstract=None):
    if not layer.isValid():
        raise RuntimeError(f"invalid layer: {name}")
    layer.setName(name)
    if abstract:
        describe(layer, abstract)
    project.addMapLayer(layer)
    return layer


def gpkg_layer(gpkg, name):
    return QgsVectorLayer(f"{gpkg}|layername={name}", name, "ogr")


def write_layer(mem, path, layer_name):
    """A memory layer to its own GeoPackage, replacing whatever was there."""
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = layer_name
    opts.fileEncoding = "UTF-8"
    opts.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile)
    err, msg = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, str(path), QgsCoordinateTransformContext(), opts)[:2]
    if err != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"{path.name}: {msg}")


def owner_codes(gpkg):
    """The administrators actually present on the eligible parcels, as summary.md counts
    them - not everyone this mode could screen, which would name agencies that hold none
    of this taxon."""
    cand = gpkg_layer(gpkg, "candidates")
    idx = cand.fields().indexOf("owner")
    return sorted(cand.uniqueValues(idx), key=str) if idx >= 0 else []


def funnel_text(sp, reg):
    """The acreage narrowing, read back from what stage 03 wrote.

    Read rather than recomputed: `MIN_SPECIES_ACRES`, `HOTSPOT_KM2` and `HOTSPOT_MIN_PCT`
    are stage 03's constants and this stage cannot import it, so any number here would be
    a third hand-copy of them. funnel.csv states them as they were actually applied.
    """
    path = paths.out_dir(sp, reg) / "funnel.csv"
    if not path.exists():
        return ""
    with path.open() as fh:
        return "\n".join(
            f"{r['stage']} - {int(float(r['features'])):,} features, "
            f"{float(r['acres']):,.0f} acres"
            for r in csv.DictReader(fh)
        )


def about_values(sp, reg, grid_text, codes, when):
    crs = QgsCoordinateReferenceSystem(reg.crs)
    return {
        "map_title": factsheet.project_title(sp, reg),
        "binomial": sp.binomial,
        "common_name": factsheet.title(sp),
        "kind": sp.kind,
        "purpose": f"{sp.mode} - {factsheet.WHAT[sp.mode]}",
        "region": reg.name,
        "range_source": factsheet.range_text(sp),
        "cover_source": factsheet.cover_text(sp),
        "conditions": factsheet.conditions_text(sp, when.year),
        "screen": factsheet.plain(factsheet.how(sp, year=when.year)),
        "funnel": funnel_text(sp, reg),
        "grid": grid_text,
        "owners": factsheet.owner_plain(sp, reg, codes),
        "ground_truth": factsheet.plain(sp.ground_truth_caveat),
        "sensitive": ("yes - the coordinates here are full precision, like every other "
                      "taxon's. Do not repost them."
                      if sp.sensitive else "no"),
        "caveat": factsheet.caveats_plain(sp, reg) + "\n- " + BASEMAP_NOTE,
        "crs": f"{reg.crs} - {crs.description()}",
        "built_on": QDate.currentDate(),
        "built_by": f"geo-test-juniper stage 05, QGIS {Qgis.QGIS_VERSION}",
    }


def write_about(sp, reg, gpkg, grid_text, when):
    """The fact sheet, as one point you can tap.

    A layer rather than a text file because "what is this map?" gets asked on a phone in
    a canyon, where the map is the only thing open. Everything in it is registry prose or
    something stage 03 wrote down; nothing here is computed a second time.
    """
    box = gpkg_layer(gpkg, "land_all").extent()
    if box.isNull() or box.isEmpty():
        box = gpkg_layer(gpkg, "public_land").extent()
    if box.isNull() or box.isEmpty():
        # A missing fact sheet must not cost anybody the map.
        print("  no usable extent for the About point - skipping the fact sheet")
        return None

    flds = QgsFields()
    for name, qtype, _, _ in ABOUT_FIELDS:
        flds.append(QgsField(name, qtype))
    mem = QgsMemoryProviderUtils.createMemoryLayer(
        "about", flds, Qgis.WkbType.Point, QgsCoordinateReferenceSystem(reg.crs))
    feat = QgsFeature(mem.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(box.center())))
    for name, value in about_values(sp, reg, grid_text, owner_codes(gpkg), when).items():
        feat.setAttribute(name, value)
    mem.dataProvider().addFeatures([feat])
    write_layer(mem, paths.about_gpkg(sp, reg), "about")

    # Re-opened from the file rather than added as it stands: a memory layer serialises
    # into the project as an empty `memory://` datasource, so the map would carry a fact
    # sheet with nothing in it.
    about = gpkg_layer(paths.about_gpkg(sp, reg), "about")
    for name, _, text, multiline in ABOUT_FIELDS:
        alias(about, name, text)
        widget(about, name, "TextEdit", {"IsMultiline": multiline, "UseHtml": False})
    form(about, [f[0] for f in ABOUT_FIELDS], "About this map")
    about.setDisplayExpression('"map_title"')
    return about


def ensure_notes(sp, reg):
    """The notes file, created once and then left alone for good.

    This is the only artifact under out/ that a person made rather than the pipeline, and
    nothing can rebuild it. So: created when missing, opened otherwise, and never
    migrated - a schema change must not be able to eat observations.
    """
    path = paths.notes_gpkg(sp, reg)
    if not path.exists():
        flds = QgsFields()
        for name, qtype, _ in NOTES_FIELDS:
            flds.append(QgsField(name, qtype))
        mem = QgsMemoryProviderUtils.createMemoryLayer(
            "notes", flds, Qgis.WkbType.Point, QgsCoordinateReferenceSystem(reg.crs))
        write_layer(mem, path, "notes")
        print(f"  created {path.relative_to(paths.ROOT)} (empty; yours from here on)")

    notes = gpkg_layer(path, "notes")
    missing = [n for n, _, _ in NOTES_FIELDS if notes.fields().indexOf(n) < 0]
    if missing:
        print(f"  note: {path.name} predates {', '.join(missing)} - left as it is")

    for name, _, text in NOTES_FIELDS:
        if notes.fields().indexOf(name) >= 0:
            alias(notes, name, text)
    if notes.fields().indexOf("noted_on") >= 0:
        widget(notes, "noted_on", "DateTime", {
            "field_format": "yyyy-MM-ddTHH:mm:ss",
            "display_format": "yyyy-MM-dd HH:mm",
            "calendar_popup": True, "allow_null": True, "field_iso_format": False})
        notes.setDefaultValueDefinition(notes.fields().indexOf("noted_on"),
                                        QgsDefaultValue("now()", False))
    if notes.fields().indexOf("found") >= 0:
        widget(notes, "found", "ValueMap",
               {"map": [{label: value} for value, label in FOUND]})
    if notes.fields().indexOf("count") >= 0:
        widget(notes, "count", "Range",
               {"Min": 0, "Max": 100000, "Step": 1, "Style": "SpinBox",
                "AllowNull": True})
    if notes.fields().indexOf("confidence") >= 0:
        widget(notes, "confidence", "ValueMap", {"map": [
            {"certain": "certain"}, {"probable": "probable"}, {"a guess": "guess"}]})
    if notes.fields().indexOf("photo") >= 0:
        # RelativeStorage 1 is RelativeProject: photos are stored relative to the project
        # file, which lives in out/<slug>/, so they land in out/<slug>/DCIM/ and travel
        # with the folder in both directions. Absolute would write a phone path the
        # desktop cannot resolve, and the other way round.
        widget(notes, "photo", "ExternalResource", {
            "StorageMode": 0, "RelativeStorage": 1, "DefaultRoot": "DCIM",
            "DocumentViewer": 1, "DocumentViewerHeight": 0, "DocumentViewerWidth": 0,
            "FileWidget": True, "FileWidgetButton": True, "FileWidgetFilter": "",
            "StorageType": "", "PropertyCollection": {}})
    if notes.fields().indexOf("note") >= 0:
        widget(notes, "note", "TextEdit", {"IsMultiline": True, "UseHtml": False})
    form(notes, [n for n, _, _ in NOTES_FIELDS if notes.fields().indexOf(n) >= 0],
         "Field note")
    notes.setDisplayExpression(
        "coalesce(format_date(\"noted_on\", 'yyyy-MM-dd'), 'note') || "
        "' - ' || coalesce(\"found\", '?')")
    return notes


def notes_renderer():
    """Found, not found, unsure - by shape and by fill, never by hue.

    Nothing on this map has a hue left to spend, and these three have to be told apart on
    a phone screen in the sun by somebody who may well be colour blind.
    """
    styles = {"yes": ("triangle", "solid"), "no": ("triangle", "no"),
              "unsure": ("cross2", "solid")}
    cats = []
    for value, text in FOUND:
        shape, style = styles[value]
        sym = QgsMarkerSymbol.createSimple(
            {"name": shape, "color": MARK if style == "solid" else "#00000000",
             "outline_color": MARK, "outline_width": "0.6", "size": "3.5"})
        cats.append(QgsRendererCategory(value, sym, text))
    return QgsCategorizedSymbolRenderer("found", cats)


# Campsite markers. Achromatic, by the same rule as the notes and the About point: every
# hue is spoken for, so these are told apart by shape and fill. Stars and diamonds because
# triangles are the notes', the square is About's and circles are the offices' and the
# GBIF records'. Filled means "an established primitive site", the thing the reader came
# for; hollow means a developed campground, context and fallback.
SITE_STYLE = {
    "primitive": ("star", "solid", "3.8", "primitive site"),
    "developed": ("diamond", "no", "3.0", "developed campground"),
    "unclassified": ("pentagon", "no", "2.4", "campsite, type not recorded"),
}
# A site on ground the screen is not open to - private, tribal, a refuge - is drawn, not
# dropped, because a well-known campground that silently vanishes is a question, and a
# faded one is an answer.
SITE_CLOSED_OPACITY = 0.4
SITE_LABEL_SCALE = 50000


def campsite_renderer():
    root = QgsRuleBasedRenderer.Rule(None)
    for closed in (False, True):
        for value, (shape, style, size, text) in SITE_STYLE.items():
            sym = QgsMarkerSymbol.createSimple(
                {"name": shape, "color": MARK if style == "solid" else "#00000000",
                 "outline_color": MARK if style == "no" else CASING_UNDER,
                 "outline_width": "0.6" if style == "no" else "0.4", "size": size})
            if closed:
                sym.setOpacity(SITE_CLOSED_OPACITY)
            test = "not \"on_screened\"" if closed else "\"on_screened\""
            root.appendChild(QgsRuleBasedRenderer.Rule(
                sym, filterExp=f"\"site_class\" = '{value}' and {test}",
                label=f"{text}{' (ground not open to camping)' if closed else ''}"))
    return QgsRuleBasedRenderer(root)


def portable(project, notes):
    """Turn the built project into the one that goes on the phone.

    Relative paths, because out/<slug>/ is copied wholesale and the phone's idea of
    /home/n3rd is nothing. Read-only everywhere except the notes layer, because the only
    thing a person in the field should be able to change is what they observed - every
    other layer is a screening result, and an accidental edit would make the map disagree
    with the GeoPackage it was generated from.

    What QField actually needs here is all QGIS-native and verified to survive the write:
    the read-only flags, the form config, the aliases and the relative paths. The
    `QFieldSync/*` properties below are hints for the packaging plugin, which is not
    installed on this machine and so has not been exercised - an unrecognised custom
    property is inert, which is why writing them is safe and why no project-level ones
    are written at all.
    """
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer is notes:
            continue
        layer.setReadOnly(True)
        layer.setCustomProperty("QFieldSync/action", "copy")
        layer.setCustomProperty("QFieldSync/cloud_action", "no_action")
    if notes is None:
        return
    notes.setReadOnly(False)
    notes.setCustomProperty("QFieldSync/action", "offline")
    notes.setCustomProperty("QFieldSync/cloud_action", "cloud")
    notes.setCustomProperty("QFieldSync/is_geometry_locked", False)
    # The key was renamed across QFieldSync 3.x and I could not confirm which this build
    # would read, so both are written.
    naming = json.dumps(
        {"photo": "'DCIM/' || format_date(now(), 'yyyyMMdd_hhmmss') || '.jpg'"})
    notes.setCustomProperty("QFieldSync/photo_naming", naming)
    notes.setCustomProperty("QFieldSync/attachment_naming", naming)


def main():
    sp = species_mod.resolve(sys.argv)
    reg = region_mod.resolve(sys.argv)
    # Read back rather than taken as an argument: the map has to name the lattice stage
    # 03 actually cut, not one this invocation was told about.
    shape = grid_mod.recall(paths.grid_marker(sp, reg))
    gpkg = paths.gpkg_path(sp, reg)
    qgs_path = paths.qgs_path(sp, reg)
    if not gpkg.exists():
        # Stage 03 writes funnel.csv either way, so its presence separates "screened, and
        # no public ground qualified" - which has no map to draw - from "never run".
        if (paths.out_dir(sp, reg) / "funnel.csv").exists():
            print(f"{sp.common_name}: nothing qualified, no map to draw "
                  f"(see {(paths.out_dir(sp, reg) / 'summary.md').relative_to(paths.ROOT)})")
            return
        raise SystemExit(f"{gpkg} not found - run stages 03 and 04 for {sp.slug} first")

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
    # One function names this map and summary.md's heading both. They were written
    # separately once, and a forage taxon's map said "where to go and look" over a
    # summary that said "where to go and pick".
    project.setTitle(factsheet.project_title(sp, reg))

    when = datetime.date.today()
    grid_text = f"1 km² {shape.label} cells - {shape.note}"
    meta = QgsProjectMetadata()
    meta.setTitle(factsheet.project_title(sp, reg))
    meta.setAbstract(factsheet.abstract(sp, reg, grid=grid_text,
                                        codes=owner_codes(gpkg)))
    meta.setAuthor("geo-test-juniper, scripts/05_qgis_project.py")
    meta.setKeywords({"gmd:topicCategory": [sp.binomial, reg.name, sp.kind, sp.mode]})
    project.setMetadata(meta)

    about = write_about(sp, reg, gpkg, grid_text, when)
    notes = ensure_notes(sp, reg)

    basemap = QgsRasterLayer(GOOGLE_SAT, "Google Satellite", "wms")
    if not basemap.isValid():
        raise RuntimeError("Google satellite basemap failed to load")
    project.addMapLayer(basemap)

    # Everything the SMA layer names, private included, so the map can answer "why does
    # the stand stop here". Off by default: it is the busiest layer in the file and its
    # only job is to be switched on when a boundary looks arbitrary.
    all_land = add(project, gpkg_layer(gpkg, "land_all"),
                   "All surface management (incl. private)",
                   "Every polygon the region's Surface Management Agency layer names, "
                   "private included. Off by default; switch it on to answer why a stand "
                   "stops where it does.")
    all_land.setRenderer(ownership_renderer(all_land, reg))
    hide(project, all_land)

    public = add(project, gpkg_layer(gpkg, "public_land"),
                 "Public land (by administrator)",
                 "Public surface, coloured by who administers it. A permit from one "
                 "agency is worth nothing on another's ground, so this layer answers the "
                 "question the parcel layer does not: whose ground are you on.")
    public.setRenderer(ownership_renderer(public, reg))

    # A taxon with no range filter has no range layer, and a taxon screened from records
    # has one that means something different, so the layer is named for what it is.
    rng = gpkg_layer(gpkg, "species_range")
    if rng.isValid() and rng.featureCount():
        label_rng = (f"Little 1971 {sp.binomial} range"
                     if sp.range_source == species_mod.LITTLE
                     else f"Documented range of {sp.binomial} (buffered records)")
        add(project, rng, label_rng,
            factsheet.range_text(sp))
        rng.setRenderer(QgsSingleSymbolRenderer(
            fill("#00000000", outline=RANGE, width=0.6, style="no")))

    other = gpkg_layer(gpkg, "other_cells")
    if other.isValid() and other.featureCount():
        add(project, other,
            f"Cells off screened land (1 km² {shape.label}, {sp.short} %)",
            "The same grid and the same scoring pass over ground this screen cannot act "
            "on: private, tribal, closed withdrawals, and any public owner this taxon's "
            "mode rules out. Context for reading the map, never a target - there is "
            "nobody to ask about this ground, and nothing in the deliverables is derived "
            "from it.")
        other.setRenderer(graduated("species_pct", RAMP_OTHER, sp, cell_fill))
        other.setOpacity(0.55)

    # added after the context grid so the hatch draws on top of it: the two overlap wherever
    # an exclusion sits on ground no screenable owner administers, and underneath a
    # translucent grid the hatch would be the thing that disappears
    excl = add(
        project, gpkg_layer(gpkg, "exclusions"), factsheet.exclusion_label(sp),
        "Wilderness, WSAs and NM/NCA units. Subtracted in collect mode, because a plant "
        "cannot lawfully leave them; kept and flagged otherwise, because walking in to "
        "look or to pick for the pot is exactly what they are for."
        if sp.mode != species_mod.COLLECT else
        "Wilderness, WSAs and NM/NCA units, subtracted from the candidates: a plant "
        "cannot lawfully leave this ground whatever the administrator would permit "
        "elsewhere.")
    excl.setRenderer(QgsSingleSymbolRenderer(
        fill(EXCLUDED, outline=EXCLUDED, width=0.3, style="b_diagonal", opacity=0.6)))

    # The water buffer is added and hidden, the way land_all is. Open water is already
    # legible on the imagery, and what the map has to *show* is the buffer's consequence -
    # which cells survived - not the buffer itself. Carrying it as a switchable layer means
    # a result can be inspected without spending a hue the ramps would then have to avoid.
    water = gpkg_layer(gpkg, "water_buffer")
    if water.isValid() and water.featureCount():
        w = add(project, water, "Perennial water buffer (screen input)",
                "The buffer on perennial flowlines and waterbodies this taxon was "
                "screened against. Hidden: what the map has to show is the buffer's "
                "consequence, which the surviving cells already encode.")
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
            f"Burn perimeters: {window.label(when.year)}"
            if window else "Burn perimeters",
            "The fires this taxon's burn window selected. The perimeter says a fire "
            "happened, not how hot it burned, and unburned islands inside it count as "
            "burned. A recent burn is also a hazard: falling snags, hot ash pits, washed "
            "-out roads, and closure orders this screening does not model.")
        burns.setRenderer(QgsSingleSymbolRenderer(
            fill("#00000000", outline=BURN, width=0.7, style="no")))

    camp = sp.mode == species_mod.CAMP
    hot = add(project, gpkg_layer(gpkg, "hotspots"),
              f"{factsheet.CELLS[sp.mode]} "
              + (f"by {sp.short} % (1 km² {shape.label})" if camp
                 else f"(1 km² {shape.label}, {sp.short} %)"),
              f"{grid_text}. Each cell is clipped to eligible public land and carries at "
              f"least the threshold's worth of {sp.short}; this is the layer the waypoint "
              "files are drawn from.")
    hot.setRenderer(graduated("species_pct", RAMP_CELLS, sp, cell_fill))
    hot.setOpacity(0.65)
    if camp:
        # On a camping map the ramp barely varies - most qualifying cells are all flat -
        # and the question a camper has is whose rules apply. So the same cells are
        # added again, filled by administrator with the registry colours, and that is
        # the one shown; the flat-ground ramp stays in the legend, switched off. Safe
        # against the palette rule because the magenta layer is hidden, the cells keep
        # cell_fill's white casing, and the wash beneath is the same colours.
        hide(project, hot)
        verbs = factsheet.PLAIN_VERBS["camp"]
        by_owner = add(
            project, gpkg_layer(gpkg, "hotspots"),
            f"{factsheet.CELLS[sp.mode]} by administrator (1 km² {shape.label})",
            f"{grid_text}. The same cells as the flat-ground layer, coloured by who "
            f"administers the ground; {sp.short} % is in the attributes and in the "
            "hidden 'by flat ground %' layer.")
        by_owner.setRenderer(ownership_renderer(
            by_owner, reg, symbol=lambda o: cell_fill(o.color),
            label=lambda o: f"{o.short} - camping: {verbs[o.camp]}"))
        by_owner.setOpacity(0.6)

    # added after the cells so it draws on top: the gradient is transparent in the
    # middle, so it rims the parcel without hiding the cells inside it
    cand = add(project, gpkg_layer(gpkg, "candidates"),
               f"Eligible public parcels ({sp.short} %)",
               "One row per eligible public parcel, with the administrator, the managing "
               "unit to call, and how much mapped cover it carries. A screening result, "
               "not an authorization.")
    cand.setRenderer(graduated("species_pct", RAMP_PARCELS, sp, shapeburst))

    # The records themselves, on top of everything they generated, so the reader can see
    # how thin the evidence for a cell actually is.
    occ = gpkg_layer(gpkg, "occurrences")
    if occ.isValid() and occ.featureCount():
        add(project, occ, f"GBIF records ({occ.featureCount()})",
            "The occurrence records this screen was built from. They say where somebody "
            "looked and found, which is not where the plant is - absence of records is "
            "absence of records.")
        occ.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": RANGE, "outline_color": "#ffffff",
             "outline_width": "0.3", "size": "2"}
        )))

    # Above the cells and parcels, below the offices and the reader's own layers: the
    # sites are the reason to zoom in, and a cell drawn over them would hide the answer.
    sites = gpkg_layer(gpkg, "campsites")
    if sites.isValid() and sites.featureCount():
        add(project, sites, f"Established campsites ({sites.featureCount():,})",
            "Campsites from OpenStreetMap, the Forest Service and BLM. Filled stars are "
            "primitive sites, hollow diamonds developed campgrounds; faded markers sit on "
            "ground where camping is not open (private, tribal, refuges). OpenStreetMap is "
            "crowd-sourced, and a USFS 'dispersed camping' marker is an area rather than "
            "a pad. Markers only - the cells are not ranked by them.")
        sites.setRenderer(campsite_renderer())
        label_with(sites, "coalesce(\"name\", '')", expression=True, size=8)
        labeling = sites.labeling().settings()
        labeling.scaleVisibility = True
        labeling.maximumScale = 0
        labeling.minimumScale = SITE_LABEL_SCALE
        sites.setLabeling(QgsVectorLayerSimpleLabeling(labeling))

    offices = add(project, gpkg_layer(gpkg, "office_points"), "BLM offices",
                  "Where to ask. Field and district offices, labelled by unit name.")
    offices.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "#ffffff", "outline_color": "#000000",
         "outline_width": "0.4", "size": "3"}
    )))
    label_with(offices, "ADMU_NAME")

    # Both added last, so they draw over everything: a fact sheet nobody can find and a
    # note buried under a cell are the same as not having them.
    if about is not None:
        add(project, about, "About this map",
            "What this map is, what screened it, and what it does not model. One point, "
            "at the centre of the region - tap it.")
        about.setRenderer(marker("square", size="4", width="0.6"))
        label_with(about, "'About this map'", expression=True)
    add(project, notes, "Field notes (yours)",
        "The one layer here you may edit. What you found, what you looked for and did "
        "not find, and where - it lives in field_notes.gpkg beside this project, which "
        "nothing in the pipeline rewrites.")
    notes.setRenderer(notes_renderer())

    # --- desktop: absolute paths, nothing locked ------------------------------
    project.setFilePathStorage(Qgis.FilePathType.Absolute)
    project.write(str(qgs_path))
    print(f"-> {qgs_path.relative_to(paths.ROOT)} with {len(project.mapLayers())} layers")

    # --- portable: beside its data, relative paths, read-only ----------------
    # Written second because every change `portable()` makes is one-directional and
    # nothing here undoes them.
    portable(project, notes)
    qfield = paths.qfield_path(sp, reg)
    project.write(str(qfield))
    print(f"-> {qfield.relative_to(paths.ROOT)}  (copy {paths.out_dir(sp, reg).relative_to(paths.ROOT)}/ to the phone)")
    qgs.exitQgis()


if __name__ == "__main__":
    main()
