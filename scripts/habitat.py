"""What has to be true of the ground itself, beyond who owns it and what grows on it.

Standard library only - stage 05 imports this too.

The fourth axis. The first three ask who administers a place, whether it is the taxon's
country, and whether the taxon's community grows there. All three describe a *standing*
state of the world, which is enough for a plant: a juniper that is there in May is there
in October.

A fungus is not like that. Its mycelium is in ground the cover layer can describe - the
host stand - but whether it fruits there turns on a condition the cover layer knows
nothing about. Black morels come up in the first springs after a stand-replacing fire and
then stop; oysters want hardwood in a wet bottom. So the host community selects the
country and the condition selects the year and the site, and neither alone is an answer.

Two kinds ship:

  * `BURN`  - inside a fire perimeter whose year falls in a window of seasons back.
  * `WATER` - within a buffer of perennial streams and waterbodies.

`required` is the whole behavioural distinction:

  * True  - a geometric gate. Ground failing it is cut before the cover pass, and a
            funnel row records the cut. Use it when the condition is the reason the taxon
            is there at all, so that ground without it is not a weaker candidate but a
            wrong one.
  * False - a scoring column only, `<kind>_pct`, fed to the ranking. Use it when the
            condition helps and its absence does not disqualify.

The gate runs *before* the zonal pass, which is also why stage 03 does not get slower for
carrying this: a burn window over Utah cuts the parcel count by two orders of magnitude
and the expensive scoring never sees what it removed.
"""
from dataclasses import dataclass

BURN = "burn"
WATER = "water"
KINDS = (BURN, WATER)


@dataclass(frozen=True)
class Condition:
    kind: str
    # See the module docstring: gate, or column.
    required: bool = True

    # --- burn -----------------------------------------------------------------
    # Inclusive window of springs since the fire year. (1, 3) is "burned one to three
    # seasons ago", which is the black-morel flush and about where it stops being worth
    # the drive. Resolved against the current calendar year at run time, so a cached
    # perimeter download stays correct into the next season.
    seasons: tuple = (1, 3)

    # --- water ----------------------------------------------------------------
    # Buffer on perennial flowlines and waterbodies. Not a hydrological claim - a
    # statement about how far from water the taxon's site type is still plausible.
    metres: int = 400

    # Says what this condition means for this taxon and why the number is what it is.
    # Lands verbatim in summary.md, like Owner.authority.
    note: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown condition kind {self.kind!r}; known: {', '.join(KINDS)}"
            )
        if self.kind == BURN and self.seasons[0] > self.seasons[1]:
            raise ValueError(f"burn window {self.seasons} runs backwards")

    @property
    def column(self):
        """The scoring column this condition contributes. Present in either mode - a
        gated condition still reports how much of the feature satisfied it, because
        "just inside the perimeter" and "wholly burned" are different places."""
        return f"{self.kind}_pct"

    def label(self, year=None):
        """Funnel row and legend text. `year` is the current calendar year; a burn
        window is meaningless without it, so the label carries it rather than leaving
        the reader to work out what "two seasons" was measured from."""
        if self.kind == BURN:
            lo, hi = self.seasons
            span = f"{lo}-{hi} season(s) ago"
            if year is not None:
                span += f" (fire years {year - hi}-{year - lo})"
            return f"burned {span}"
        return f"within {self.metres} m of perennial water"


def kinds(conditions):
    """Which condition kinds a taxon uses, in registry order and de-duplicated.

    Stage 01 downloads on this rather than on the taxon, so a taxon that wants no burn
    layer never pays for one - the same shape as `Taxon.needs_raster`.
    """
    seen = []
    for c in conditions:
        if c.kind not in seen:
            seen.append(c.kind)
    return seen


def gates(conditions):
    return [c for c in conditions if c.required]


def scores(conditions):
    """Conditions that only rank. These, in order, are what `rank_order` sorts by first."""
    return [c for c in conditions if not c.required]


if __name__ == "__main__":
    import datetime

    _year = datetime.date.today().year
    print(f"  condition kinds: {', '.join(KINDS)}\n")
    for _c in (Condition(BURN), Condition(BURN, required=False), Condition(WATER)):
        _how = "gate  " if _c.required else "score "
        print(f"  {_c.kind:<7} {_how} {_c.label(_year)}")
    print(
        "\n  A gate cuts the ground and adds a funnel row; a score only adds a\n"
        "  <kind>_pct column and sorts by it. Conditions are set per taxon in\n"
        "  scripts/species.py - `just species` shows which taxa carry them."
    )
