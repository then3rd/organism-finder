"""The lattice the scouting cells are cut from.

Standard library only - stage 05 imports this too, so the tiling maths is `math` and
lists rather than numpy, and stage 03 turns the rings into shapely polygons.

Cell *shape* is a cartographic choice, not a screening one: both lattices are laid over
the same domain, scored by the same pass and cut at the same threshold, so a run differs
only in how the answer is drawn. Which is why the shape is remembered rather than
re-derived - stages 04 and 05 describe the cells they were handed, and a summary that
says "hex" over a square map would be the pipeline disagreeing with itself.

`HOTSPOT_KM2` in scripts/03_overlay.py sets the area; a shape only decides how that area
tiles the plane.
"""
from dataclasses import dataclass
import math


def _span(start, stop, step):
    """`numpy.arange` for one axis, without numpy: half-open, so `stop` is never hit."""
    n = max(int(math.ceil((stop - start) / step)), 0)
    return [start + i * step for i in range(n)]


def _hex_rings(bounds, area_m2):
    """Flat-top hexagons of the given area tiling `bounds`."""
    r = math.sqrt(2 * area_m2 / (3 * math.sqrt(3)))     # centre -> vertex
    dx, dy = 1.5 * r, math.sqrt(3) * r                  # column pitch, row pitch
    corners = [(r * math.cos(a * math.pi / 3), r * math.sin(a * math.pi / 3))
               for a in range(6)]
    xmin, ymin, xmax, ymax = bounds
    rings = []
    for i, cx in enumerate(_span(math.floor(xmin / dx) * dx - dx, xmax + dx, dx)):
        offset = dy / 2 if i % 2 else 0.0
        for cy in _span(math.floor(ymin / dy) * dy - dy, ymax + dy, dy):
            rings.append([(cx + ox, cy + offset + oy) for ox, oy in corners])
    return rings


def _square_rings(bounds, area_m2):
    """Axis-aligned squares of the given area tiling `bounds`."""
    s = math.sqrt(area_m2)
    h = s / 2
    corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
    xmin, ymin, xmax, ymax = bounds
    rings = []
    for cx in _span(math.floor(xmin / s) * s - s, xmax + s, s):
        for cy in _span(math.floor(ymin / s) * s - s, ymax + s, s):
            rings.append([(cx + ox, cy + oy) for ox, oy in corners])
    return rings


@dataclass(frozen=True)
class Shape:
    name: str       # what you type: `just overlay junioste square`
    label: str      # the word that lands in the funnel, summary.md and the legend
    note: str       # why you would pick it
    tile: object    # callable(bounds, area_m2) -> list of coordinate rings


HEX = Shape(
    name="hex",
    label="hex",
    note=("every neighbour the same distance away and no dominant axis, so the "
          "lattice does not read as an artifact over the terrain"),
    tile=_hex_rings,
)
SQUARE = Shape(
    name="square",
    label="square",
    note=("axis-aligned, so cells line up with section lines, quad sheets and any "
          "other raster grid the reader is comparing against"),
    tile=_square_rings,
)

SHAPES = {s.name: s for s in (HEX, SQUARE)}
DEFAULT = HEX.name


def resolve(argv=(), default=DEFAULT):
    """Lattice named by argv[2] - the optional second argument, after the taxon slug."""
    name = argv[2] if len(argv) > 2 else default
    if name not in SHAPES:
        raise SystemExit(
            f"unknown grid shape {name!r}\nknown: {', '.join(SHAPES)}\n"
            "The shape is cartographic only - it changes how the cells tile, not what "
            "ground qualifies."
        )
    return SHAPES[name]


def remember(path, shape):
    """Record which lattice stage 03 laid, for the stages that only read its output."""
    path.write_text(shape.name + "\n")


def recall(path, default=DEFAULT):
    """Read it back. A run from before this file existed reads as the default."""
    try:
        name = path.read_text().strip()
    except OSError:
        return SHAPES[default]
    return SHAPES.get(name, SHAPES[default])


if __name__ == "__main__":
    print(f"  default: {DEFAULT}\n")
    for _s in SHAPES.values():
        print(f"  {_s.name:<7} {_s.note}")
    print(
        "\n  Pass one as the second argument: `just overlay junioste square`, or\n"
        "  `just all junioste square`. Stages 04 and 05 read the shape back from\n"
        "  out/<slug>/grid.txt, so they describe the cells stage 03 actually cut."
    )
