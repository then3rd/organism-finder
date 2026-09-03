"""What the deliverables say in words, as opposed to what they say in geometry.

Standard library only - stage 05 imports this under the system interpreter, where
geopandas does not exist. `textwrap`, `datetime`, and the three registries.

Every sentence here appears in at least two deliverables, and that is the whole reason
the module exists. `summary.md` and the map were written independently and had already
drifted: stage 05 built its project title from a two-way conditional, so a mushroom map
was titled "where to go and look" while the summary beside it said "where to go and
pick". Two stages writing the same sentence from the same registry is how that happens,
and the fix is the one this repo already applies to colour - the prose lives on one
object, so the map and the tables cannot disagree.

Markdown lives here because `summary.md` is the older caller and the richer format. QGIS
renders a layer abstract as plain text, so the `*_plain` helpers strip the emphasis and
un-wrap the hand-wrapped bullets rather than a second copy of the sentences being kept in
plain form - a second copy is exactly what this module exists to end.
"""
from pathlib import Path
import datetime
import sys
import textwrap

sys.path.insert(0, str(Path(__file__).resolve().parent))
import habitat  # noqa: E402
import ownership  # noqa: E402
import species as species_mod  # noqa: E402


def title(sp):
    return sp.common_name[:1].upper() + sp.common_name[1:]


# What the document is for, keyed by mode. Every one of these dicts was written out
# separately in stage 04 or stage 05 before it moved here; a mode is a taxon-registry
# fact and the deliverables only report it.
WHAT = {
    "collect": "transplant permit screening",
    "observe": "where to go and look",
    "forage": "where to go and pick",
}
# The scouting-grid layer name on the map.
CELLS = {"collect": "Scouting cells", "observe": "Viewing cells",
         "forage": "Foraging cells"}
# "## 25 places to scout"
GOING = {"collect": "to scout", "observe": "to go and look",
         "forage": "to go and pick"}
# The waypoint file's own name for what it is.
ACTIVITY = {"collect": "scouting", "observe": "viewing", "forage": "foraging"}


def project_title(sp, reg):
    return f"{title(sp)} on {reg.name} public land - {WHAT[sp.mode]}"


def heading(sp, reg):
    return f"# {project_title(sp, reg)}"


def exclusion_label(sp):
    """Wilderness / WSA / NM-NCA are subtracted in one mode and kept in the other two."""
    return ("Excluded: Wilderness / WSA / NM-NCA" if sp.mode == species_mod.COLLECT
            else "Wilderness / WSA / NM-NCA (open to visit)")


def how(sp, samples=None, year=None):
    """The methodology sentence: which sources this taxon was screened from, in markdown.

    `samples` is the occurrence-record count, which only stage 04 can know because only it
    has the candidates frame. Stage 05 passes nothing and the sentence drops the count
    rather than inventing one.
    """
    year = year or datetime.date.today().year
    records = (f"**{samples:,} georeferenced GBIF records**" if samples is not None
               else "**Georeferenced GBIF records**")
    text = {
        (species_mod.LITTLE, species_mod.EVT):
            f"**Little 1971 *{sp.binomial}* range** (species filter) x "
            f"**LANDFIRE EVT 30 m** (where {sp.short} actually grows)",
        (None, species_mod.EVT):
            f"**LANDFIRE EVT 30 m** (where {sp.short} actually grows). There is no range "
            "filter: this plant occupies the region broadly enough that a range polygon "
            "would narrow nothing",
        (species_mod.GBIF, species_mod.OCCURRENCE):
            f"{records} buffered by "
            f"their own stated accuracy (at least {sp.occurrence_buffer_m} m). This is a "
            "record of where people have *looked and found*, not modelled cover",
    }.get((sp.range_source, sp.cover), "the sources named in scripts/species.py")

    if sp.kind == "fungus":
        # The EVT class names the host stand, and saying "where the fungus grows" here
        # would be the one sentence in this document that is flatly untrue.
        text = (f"**LANDFIRE EVT 30 m** for the *host* community - the stand this fungus "
                f"fruits in, not the fungus, which no vegetation model maps")
    if sp.conditions:
        text += ", and " + " and ".join(
            f"**{c.label(year)}**" if c.required else f"scored by *{c.label(year)}*"
            for c in sp.conditions
        )
    return text


