"""Who administers the ground, and what that means for taking a plant off it.

Standard library only - stage 05 imports this too.

This is the third axis, alongside species and region. It exists because the pipeline
originally screened one agency's land and encoded that agency in column names, layer
names and prose; the moment a second agency is in scope, "who administers this" has to
become a value rather than an assumption.

Each state-office SMA service publishes an agency code per polygon (`Region.owner_field`),
and these records are keyed by it - so the registry is per region. That is not a
generalisation for its own sake: Utah says `ADMIN` and Idaho says `MGMT_AGNCY`, and the
values barely overlap either. Idaho's refuges are `NWR` where Utah's are `USFWS`, its
Reclamation ground is `BOR` rather than `BR`, and it publishes `COE`, `LU_DOI` and
`HSTRCWTR`, none of which Utah has. `Region` names the field; this names the values.

The agencies themselves are mostly national, so the federal records are written once and
recoded per region with `_as()`. A state that calls the Forest Service something else
still gets the same sentence about ranger districts, which is the same argument
scripts/factsheet.py makes about the map and the tables sharing prose.

Two fields drive behaviour rather than display:

  * `public`  - may a member of the public walk onto it at all. False for DOD, DOE,
                UDOT right-of-way, tribal and private ground. Non-public owners are
                still mapped (they are why a stand is unreachable) but never screened.
  * `collect` - whether a live plant may be taken with a permit, cannot be taken at all,
                or needs a conversation. Stage 03 uses this to decide which owners a
                *collect*-mode taxon may be screened on; an *observe*-mode taxon ignores
                it, because looking at an orchid in a national park is not a permit
                question.
  * `camp`    - may you stay the night, and how: FREE is dispersed camping with no
                permit (BLM, USFS), PERMIT is designated sites or a backcountry permit
                (national and state parks). Screened by camp mode, which asks nothing
                about what may be taken.
  * `forage`  - the same question for mushrooms, and it is a genuinely different one.
                Picking a fungal fruiting body leaves the organism in the ground, so most
                agencies that will not let you dig a plant will let you fill a bag with
                morels, several without any permit at all. Reusing `collect` here would
                have the summary tell people the wrong law, so it is its own field.
"""
from dataclasses import dataclass, replace

# collect / forage: what may be taken off this ground
FREE = "free"              # personal-use quantities, no permit required
PERMIT = "permit"          # a permit exists and is routinely issued
ASK = "ask"                # case by case; no standing programme
PROHIBITED = "prohibited"  # categorically not available

# FREE is a forage or camp answer only. No agency here lets anybody dig a live plant
# without paperwork, and if one ever does, it belongs on `collect` as a deliberate edit.


@dataclass(frozen=True)
class Owner:
    code: str
    name: str
    # One short token for tables and legends: "USFS", "SITLA".
    short: str
    tenure: str            # federal | state | tribal | private
    public: bool
    collect: str
    # Who to ask and under what authority. Lands verbatim in summary.md, so it is prose,
    # not a phrase - one sentence, no trailing full stop handling needed downstream.
    authority: str
    # Map wash. See the palette note in 05_qgis_project.py: these sit at low alpha as the
    # bottom vector layer, so they may use hues the ramps above have claimed.
    color: str
    # Mushrooms. Required rather than defaulted, because a silent default here would be
    # the registry quietly guessing at somebody's regulations.
    forage: str
    # Camping, and the third question rather than a variant of the first two: nothing
    # leaves the ground, but you stay on it overnight, and the agencies that will sell you
    # a plant permit have entirely separate rules for that. Both fields required, and the
    # prose has no fallback - a plant-permit sentence under "may you camp here" is wrong
    # on every row, not merely imprecise.
    camp: str
    camp_authority: str
    # Only where the mushroom answer differs materially from the plant one. Blank falls
    # back to `authority`, so agencies that treat both the same are written once.
    forage_authority: str = ""


