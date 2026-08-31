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
    # --- shared-class congeners -------------------------------------------------
    # These draw on exactly the classes an existing species already uses. That is the
    # pinyon/juniper situation and it is correct: EVT names communities, so it cannot
    # separate congeners. Little's range polygon is the only thing that separates the maps.
    Species(
        slug="juniscop",
        binomial="Juniperus scopulorum",
        common_name="Rocky Mountain juniper",
        short="juniper",
        evt_include=("juniper",),
        little_polygons=150,
        ground_truth_caveat=(
            "No EVT class names *J. scopulorum* - these are the same juniper communities "
            "*J. osteosperma* selects, and over most of Utah osteosperma is the commoner "
            "tree in them. Expect scopulorum on the cooler, moister end: higher, north "
            "aspects and drainage bottoms."
        ),
    ),
    Species(
        slug="pinumono",
        binomial="Pinus monophylla",
        common_name="singleleaf pinyon",
        short="pinyon",
        evt_include=("pinyon",),
        little_polygons=133,
        ground_truth_caveat=(
            "The same pinyon-juniper classes *P. edulis* selects. The two split roughly at "
            "the Great Basin / Colorado Plateau line and hybridise where they meet, so on "
            "the western half of the state read a mapped stand as monophylla and on the "
            "eastern half as edulis - EVT will not tell you which."
        ),
    ),
    Species(
        slug="piceenge",
        binomial="Picea engelmannii",
        common_name="Engelmann spruce",
        short="spruce",
        evt_include=("spruce-fir",),
        little_polygons=88,
        ground_truth_caveat=(
            "Spruce-fir is one class carrying both dominants - it cannot separate "
            "*Picea engelmannii* from *Abies lasiocarpa*, which is why both are registered "
            "against it. Their Little ranges are nearly co-extensive in Utah, so unlike the "
            "pinyon and juniper pairs the range polygon barely separates these two either."
        ),
    ),
    Species(
        slug="abielasi",
        binomial="Abies lasiocarpa",
        common_name="subalpine fir",
        short="fir",
        evt_include=("spruce-fir",),
        little_polygons=108,
        ground_truth_caveat=(
            "The same spruce-fir class *Picea engelmannii* selects, and the class name is "
            "the honest answer: these two share the canopy. Subalpine fir tends to the "
            "moister, more sheltered part of a stand, but not separably at 30 m."
        ),
    ),

    # --- classes of their own ---------------------------------------------------
    Species(
        slug="pseumenz",
        binomial="Pseudotsuga menziesii",
        common_name="Douglas-fir",
        short="douglas-fir",
        # One Utah class names the tree; most Utah Douglas-fir is in the two Southern Rocky
        # Mountain mixed-conifer types, where it is the characteristic dominant. Taking the
        # named class alone maps about a fifteenth of the tree. "aspen" drops
        # Inter-Mountain Basins Aspen-Mixed Conifer, which is aspen-led.
        evt_include=("douglas-fir", "mixed conifer"),
        evt_exclude=("aspen",),
        little_polygons=245,
        ground_truth_caveat=(
            "Most of this acreage is mixed conifer, a class that names no species at all. "
            "Douglas-fir is its characteristic dominant in Utah, but white fir, ponderosa "
            "and blue spruce are in there too - a mixed-conifer cell is a good bet for "
            "Douglas-fir, not a guarantee of it."
        ),
    ),
    Species(
        slug="pinucont",
        binomial="Pinus contorta",
        common_name="lodgepole pine",
        short="lodgepole",
        evt_include=("lodgepole",),
        little_polygons=882,
        ground_truth_caveat=(
            "Utah lodgepole is a Uinta tree and nearly all of it is National Forest, not "
            "BLM. A thin result here is the real answer rather than a screening failure."
        ),
    ),
    Species(
        slug="pinuflex",
        binomial="Pinus flexilis",
        common_name="limber pine",
        short="limber",
        evt_include=("limber",),
        little_polygons=112,
        ground_truth_caveat=(
            "Two of the three classes pair limber pine with something else - juniper at the "
            "foothill end, bristlecone at the subalpine end - so a mapped stand may carry "
            "very little *P. flexilis*."
        ),
    ),
    Species(
        slug="pinulong",
        binomial="Pinus longaeva",
        common_name="Great Basin bristlecone pine",
        short="bristlecone",
        evt_include=("bristlecone",),
        little_polygons=31,
        ground_truth_caveat=(
            "Both bristlecone classes are shared with limber pine and the acreage is small "
            "and high. Bristlecone is also a very slow-growing, long-lived tree on public "
            "land, so a permit here is a different conversation from one for juniper."
        ),
    ),
    Species(
        slug="acergran",
        binomial="Acer grandidentatum",
        common_name="bigtooth maple",
        short="maple",
        evt_include=("bigtooth maple",),
        little_polygons=62,
        ground_truth_caveat=(
            "One class, and it names the tree - the cleanest signal in this registry. "
            "It is a *ravine* woodland, so the mapped ground is steep and narrow and the "
            "1 km2 cell is coarse against it."
        ),
    ),
    Species(
        slug="cercledi",
        binomial="Cercocarpus ledifolius",
        common_name="curl-leaf mountain mahogany",
        short="mahogany",
        # Both classes: EVT files the woodland under Conifer physiognomy and splits off a
        # shrubland for poor sites. Curl-leaf mahogany is genuinely both.
        evt_include=("mahogany",),
        little_polygons=132,
        ground_truth_caveat=(
            "EVT files the woodland class under *Conifer* physiognomy; curl-leaf mahogany "
            "is a rose-family broadleaf, not a conifer, and the label is a physiognomic "
            "convenience. On poor sites it is a shrub rather than a tree, which is what the "
            "second, shrubland class is."
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