def method(sp, reg, samples=None, year=None):
    """`how()` as the whole sentence, the way summary.md opens."""
    return (f"Cross-reference of **{reg.name} Surface Management Agency** polygons (who "
            f"administers the ground) x {how(sp, samples, year)}.")


# --- the axes, one line each -------------------------------------------------
# These name what a reader is looking at rather than narrating method, because on a phone
# the question is "what is this map?" and the answer has to fit on a screen.

def range_text(sp):
    if sp.range_source == species_mod.LITTLE:
        return f"Little 1971 atlas range polygon for {sp.binomial} (1:2,000,000)"
    if sp.range_source == species_mod.GBIF:
        return (f"GBIF occurrence records, taxon key {sp.gbif_key}, buffered by each "
                f"record's own stated accuracy - at least {sp.occurrence_buffer_m} m, and "
                f"records looser than {sp.max_uncertainty_m} m dropped")
    return ("none - the region is the range, which is only honest for a plant that "
            "occupies it broadly")


def cover_text(sp):
    if sp.cover == species_mod.EVT:
        whose = "host community" if sp.kind == "fungus" else "community"
        text = (f"LANDFIRE EVT 30 m modelled cover, {whose} keywords: "
                + ", ".join(sp.evt_include))
        if sp.evt_exclude:
            text += "; excluding " + ", ".join(sp.evt_exclude)
        return text
    return ("distance to a georeferenced record - the same buffers the range is built "
            "from, so the two are one source and not two opinions")


def conditions_text(sp, year=None):
    """Gate or score, per condition, in the reader's words rather than the field name."""
    if not sp.conditions:
        return "none"
    year = year or datetime.date.today().year
    return "; ".join(
        f"{c.label(year)} ({'required' if c.required else 'preferred, ranks only'})"
        for c in sp.conditions
    )


def owner_note(sp, codes):
    """The per-owner authority paragraph. This is the part that changes with ownership.

    Which of the two taking fields is consulted is the mode's to decide, and the two give
    different answers on the same ground: the Forest Service will not let you dig a tree
    without paperwork and will let you fill a bag with morels without any.
    """
    field = ownership.taking(sp.mode) or "collect"
    question = ("may mushrooms be taken?" if sp.mode == species_mod.FORAGE
                else "may a plant be taken?")
    lines = ["", "## Who administers it, and what that means", "",
             f"| administrator | ground | {question} |", "|---|---|---|"]
    for _, o in ownership.summarize(codes):
        verb = {ownership.FREE: "yes, personal use, no permit",
                ownership.PERMIT: "yes, with a permit",
                ownership.ASK: "case by case - ask first",
                ownership.PROHIBITED: "no"}[getattr(o, field)]
        lines.append(f"| {o.name} ({o.short}) | {o.tenure} | {verb} |")
    lines.append("")
    if sp.mode == species_mod.OBSERVE:
        # The column is still worth printing in observe mode - it is the reason this is
        # an observe-mode taxon on half these owners - but it is not what the map is for.
        lines += [
            f"The last column is context, not an invitation: {title(sp)} is screened in",
            "observe mode and nothing here is a suggestion to take one. See *Before you go*.",
            "",
        ]
    if sp.mode == species_mod.FORAGE:
        lines += [
            "Personal-use limits and free-use permit rules are set forest by forest and",
            "field office by field office, and they change from year to year. The bullets",
            "below say who to ask; none of them is a substitute for asking.",
            "",
        ]
    for _, o in ownership.summarize(codes):
        lines.append(textwrap.fill(
            f"* **{o.short}** - {ownership.authority_for(o, sp.mode)}.",
            width=90, subsequent_indent="  ",
            break_on_hyphens=False, break_long_words=False))
    return lines