# --- federal ------------------------------------------------------------------
# National agencies, written once. Only the code differs between state offices, and
# `_as()` below restamps it - the prose, the colour and the permit answers are the same
# ground truth in every state.
_FEDERAL = (
    Owner(
        code="BLM", name="Bureau of Land Management", short="BLM",
        tenure="federal", public=True, collect=PERMIT, forage=FREE, color="#ffb300",
        authority=(
            "The BLM field office that administers the parcel issues live-plant and "
            "vegetative-product permits - not the state office"
        ),
        forage_authority=(
            "Personal-use mushroom picking is casual use on BLM ground and needs no "
            "permit; the field office sets any local limit and sells the permit "
            "commercial pickers need, so ask before selling anything"
        ),
        camp=FREE,
        camp_authority=(
            "Dispersed camping is allowed on most BLM ground for 14 days in any 28, after "
            "which you move at least 25 miles; the field office posts closures and "
            "designated-sites-only areas, and a developed recreation site follows its own "
            "rules and fees"
        ),
    ),
    Owner(
        code="USFS", name="U.S. Forest Service", short="USFS",
        # Light lime rather than the forest green it was: a dark green disappears into
        # conifer canopy on the imagery, which is exactly where Forest Service ground is.
        # Not a fully saturated lime either - those collapse into BLM's amber under
        # simulated deuteranopia and protanopia (dE 5-9), and BLM is the neighbour USFS
        # ground meets most. This one holds dE >= 19.7 against every owner and ramp colour.
        tenure="federal", public=True, collect=PERMIT, forage=FREE, color="#9ccc65",
        authority=(
            "The ranger district issues free-use and charge permits for plants and "
            "transplants; terms and species lists differ forest by forest, and some "
            "districts do not issue them at all"
        ),
        forage_authority=(
            "Most ranger districts allow personal-use mushrooms free up to a daily "
            "gallon limit, but several require a free-use permit picked up in person "
            "and a few close burned areas outright - ring the district before driving"
        ),
        camp=FREE,
        camp_authority=(
            "Dispersed camping is allowed across most of a national forest for 14 days "
            "(16 on some forests) outside developed sites; drive only on routes the Motor "
            "Vehicle Use Map shows, park within the dispersed-camping corridors it marks, "
            "and ring the district for area closures"
        ),
    ),
    Owner(
        code="NPS", name="National Park Service", short="NPS",
        tenure="federal", public=True, collect=PROHIBITED, forage=PROHIBITED,
        color="#4e342e",
        authority=(
            "Possessing, destroying or removing plants from a unit of the National Park "
            "System is prohibited under 36 CFR 2.1 - there is no transplant permit to "
            "apply for, only a research collecting permit"
        ),
        forage_authority=(
            "Mushrooms are plants for the purpose of 36 CFR 2.1 and picking them is "
            "prohibited unless that unit's superintendent's compendium says otherwise, "
            "which few compendia in these states do"
        ),
        camp=PERMIT,
        camp_authority=(
            "Camping in a park unit is in a designated campground or under a backcountry "
            "permit from that park; roadside and dispersed camping are prohibited "
            "throughout the park system"
        ),
    ),
    Owner(
        code="USFWS", name="U.S. Fish and Wildlife Service", short="USFWS",
        tenure="federal", public=True, collect=PROHIBITED, forage=PROHIBITED,
        color="#ef6c00",
        authority=(
            "National Wildlife Refuge collection needs a Special Use Permit, issued for "
            "research rather than for transplanting"
        ),
        forage_authority=(
            "Refuge collection of any kind needs a Special Use Permit, and mushrooms "
            "are not what those are issued for"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "Camping on a National Wildlife Refuge is prohibited except in an area the "
            "refuge designates for it, and few refuges in these states have one"
        ),
    ),
    Owner(
        code="BR", name="Bureau of Reclamation", short="BOR",
        tenure="federal", public=True, collect=ASK, forage=ASK, color="#607d8b",
        authority=(
            "Reclamation withdrawals have no standing plant-permit programme; ask the "
            "area office, and expect the answer to depend on the project"
        ),
        camp=ASK,
        camp_authority=(
            "Reclamation reservoirs often have campgrounds run by a partner agency; "
            "whether dispersed camping is allowed depends on the project, so ask the area "
            "office"
        ),
    ),
    Owner(
        code="DOD", name="Department of Defense", short="DOD",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Closed military withdrawal - no public entry, let alone collection",
        camp=PROHIBITED,
        camp_authority=(
            "Closed military withdrawal - no public entry, let alone an overnight stay"
        ),
    ),
    Owner(
        code="DOE", name="Department of Energy", short="DOE",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Closed federal withdrawal - no public entry",
        camp=PROHIBITED,
        camp_authority=(
            "Closed federal withdrawal - no public entry"
        ),
    ),
    Owner(
        code="OF", name="other federal", short="other fed",
        tenure="federal", public=True, collect=ASK, forage=ASK, color="#607d8b",
        authority="Federal ground held by an agency this registry does not name - identify "
                  "the administering agency before assuming anything",
        camp=ASK,
        camp_authority=(
            "Federal ground held by an agency this registry does not name - identify the "
            "administrator and its camping rules before pitching a tent"
        ),
    ),
)

