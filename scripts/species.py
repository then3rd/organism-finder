"""Which plant to screen for.

Standard library only - stage 05 imports this too.

Started as a tree registry and the shape still shows: fourteen of these are trees with a
Little (1971) range map and a LANDFIRE class. But "where does this plant grow on public
land" is the same question for an orchid, and an orchid has neither of those things, so
the two things a tree entry bundled together are now separate fields:

  * `range_source` - what says this is the plant's country at all.
      "little"     Little (1971) USTreeAtlas polygon, a URL swap on an 8-char slug
                   (genus4 + species4). Trees only; the atlas maps nothing else.
      "gbif"       Georeferenced occurrence records, buffered by their own stated
                   coordinate uncertainty. What you use when nobody drew a range map.
      None         No range filter - the region is the range. Honest for a plant that
                   is genuinely everywhere in the region, and only for those.

  * `cover`        - what says it actually grows on *this* ground.
      "evt"        LANDFIRE EVT classes selected by `evt_include`. Derive them with
                   `just evt-classes <keyword>` before adding an entry. EVT names woody
                   plant *communities*, so this works for trees and shrubs and fails
                   completely for anything herbaceous.
      "occurrence" Distance to a documented occurrence. Coarse, sparse and honest: it
                   says "somebody found one here", not "this is modelled cover".

  * `mode`         - what the map is for. "collect" produces permit tables and dig
                   targets; "observe" drops the permit framing, keeps Wilderness and
                   national parks in scope rather than excluding them, and says plainly
                   that the plant is to be looked at. Orchids are observe.

A `range_url` 404, an empty EVT match and a taxon key with no records in the region are
all hard errors upstream - the failure mode this registry exists to prevent is a
plausible-looking empty map.
"""
from dataclasses import dataclass

ATLAS = "https://raw.githubusercontent.com/wpetry/USTreeAtlas/master/geojson"

LITTLE, GBIF = "little", "gbif"
EVT, OCCURRENCE = "evt", "occurrence"
COLLECT, OBSERVE = "collect", "observe"


@dataclass(frozen=True)
class Taxon:
    slug: str
    binomial: str
    common_name: str
    # One lowercase word for legends and table headers: "cell 25-40 % juniper".
    short: str
    evt_include: tuple = ()
    evt_exclude: tuple = ()
    # Polygon count in Little's shapefile, asserted at fetch time when known so a
    # truncated download fails loudly. None = report the count and carry on.
    little_polygons: int | None = None
    # Appended to the ground-truthing bullet in summary.md. Say what the underlying data
    # actually contains, because the answer differs sharply per plant.
    ground_truth_caveat: str = ""

    # --- the three axes, defaulted so the fourteen tree entries below say nothing ---
    kind: str = "tree"
    range_source: str | None = LITTLE
    cover: str = EVT
    mode: str = COLLECT

    # --- occurrence screening -------------------------------------------------
    # GBIF taxon key: `https://api.gbif.org/v1/species/match?name=<binomial>`. Pinned as
    # an integer rather than looked up by name so a taxonomic reshuffle upstream cannot
    # silently repoint the query at a different plant.
    gbif_key: int | None = None
    # Records looser than this are dropped. GBIF carries observations with 28 km stated
    # uncertainty, which as a "range" polygon is a lie the size of a county.
    max_uncertainty_m: int = 2000
    # How far from a record the plant is taken to plausibly be. Not a home range - a
    # statement about how precisely a person can be pointed at a patch on the ground.
    occurrence_buffer_m: int = 1000
    # Listed, dug, or both. A label carried onto summary.md, not a filter on the data:
    # the coordinates and the waypoint file are the same ones every other taxon gets.
    sensitive: bool = False

    def __post_init__(self):
        if self.cover == EVT and not self.evt_include:
            raise ValueError(f"{self.slug}: cover='evt' needs evt_include keywords")
        if self.range_source == LITTLE and self.kind != "tree":
            raise ValueError(f"{self.slug}: Little's atlas maps trees only")
        if (self.range_source == GBIF or self.cover == OCCURRENCE) and not self.gbif_key:
            raise ValueError(f"{self.slug}: occurrence screening needs a gbif_key")

    @property
    def range_url(self):
        return f"{ATLAS}/{self.slug}.geojson"

    @property
    def needs_landfire(self):
        """Stage 02 is a no-op for a plant LANDFIRE cannot see."""
        return self.cover == EVT

    def matches(self, evt_name):
        name = evt_name.lower()
        if any(x.lower() in name for x in self.evt_exclude):
            return False
        return any(k.lower() in name for k in self.evt_include)