def owner_plain(sp, codes):
    """The same answers as one plain block: who administers it, and what may be taken."""
    field = ownership.taking(sp.mode) or "collect"
    verbs = {ownership.FREE: "personal use, no permit",
             ownership.PERMIT: "with a permit",
             ownership.ASK: "case by case - ask first",
             ownership.PROHIBITED: "no"}
    out = []
    for _, o in ownership.summarize(codes):
        answer = ("" if sp.mode == species_mod.OBSERVE
                  else f" - taking: {verbs[getattr(o, field)]}")
        out.append(f"{o.short} ({o.name}, {o.tenure}){answer}")
    return "\n".join(out)


def caveats(sp, reg):
    """The closing section. Says what the screening does not model, in either mode."""
    head = {"collect": "## Before you dig", "observe": "## Before you go",
            "forage": "## Before you pick"}[sp.mode]
    lines = ["", head, ""]
    if sp.mode == species_mod.FORAGE:
        lines += [
            "* **This map finds habitat, not mushrooms, and it identifies nothing.** Every",
            "  cell on it is a place the host stand and the conditions line up; whether",
            "  anything is fruiting there this week is weather, and what you have picked is a",
            "  question for a key and an expert, never for a map. *Gyromitra* comes up in the",
            "  same burns as black morels and has killed people; *Omphalotus* grows on the",
            "  same hardwood as oysters. Never eat a wild mushroom on the strength of where",
            "  you found it.",
            "* Personal use only. Every `free` in the table above means personal-use",
            "  quantities - selling what you pick is a different permit on every agency here,",
            "  and picking commercially without one is theft of public property.",
            "* Wilderness, WSAs and monuments are *included* rather than subtracted: picking",
            "  for the pot is lawful in them. What is closed to a forager is closed by",
            "  administrator instead, and those owners are already out of this document.",
            "* Cut or pinch, take what you will eat, and leave the duff and the dead wood",
            "  the way you found them - the organism is the ground, not what you carried out.",
        ]
    elif sp.mode == species_mod.COLLECT:
        lines += [
            "* Call the administering unit first - and note that a permit from one agency is",
            "  worth nothing on another's ground, so check whose parcel you are actually on.",
            "* This screening does not model ACEC boundaries, grazing or mineral leases,",
            "  rights-of-way, sage-grouse habitat closures, developed recreation sites,",
            "  riparian buffers, or cultural-resource restrictions.",
        ]
    else:
        lines += [
            "* **This is a looking map, not a collecting map.** Every taxon screened in",
            "  observe mode is here because taking it is either unlawful, futile, or both -",
            "  Utah's orchids depend on soil fungi that do not come up with the plant, so a",
            "  dug one dies whatever the paperwork says.",
            "* Wilderness, WSAs and national monuments are *included* here rather than",
            "  subtracted, because walking into them to look is exactly what they are for.",
            "  Ground rules still differ by unit - check before you drive.",
            "* Stay on the trail where there is one, photograph rather than pick, and do not",
            "  clear vegetation for the shot.",
        ]
    if any(c.kind == habitat.BURN for c in sp.conditions):
        lines += [
            "* **A recent burn is a hazard, not just habitat.** Standing dead trees come down",
            "  without warning in wind, ash pits stay hot under a crust for months, and the",
            "  road you can see on the imagery may have washed out in the first storm after",
            "  the fire. Many burns are also under a BAER closure order for one to three",
            "  seasons, which is exactly the window this map selects and which it does not",
            "  model - check the administering unit's closures before you drive.",
            "* The perimeter says a fire happened, not how hot it burned. This screening does",
            "  not read burn severity, and it counts unburned islands inside a perimeter as",
            "  burned, so treat a cell as a place to look rather than a place with morels.",
        ]
    for c in sp.conditions:
        if c.note:
            lines.append(textwrap.fill(
                f"* **{c.label(datetime.date.today().year)}** - {c.note}.",
                width=90, subsequent_indent="  ",
                break_on_hyphens=False, break_long_words=False))
    if sp.sensitive:
        lines += [
            f"* **{title(sp)} is flagged sensitive.** It is listed, dug, or both, and the",
            "  coordinates in these files are full precision, like every other taxon's.",
            "  Treat them accordingly: do not repost the waypoints, and do not lead anyone",
            "  to a patch you would not want dug.",
        ]
    src = ("LANDFIRE EVT is 30 m *modelled* cover, and for a fungus it is describing the "
           "host stand rather than the organism - the percentage on this map is host "
           "cover, and the fungus may be in none of it."
           if sp.kind == "fungus" else
           "Little's range map is 1:2,000,000 (1971) and LANDFIRE EVT is 30 m *modelled* "
           "cover. Both are screening tools."
           if sp.cover == species_mod.EVT else
           "Occurrence records say where somebody looked and found, which is not where the "
           "plant is. These species are small, briefly visible and unevenly searched - "
           "absence of records is absence of records.")
    lines.append(textwrap.fill(
        "* " + src + " Ground-truth "
        + ("this before acting on it - " if sp.kind == "fungus"
           else "the species before acting on this - ")
        + sp.ground_truth_caveat,
        width=90, subsequent_indent="  ",
        break_on_hyphens=False, break_long_words=False))
    lines += [
        "* Lands with wilderness characteristics (`in_lwc`) are not closed, but expect scrutiny.",
        "* The `other_cells` layer in the GeoPackage grids the same screen over ground this",
        "  document cannot act on - private, tribal, closed withdrawals, and any public owner",
        f"  this taxon's {sp.mode} mode rules out. It shows where the stands are and confers",
        "  nothing. Nothing above is derived from it.",
        "",
    ]
    return lines