# --- Utah state ---------------------------------------------------------------
# SITLA is the big one in Utah - more polygons than BLM - and it is the owner most
# often mistaken for public land. It is a revenue trust, not a public estate.
_UTAH_STATE = (
    Owner(
        code="SITLA", name="School and Institutional Trust Lands Administration",
        short="SITLA", tenure="state", public=True, collect=PERMIT, forage=ASK,
        # Its own blue, not the state agencies' #1565c0: trust land and a state park have
        # different rules, and the camping map fills cells by owner. Darker rather than
        # lighter because every light blue collided with the parcel cyan under simulated
        # deuteranopia; this one holds dE >= 19.5 against every owner and ramp colour in
        # normal vision and all three CVD simulations, and >= 22.9 against the state blue.
        color="#1414b8",
        authority=(
            "Trust land is managed to make money for the school fund, not for public "
            "recreation; SITLA issues a special-use lease or permit and charges for the "
            "material, and unpermitted collection is trespass even where access is open"
        ),
        forage_authority=(
            "Trust land is leased, not open range: recreational use needs a SITLA "
            "permit and gathering anything of value is a separate conversation with the "
            "area office"
        ),
        camp=ASK,  # unverified: SITLA's recreation rules have changed more than once
        camp_authority=(
            "Trust land is not public recreation land by right; SITLA allows casual "
            "camping on some parcels and requires a recreation permit on others, so check "
            "before staying the night"
        ),
    ),
    Owner(
        code="FFSL", name="Utah Forestry, Fire and State Lands", short="FFSL",
        tenure="state", public=True, collect=PERMIT, forage=ASK, color="#1565c0",
        authority="FFSL issues permits on sovereign and other state lands it administers",
        camp=ASK,
        camp_authority=(
            "Sovereign and state lands are open to some recreation; ask FFSL whether "
            "overnight camping is allowed on the parcel"
        ),
    ),
    Owner(
        code="SL&F", name="Utah Forestry, Fire and State Lands", short="FFSL",
        tenure="state", public=True, collect=PERMIT, forage=ASK, color="#1565c0",
        authority="FFSL issues permits on sovereign and other state lands it administers",
        camp=ASK,
        camp_authority=(
            "Sovereign and state lands are open to some recreation; ask FFSL whether "
            "overnight camping is allowed on the parcel"
        ),
    ),
    Owner(
        code="UDWR", name="Utah Division of Wildlife Resources", short="UDWR",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority=(
            "Wildlife management areas are managed for habitat; ask the regional office, "
            "and note that many WMAs are seasonally closed"
        ),
        camp=ASK,
        camp_authority=(
            "Many wildlife management areas prohibit camping or close seasonally for "
            "wintering wildlife; read the posted rules or ask the regional office"
        ),
    ),
    Owner(
        code="USP", name="Utah State Parks", short="State Parks",
        tenure="state", public=True, collect=PROHIBITED, forage=PROHIBITED, color="#1565c0",
        authority="Collecting plants in a Utah state park is prohibited",
        camp=PERMIT,
        camp_authority=(
            "Camping in a Utah state park is in its designated campground, for a fee - "
            "there is no dispersed camping"
        ),
    ),
    Owner(
        code="DNR", name="Utah Department of Natural Resources", short="DNR",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority="Ask the administering DNR division",
        camp=ASK,
        camp_authority=(
            "Ask the administering DNR division before camping"
        ),
    ),
    Owner(
        code="OS", name="other state", short="other state",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority="State ground held by an agency this registry does not name",
        camp=ASK,
        camp_authority=(
            "State ground held by an agency this registry does not name - ask before "
            "camping"
        ),
    ),
    Owner(
        code="UDOT", name="Utah Department of Transportation", short="UDOT",
        tenure="state", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Highway right-of-way - not a place to park and dig",
        camp=PROHIBITED,
        camp_authority=(
            "Highway right-of-way - not a place to camp"
        ),
    ),
)

