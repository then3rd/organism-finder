"""Which tree to screen for.

Standard library only - stage 05 imports this too.

Two facts drive a run and neither is derivable from the other:

  * `slug`        - Little (1971) mapped every tree in the USTreeAtlas by an 8-char
                    slug (genus4 + species4), so the range polygon is a URL swap.
  * `evt_include` - LANDFIRE EVT class names name a plant *community*, not a species,
                    so which classes count is a judgment call per tree. Derive it with
                    `just evt-classes <keyword>` before adding an entry here; a species
                    with no EVT signal at all cannot be screened by this pipeline.

`range_url` 404s and an empty EVT match are both hard errors upstream - the failure
mode this registry exists to prevent is a plausible-looking empty map.
"""
from dataclasses import dataclass, field

ATLAS = "https://raw.githubusercontent.com/wpetry/USTreeAtlas/master/geojson"


@dataclass(frozen=True)
class Species:
    slug: str
    binomial: str
    common_name: str
    # One lowercase word for legends and table headers: "cell 25-40 % juniper".
    short: str
    evt_include: tuple
    evt_exclude: tuple = ()
    # Polygon count in Little's shapefile, asserted at fetch time when known so a
    # truncated download fails loudly. None = report the count and carry on.
    little_polygons: int | None = None
    # Appended to the ground-truthing bullet in summary.md. Say what the EVT classes
    # actually contain, because the answer differs sharply per tree.
    ground_truth_caveat: str = ""

    @property
    def range_url(self):
        return f"{ATLAS}/{self.slug}.geojson"

    def matches(self, evt_name):
        name = evt_name.lower()
        if any(x.lower() in name for x in self.evt_exclude):
            return False
        return any(k.lower() in name for k in self.evt_include)


SPECIES = {s.slug: s for s in (
    Species(
        slug="junioste",
        binomial="Juniperus osteosperma",
        common_name="Utah juniper",
        short="juniper",
        evt_include=("juniper",),
        little_polygons=161,
        ground_truth_caveat=(
            "Great Basin and Colorado Plateau pinyon-juniper types contain pinyon pine "
            "and, at the margins, *J. scopulorum* and *J. monosperma*."
        ),
    ),
    Species(
        slug="pinuedul",
        binomial="Pinus edulis",
        common_name="two-needle pinyon",
        short="pinyon",
        # The same five pinyon-juniper communities juniper draws on, minus the four
        # juniper-only classes. Overlap with junioste is expected and correct: it is
        # Little's range polygon, not EVT, that separates the two maps.
        evt_include=("pinyon",),
        ground_truth_caveat=(
            "The pinyon-juniper classes are named for the community, not the tree - much "
            "of that acreage is more juniper than pinyon, and at the southwestern margin "
            "*P. monophylla* replaces *P. edulis*."
        ),
    ),
    Species(
        slug="pinupond",
        binomial="Pinus ponderosa",
        common_name="ponderosa pine",
        short="ponderosa",
        # Jeffrey Pine-(Ponderosa Pine) is a Jeffrey-dominated Californian class; the
        # parenthetical is the giveaway.
        evt_include=("ponderosa",),
        evt_exclude=("jeffrey",),
        ground_truth_caveat=(
            "Ponderosa is mapped at the woodland scale - mixed-conifer stands carrying "
            "scattered ponderosa are not broken out as ponderosa."
        ),
    ),
    Species(
        slug="quergamb",
        binomial="Quercus gambelii",
        common_name="Gambel oak",
        short="oak",
        # One class, and it is a shrubland: bare "oak" would drag in 50 classes from
        # the Appalachians to the Central Valley.
        evt_include=("gambel oak",),
        ground_truth_caveat=(
            "Gambel oak maps as a *shrubland* class - most of that acreage is "
            "multi-stemmed clonal thicket rather than tree-form oak."
        ),
    ),
    Species(
        slug="poputrem",
        binomial="Populus tremuloides",
        common_name="quaking aspen",
        short="aspen",
        evt_include=("aspen",),
        ground_truth_caveat=(
            "Aspen is clonal - a mapped stand is one genet's ramets, and stem size "
            "varies more within a stand than the class name suggests."
        ),
    ),
)}

DEFAULT = "junioste"


def resolve(argv=(), default=DEFAULT):
    """Species named by argv[1], defaulting to the tree this pipeline started with."""
    slug = argv[1] if len(argv) > 1 else default
    if slug not in SPECIES:
        raise SystemExit(
            f"unknown species {slug!r}\nknown: {', '.join(sorted(SPECIES))}\n"
            "Add one to scripts/species.py - `just evt-classes <keyword>` finds the "
            "LANDFIRE classes it should match."
        )
    return SPECIES[slug]


if __name__ == "__main__":
    for _s in SPECIES.values():
        _d = " (default)" if _s.slug == DEFAULT else ""
        print(f"  {_s.slug:10} {_s.common_name:<20} {_s.binomial}{_d}")