# The pipeline was a tree pipeline and most callers still say "species"; both names refer
# to the same objects, so neither import site had to change.
Species = Taxon

TAXA = {t.slug: t for t in (
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    Taxon(
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
    # --- shrubs: LANDFIRE sees them, Little never mapped them ------------------
    # The first entries with no range map at all. Big sagebrush occupies most of the
    # Great Basin and Colorado Plateau, so a range polygon would be a tracing of the
    # region and would narrow nothing; EVT is doing the whole job here, which is exactly
    # the case `range_source=None` exists for. Do not reach for it to paper over a
    # missing range map for a plant that is genuinely patchy.
    Taxon(
        slug="artetrid",
        binomial="Artemisia tridentata",
        common_name="big sagebrush",
        short="sagebrush",
        kind="shrub",
        range_source=None,
        # Bare "sagebrush" matches eight Utah classes and three of them are the wrong
        # plant: the Colorado Plateau "Mixed Low" and Great Basin "Xeric Mixed" types are
        # black and low sagebrush (*A. nova*, *A. arbuscula*), which are different shrubs
        # on different soils. Naming big sagebrush directly, plus the montane steppe that
        # is ssp. vaseyana under another name, gets the three classes that are this plant.
        evt_include=("big sagebrush", "montane sagebrush"),
        ground_truth_caveat=(
            "Several subspecies are lumped here - Wyoming, basin and mountain big "
            "sagebrush occupy different sites and EVT does not separate them, so read a "
            "mapped stand as *A. tridentata* sensu lato. Much of the mapped acreage is "
            "also post-fire cheatgrass with relict sagebrush rather than intact stand."
        ),
    ),

    # --- orchids: neither a range map nor an EVT class ------------------------
    # Every one of these is observe-mode, and that is not squeamishness. Utah's orchids
    # are mycoheterotrophic or nearly so - they depend on a soil fungus that does not
    # come up with the plant - so a transplanted one dies. There is nothing to permit,
    # which makes "where can I legally dig this" the wrong question and "where can I go
    # look at one" the right one. Observe mode is what makes national parks and
    # Wilderness appear on these maps instead of being subtracted from them.
    #
    # Cover is occurrence-based because LANDFIRE names woody communities: no EVT class in
    # Utah names any herb, so `just evt-classes orchid` returns nothing and always will.
    Taxon(
        slug="calybulb",
        binomial="Calypso bulbosa",
        common_name="fairy slipper",
        short="calypso",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=5323572,
        # Small, spectacular, shallow-rooted and famously killed by being picked.
        sensitive=True,
        ground_truth_caveat=(
            "Flowers for perhaps two weeks after snowmelt and is invisible the rest of "
            "the year, so an occurrence records a *visit* as much as a plant. It grows "
            "in deep conifer duff and dies if disturbed - it cannot be transplanted at "
            "all, by permit or otherwise."
        ),
    ),
    Taxon(
        slug="coramacu",
        binomial="Corallorhiza maculata",
        common_name="spotted coralroot",
        short="coralroot",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=2797273,
        ground_truth_caveat=(
            "Wholly mycoheterotrophic - no chlorophyll, no leaves, and it lives on a "
            "fungus that lives on conifer roots. The visible stem is a flowering shoot "
            "off an underground rhizome, so a record marks a colony, not an individual."
        ),
    ),
    Taxon(
        slug="platdila",
        binomial="Platanthera dilatata",
        common_name="white bog orchid",
        short="bog orchid",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=2797036,
        # The best-recorded Utah orchid, and wetland-obligate, so the records cluster
        # tightly on seeps and streambanks rather than smearing over a mountain range.
        occurrence_buffer_m=750,
        ground_truth_caveat=(
            "Strictly a wet-ground plant - seeps, springs, streambanks and wet meadows. "
            "The 1 km cell is far coarser than the habitat, which is often a strip a few "
            "metres wide, so treat a cell as 'walk this drainage', not 'dig here'."
        ),
    ),
    Taxon(
        slug="epipgiga",
        binomial="Epipactis gigantea",
        common_name="stream orchid",
        short="stream orchid",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=8144712,
        occurrence_buffer_m=750,
        ground_truth_caveat=(
            "A hanging-garden and seep plant in southern Utah, so records concentrate on "
            "canyon walls and springs. Many sit in national parks and monuments, which "
            "this map includes precisely because looking is what is on offer there."
        ),
    ),
    Taxon(
        slug="goodoblo",
        binomial="Goodyera oblongifolia",
        common_name="western rattlesnake plantain",
        short="rattlesnake plantain",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=2840754,
        ground_truth_caveat=(
            "The evergreen mottled rosette is present year-round and far easier to find "
            "than the flower spike, so this is the one Utah orchid worth looking for out "
            "of season."
        ),
    ),
    Taxon(
        slug="cyprfasc",
        binomial="Cypripedium fasciculatum",
        common_name="clustered lady's slipper",
        short="lady's slipper",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=2820361,
        sensitive=True,
        ground_truth_caveat=(
            "A BLM and Forest Service sensitive species with very few Utah records - "
            "thirty-odd, several of them historical. Slipper orchids are the most dug-up "
            "genus in North America, so weigh who sees these coordinates."
        ),
    ),
    Taxon(
        slug="spirdilu",
        binomial="Spiranthes diluvialis",
        common_name="Ute ladies'-tresses",
        short="ladies'-tresses",
        kind="forb",
        range_source=GBIF, cover=OCCURRENCE, mode=OBSERVE,
        gbif_key=2805372,
        sensitive=True,
        # Fewer precise records than any other entry, and the ones that exist are on
        # riparian ground that moves. A wide buffer here is honesty, not generosity.
        occurrence_buffer_m=1500,
        ground_truth_caveat=(
            "**Federally listed as threatened under the Endangered Species Act.** Taking "
            "it is a federal offence wherever it grows, on public land or private. It is "
            "included here so that the ground it occupies can be recognised and avoided, "
            "and the mapped cells are deliberately coarse. Report sightings to the Utah "
            "Natural Heritage Program rather than acting on them."
        ),
    ),
)}

# Callers written when this was a tree-only
# registry still say SPECIES; it is the same dict.
SPECIES = TAXA

DEFAULT = "junioste"


def resolve(argv=(), default=DEFAULT):
    """Taxon named by argv[1], defaulting to the tree this pipeline started with."""
    slug = argv[1] if len(argv) > 1 else default
    if slug not in SPECIES:
        raise SystemExit(
            f"unknown species {slug!r}\nknown: {', '.join(sorted(SPECIES))}\n"
            "Add one to scripts/species.py - `just evt-classes <keyword>` finds the "
            "LANDFIRE classes a woody plant should match; a plant LANDFIRE cannot see "
            "needs a gbif_key and cover='occurrence' instead."
        )
    return SPECIES[slug]


if __name__ == "__main__":
    _how = {("little", "evt"): "Little x EVT", (None, "evt"): "EVT only",
            ("gbif", "occurrence"): "GBIF records"}
    for _t in TAXA.values():
        _d = " (default)" if _t.slug == DEFAULT else ""
        _s = "  sensitive" if _t.sensitive else ""
        print(f"  {_t.slug:10} {_t.kind:<6} {_t.mode:<8} "
              f"{_how.get((_t.range_source, _t.cover), '?'):<13} "
              f"{_t.common_name:<28} {_t.binomial}{_d}{_s}")