# --- neither public nor private in the usual sense ----------------------------
_COMMON = (
    Owner(
        code="Tribal", name="tribal land", short="tribal",
        tenure="tribal", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#6a1b9a",
        authority=(
            "Sovereign tribal land. It is not public land and this screening confers "
            "nothing; entry and collection are the tribe's to grant"
        ),
        forage_authority=(
            "Sovereign tribal land. Mushroom gathering is the tribe's to grant and is "
            "often reserved to members; this screening confers nothing"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "Sovereign tribal land. Camping is the tribe's to allow, often by tribal "
            "permit; this screening confers nothing"
        ),
    ),
    Owner(
        code="Private", name="private land", short="private",
        tenure="private", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#9e9e9e",
        authority="Private property - the landowner's permission is the only authority",
        camp=PROHIBITED,
        camp_authority=(
            "Private property - camping needs the landowner's permission"
        ),
    ),
)

def _as(o, code):
    """The same agency under the code this state's SMA layer publishes for it."""
    return replace(o, code=code)


def _by_code(owners, code):
    return next(o for o in owners if o.code == code)


# --- Idaho state and Idaho-only codes -----------------------------------------
# IDL is the SITLA analogue - an endowment trust, not a public estate - but the two
# states differ in a way worth keeping in the prose: Idaho endowment land is open to
# public recreation by default, while taking anything off it still needs IDL's say-so.
_IDAHO_STATE = (
    Owner(
        code="STATE", name="Idaho Department of Lands", short="IDL",
        tenure="state", public=True, collect=PERMIT, forage=ASK, color="#1565c0",
        authority=(
            "State endowment land is managed to earn money for the beneficiaries, not "
            "for recreation; IDL's area office issues the permit or lease, and taking "
            "plants without one is trespass even though the ground is open to walk on"
        ),
        forage_authority=(
            "Idaho endowment land is open to recreation, but gathering anything of "
            "value is a separate conversation with the IDL area office"
        ),
        camp=ASK,  # unverified: IDL restricts some parcels and sets the stay limit
        camp_authority=(
            "Idaho endowment land is generally open to recreation, including short-stay "
            "camping, but IDL restricts some parcels and sets the stay limit - check with "
            "the area office"
        ),
    ),
    Owner(
        code="STATEFG", name="Idaho Department of Fish and Game", short="IDFG",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority=(
            "Wildlife management areas are managed for habitat; ask the regional "
            "office, and note that many WMAs are seasonally closed"
        ),
        camp=ASK,
        camp_authority=(
            "Many wildlife management areas prohibit camping or close seasonally for "
            "wintering wildlife; read the posted rules or ask the regional office"
        ),
    ),
    Owner(
        # unverified: some Idaho parks do allow personal-use mushrooms under the park
        # manager's discretion. Closed until somebody reads the current park rules -
        # the cost of being wrong this way is a smaller map, the other way a citation.
        code="STATEPR", name="Idaho Parks and Recreation", short="State Parks",
        tenure="state", public=True, collect=PROHIBITED, forage=PROHIBITED,
        color="#1565c0",
        authority="Collecting plants in an Idaho state park is prohibited",
        forage_authority=(
            "Treated here as prohibited. Some Idaho parks permit personal-use "
            "mushrooms at the manager's discretion - ring the park before assuming "
            "either answer"
        ),
        camp=PERMIT,
        camp_authority=(
            "Camping in an Idaho state park is in its designated campground, for a fee"
        ),
    ),
)