# --- markdown out ------------------------------------------------------------
# QGIS shows a layer abstract and an attribute value as plain text, so the emphasis has
# to come off and the hand-wrapped bullets have to be put back together.

def plain(text):
    """Markdown emphasis and code ticks off. Asterisks are only ever emphasis here."""
    return text.replace("*", "").replace("`", "")


def bullets(lines):
    """The bullets of a markdown block, one whole sentence each, unwrapped and plain."""
    out, current = [], None
    for line in lines:
        if line.startswith("* "):
            if current:
                out.append(current)
            current = line[2:].strip()
        elif current is not None and line.startswith("  "):
            current += " " + line.strip()
        elif not line.strip() or line.startswith("#") or line.startswith("|"):
            if current:
                out.append(current)
            current = None
    if current:
        out.append(current)
    return [plain(b) for b in out]


def caveats_plain(sp, reg, width=88):
    """`caveats()` as a plain-text block, for a layer abstract or an attribute value."""
    return "\n".join(
        textwrap.fill(f"- {b}", width=width, subsequent_indent="  ",
                      break_on_hyphens=False, break_long_words=False)
        for b in bullets(caveats(sp, reg))
    )


def abstract(sp, reg, grid=None, codes=(), samples=None, when=None):
    """Everything the map is, in plain text: the project abstract and the About layer.

    `grid` is prose the caller builds, because the cell *area* is stage 03's constant and
    naming it here would be a second place to forget it.
    """
    year = (when or datetime.date.today()).year
    out = [
        project_title(sp, reg),
        "",
        textwrap.fill(plain(method(sp, reg, samples, year)), width=88),
        "",
        f"Scientific name: {sp.binomial}",
        f"Common name: {title(sp)}",
        f"Kind: {sp.kind}",
        f"Range: {range_text(sp)}",
        f"Cover: {cover_text(sp)}",
        f"Habitat conditions: {conditions_text(sp, year)}",
    ]
    if grid:
        out.append(f"Scouting grid: {grid}")
    if codes:
        out += ["", "Who administers it:", owner_plain(sp, codes)]
    out += ["", caveats_plain(sp, reg)]
    return "\n".join(out)


if __name__ == "__main__":
    import region as region_mod

    _reg = region_mod.resolve()
    for _slug in ("junioste", "platdila", "morcelat"):
        _sp = species_mod.resolve(["", _slug])
        print("=" * 78)
        print(abstract(_sp, _reg, grid="1 km2 hex",
                       codes=list(ownership.screenable(_sp.mode))))