# Codes Idaho publishes that Utah has no equivalent for. The four marked unverified are
# deliberately closed: the SMA layer names them but does not say enough to place them,
# and "unchecked ground is treated as closed" is the rule the whole registry runs on.
_IDAHO_ONLY = (
    Owner(
        code="COE", name="U.S. Army Corps of Engineers", short="USACE",
        tenure="federal", public=True, collect=ASK, forage=ASK, color="#607d8b",
        authority=(
            "Corps project land around reservoirs is generally open to recreation, but "
            "there is no standing plant-permit programme; ask the project office"
        ),
        camp=ASK,
        camp_authority=(
            "Corps project land is generally camping-in-designated-campgrounds-only; ask "
            "the project office about anything else"
        ),
    ),
    Owner(
        code="BIA", name="tribal land", short="tribal",
        tenure="tribal", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#6a1b9a",
        authority=(
            "Land held in trust for a tribe. It is not public land and this screening "
            "confers nothing; entry and collection are the tribe's to grant"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "Land held in trust for a tribe. Camping is the tribe's to allow; this "
            "screening confers nothing"
        ),
    ),
    Owner(
        code="IR", name="tribal land", short="tribal",
        tenure="tribal", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#6a1b9a",
        authority=(
            "Indian reservation. It is not public land and this screening confers "
            "nothing; entry and collection are the tribe's to grant"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "Indian reservation. Camping is the tribe's to allow, often by tribal permit; "
            "this screening confers nothing"
        ),
    ),
    Owner(
        # unverified
        code="LU_USDA", name="land-utilization project (USDA)", short="LU tract",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#37474f",
        authority=(
            "A land-utilization tract whose administering agency this registry has not "
            "identified - treat it as closed until somebody does"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "A land-utilization tract whose administering agency this registry has not "
            "identified - treat it as closed until somebody does"
        ),
    ),
    Owner(
        # unverified
        code="LU_DOI", name="land-utilization project (DOI)", short="LU tract",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#37474f",
        authority=(
            "A land-utilization tract whose administering agency this registry has not "
            "identified - treat it as closed until somebody does"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "A land-utilization tract whose administering agency this registry has not "
            "identified - treat it as closed until somebody does"
        ),
    ),
    Owner(
        # unverified
        code="HSTRCWTR", name="historic water", short="hist. water",
        tenure="state", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#37474f",
        authority=(
            "Bed of a historic or navigable watercourse. Even where the state holds it, "
            "it is riverbed - not ground to send somebody to dig on"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "Bed of a historic or navigable watercourse - not ground to camp on"
        ),
    ),
    Owner(
        # unverified, and deliberately NOT mapped to Utah's `OF` ("other federal",
        # public, ASK). Idaho's AGNCY_NAME for these rows is one of FAA, USDA, FHA, BIA,
        # DOI or GSA - and BIA is tribal, so the permissive reading would open tribal
        # ground on the strength of a code that means "we did not say".
        code="OTHER", name="unnamed administrator", short="other",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED,
        color="#9e9e9e",
        authority=(
            "The layer files this under a catch-all that spans several agencies, one of "
            "them tribal - identify the administrator before assuming anything"
        ),
        camp=PROHIBITED,
        camp_authority=(
            "The layer files this under a catch-all that spans several agencies, one of "
            "them tribal - identify the administrator before assuming anything"
        ),
    ),
)


def _registry(*owners):
    return {o.code: o for o in owners}


UTAH_OWNERS = _registry(*_FEDERAL, *_UTAH_STATE, *_COMMON)
IDAHO_OWNERS = _registry(
    _by_code(_FEDERAL, "BLM"),
    _by_code(_FEDERAL, "USFS"),
    _by_code(_FEDERAL, "NPS"),
    _as(_by_code(_FEDERAL, "USFWS"), "NWR"),
    _as(_by_code(_FEDERAL, "BR"), "BOR"),
    _as(_by_code(_FEDERAL, "DOD"), "MIL"),
    _by_code(_FEDERAL, "DOE"),
    *_IDAHO_ONLY,
    *_IDAHO_STATE,
    _as(_by_code(_COMMON, "Private"), "PRIVATE"),
)

# Deliberately no module-level `OWNERS`. A registry that resolves without naming a region
# is how a caller silently gets Utah's answer on Idaho ground.
REGISTRIES = {"ut": UTAH_OWNERS, "id": IDAHO_OWNERS}

# Anything the service reports that is not in the registry. Unknown ground is treated as
# closed rather than open: the failure mode to avoid is telling somebody to dig on ground
# nobody has checked.
UNKNOWN = Owner(
    code="?", name="unidentified administrator", short="unknown",
    tenure="unknown", public=False, collect=PROHIBITED, forage=PROHIBITED,
    color="#9e9e9e",
    authority="The surface management layer does not name an administrator for this "
              "polygon - treat it as closed until somebody identifies it",
    camp=PROHIBITED,
    camp_authority=(
        "The surface management layer does not name an administrator for this polygon - "
        "treat it as closed until somebody identifies it"
    ),
)


def registry(reg):
    """The owner records for a region. Accepts a `Region` or a bare key string.

    Duck-typed rather than importing region.py, which keeps the module import graph a
    DAG: region.py is a pure data record and nothing here needs to know that.
    """
    key = getattr(reg, "key", reg)
    try:
        return REGISTRIES[key]
    except KeyError:
        raise SystemExit(
            f"no ownership registry for region {key!r}; known: "
            f"{', '.join(sorted(REGISTRIES))}. A region needs one before it can be "
            "screened - the SMA codes are the state office's, not a national set."
        )


def owner(code, reg):
    """Registry entry for an SMA agency code, never KeyError."""
    return registry(reg).get(code, UNKNOWN)


def public_codes(reg):
    """Every code a member of the public can set foot on, in registry order."""
    return [c for c, o in registry(reg).items() if o.public]


# Most open to least. `stricter()` walks this, so the order is the rule.
_TAKING_RANK = (FREE, PERMIT, ASK, PROHIBITED)


def stricter(a, b):
    """The more restrictive of two owner records.

    For the regions whose SMA layer publishes a second agency column that can disagree
    with the first (`Region.owner_confirm_field`). Closed beats open, and within open
    ground the tighter taking answer wins. A polygon the layer cannot describe
    consistently is not a polygon to send somebody to dig on.
    """
    if a.public != b.public:
        return a if not a.public else b
    for field_name in ("collect", "forage", "camp"):
        ra, rb = (_TAKING_RANK.index(getattr(x, field_name)) for x in (a, b))
        if ra != rb:
            return a if ra > rb else b
    return a


def taking(mode):
    """The field that answers "may this leave the ground" for this mode, or None.

    `observe` has no such field on purpose: looking at an orchid in a national park is
    not a permit question, so nothing is consulted and every public owner is in scope.
    """
    return {"collect": "collect", "forage": "forage", "camp": "camp"}.get(mode)


def screenable(mode, reg):
    """Codes a taxon in this mode may be screened on, in this region.

    `collect` needs ground where a plant can lawfully leave; `forage` asks the same of
    mushrooms and gets a different and generally wider answer; `camp` needs ground you
    may lawfully sleep on, by right or by permit; `observe` only needs
    ground somebody can stand on, which is why an orchid map covers national parks and a
    juniper transplant map does not.
    """
    field = taking(mode)
    if field is None:
        return public_codes(reg)
    return [
        c for c, o in registry(reg).items()
        if o.public and getattr(o, field) in (FREE, PERMIT, ASK)
    ]


def authority_for(o, mode):
    """The prose for this owner under this mode. Forage falls back to the plant text,
    which is the right answer for every agency that treats the two the same; camp never
    does, because no plant-permit sentence answers a camping question."""
    if mode == "camp":
        return o.camp_authority
    if mode == "forage" and o.forage_authority:
        return o.forage_authority
    return o.authority


def summarize(codes, reg):
    """`[('BLM', Owner), ...]` for display, de-duplicated by owner name.

    Two agency codes can point at one agency - Utah publishes both `FFSL` and `SL&F` for
    Forestry, Fire and State Lands, and Idaho both `BIA` and `IR` for tribal ground - and
    a table with the agency twice is a bug report waiting to happen.
    """
    seen, out = set(), []
    for code in codes:
        o = owner(code, reg)
        if o.name in seen:
            continue
        seen.add(o.name)
        out.append((code, o))
    return out


if __name__ == "__main__":
    # region imported here rather than at module scope: stage 05 imports this file under
    # the system interpreter and never runs this block, and keeping the import local is
    # what stops the two data records depending on each other.
    import sys
    import region as region_mod

    # The bare-key form, not the argv scan: this command's only argument *is* a region,
    # so a typo has to be an error rather than a silent fall back to the default. Nothing
    # else here would catch it - there is no species argument to reject the leftover.
    _reg = region_mod.resolve(sys.argv[1] if len(sys.argv) > 1 else region_mod.DEFAULT)
    print(f"\n  {_reg.name} - {len(registry(_reg))} administrators, keyed by "
          f"{_reg.owner_field}\n")
    print(f"  {'code':<8} {'tenure':<8} {'entry':<7} {'plants':<10} {'mushrooms':<10} "
          f"{'camping':<10} name")
    for _c, _o in registry(_reg).items():
        _p = "public" if _o.public else "closed"
        print(f"  {_c:<8} {_o.tenure:<8} {_p:<7} {_o.collect:<10} "
              f"{_o.forage:<10} {_o.camp:<10} {_o.name}")
    print("\n  plants = collect mode, mushrooms = forage mode, camping = camp mode. `free`")
    print("  means personal-use quantities, or dispersed camping, without a permit.")
    print(f"\n  Another region: `just owners <key>`; known: "
          f"{', '.join(sorted(REGISTRIES))}.")
