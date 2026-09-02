"""Who administers the ground, and what that means for taking a plant off it.

Standard library only - stage 05 imports this too.

This is the third axis, alongside species and region. It exists because the pipeline
originally screened one agency's land and encoded that agency in column names, layer
names and prose; the moment a second agency is in scope, "who administers this" has to
become a value rather than an assumption.

The Utah SMA service publishes an `ADMIN` code per polygon (`Region.owner_field`), and
these records are keyed by it. Utah's codes are shared with the other BLM state-office
SMA services, so most of this registry ports; `Region` names the field, this names the
values.

Two fields drive behaviour rather than display:

  * `public`  - may a member of the public walk onto it at all. False for DOD, DOE,
                UDOT right-of-way, tribal and private ground. Non-public owners are
                still mapped (they are why a stand is unreachable) but never screened.
  * `collect` - whether a live plant may be taken with a permit, cannot be taken at all,
                or needs a conversation. Stage 03 uses this to decide which owners a
                *collect*-mode taxon may be screened on; an *observe*-mode taxon ignores
                it, because looking at an orchid in a national park is not a permit
                question.
  * `forage`  - the same question for mushrooms, and it is a genuinely different one.
                Picking a fungal fruiting body leaves the organism in the ground, so most
                agencies that will not let you dig a plant will let you fill a bag with
                morels, several without any permit at all. Reusing `collect` here would
                have the summary tell people the wrong law, so it is its own field.
"""
from dataclasses import dataclass

# collect / forage: what may be taken off this ground
FREE = "free"              # personal-use quantities, no permit required
PERMIT = "permit"          # a permit exists and is routinely issued
ASK = "ask"                # case by case; no standing programme
PROHIBITED = "prohibited"  # categorically not available

# FREE is a forage answer only. No agency here lets anybody dig a live plant without
# paperwork, and if one ever does, it belongs on `collect` as a deliberate edit.


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
    # Only where the mushroom answer differs materially from the plant one. Blank falls
    # back to `authority`, so agencies that treat both the same are written once.
    forage_authority: str = ""


OWNERS = {o.code: o for o in (
    # --- federal --------------------------------------------------------------
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
    ),
    Owner(
        code="USFS", name="U.S. Forest Service", short="USFS",
        tenure="federal", public=True, collect=PERMIT, forage=FREE, color="#33691e",
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
            "which in Utah it generally does not"
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
    ),
    Owner(
        code="BR", name="Bureau of Reclamation", short="BOR",
        tenure="federal", public=True, collect=ASK, forage=ASK, color="#607d8b",
        authority=(
            "Reclamation withdrawals have no standing plant-permit programme; ask the "
            "area office, and expect the answer to depend on the project"
        ),
    ),
    Owner(
        code="DOD", name="Department of Defense", short="DOD",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Closed military withdrawal - no public entry, let alone collection",
    ),
    Owner(
        code="DOE", name="Department of Energy", short="DOE",
        tenure="federal", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Closed federal withdrawal - no public entry",
    ),
    Owner(
        code="OF", name="other federal", short="other fed",
        tenure="federal", public=True, collect=ASK, forage=ASK, color="#607d8b",
        authority="Federal ground held by an agency this registry does not name - identify "
                  "the administering agency before assuming anything",
    ),
    # --- state ----------------------------------------------------------------
    # SITLA is the big one in Utah - more polygons than BLM - and it is the owner most
    # often mistaken for public land. It is a revenue trust, not a public estate.
    Owner(
        code="SITLA", name="School and Institutional Trust Lands Administration",
        short="SITLA", tenure="state", public=True, collect=PERMIT, forage=ASK,
        color="#1565c0",
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
    ),
    Owner(
        code="FFSL", name="Utah Forestry, Fire and State Lands", short="FFSL",
        tenure="state", public=True, collect=PERMIT, forage=ASK, color="#1565c0",
        authority="FFSL issues permits on sovereign and other state lands it administers",
    ),
    Owner(
        code="SL&F", name="Utah Forestry, Fire and State Lands", short="FFSL",
        tenure="state", public=True, collect=PERMIT, forage=ASK, color="#1565c0",
        authority="FFSL issues permits on sovereign and other state lands it administers",
    ),
    Owner(
        code="UDWR", name="Utah Division of Wildlife Resources", short="UDWR",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority=(
            "Wildlife management areas are managed for habitat; ask the regional office, "
            "and note that many WMAs are seasonally closed"
        ),
    ),
    Owner(
        code="USP", name="Utah State Parks", short="State Parks",
        tenure="state", public=True, collect=PROHIBITED, forage=PROHIBITED, color="#1565c0",
        authority="Collecting plants in a Utah state park is prohibited",
    ),
    Owner(
        code="DNR", name="Utah Department of Natural Resources", short="DNR",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority="Ask the administering DNR division",
    ),
    Owner(
        code="OS", name="other state", short="other state",
        tenure="state", public=True, collect=ASK, forage=ASK, color="#1565c0",
        authority="State ground held by an agency this registry does not name",
    ),
    Owner(
        code="UDOT", name="Utah Department of Transportation", short="UDOT",
        tenure="state", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#37474f",
        authority="Highway right-of-way - not a place to park and dig",
    ),
    # --- neither public nor private in the usual sense -------------------------
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
    ),
    Owner(
        code="Private", name="private land", short="private",
        tenure="private", public=False, collect=PROHIBITED, forage=PROHIBITED, color="#9e9e9e",
        authority="Private property - the landowner's permission is the only authority",
    ),
)}

# Anything the service reports that is not in the registry. Unknown ground is treated as
# closed rather than open: the failure mode to avoid is telling somebody to dig on ground
# nobody has checked.
UNKNOWN = Owner(
    code="?", name="unidentified administrator", short="unknown",
    tenure="unknown", public=False, collect=PROHIBITED, forage=PROHIBITED,
    color="#9e9e9e",
    authority="The surface management layer does not name an administrator for this "
              "polygon - treat it as closed until somebody identifies it",
)


def owner(code):
    """Registry entry for an SMA ADMIN code, never KeyError."""
    return OWNERS.get(code, UNKNOWN)


def public_codes():
    """Every code a member of the public can set foot on, in registry order."""
    return [c for c, o in OWNERS.items() if o.public]


def taking(mode):
    """The field that answers "may this leave the ground" for this mode, or None.

    `observe` has no such field on purpose: looking at an orchid in a national park is
    not a permit question, so nothing is consulted and every public owner is in scope.
    """
    return {"collect": "collect", "forage": "forage"}.get(mode)


def screenable(mode):
    """Codes a taxon in this mode may be screened on.

    `collect` needs ground where a plant can lawfully leave; `forage` asks the same of
    mushrooms and gets a different and generally wider answer; `observe` only needs
    ground somebody can stand on, which is why an orchid map covers national parks and a
    juniper transplant map does not.
    """
    field = taking(mode)
    if field is None:
        return public_codes()
    return [
        c for c, o in OWNERS.items()
        if o.public and getattr(o, field) in (FREE, PERMIT, ASK)
    ]


def authority_for(o, mode):
    """The prose for this owner under this mode. Falls back to the plant text, which is
    the right answer for every agency that treats the two the same."""
    if mode == "forage" and o.forage_authority:
        return o.forage_authority
    return o.authority


def summarize(codes):
    """`[('BLM', Owner), ...]` for display, de-duplicated by owner name.

    Two ADMIN codes can point at one agency - Utah publishes both `FFSL` and `SL&F` for
    Forestry, Fire and State Lands - and a table with the agency twice is a bug report
    waiting to happen.
    """
    seen, out = set(), []
    for code in codes:
        o = owner(code)
        if o.name in seen:
            continue
        seen.add(o.name)
        out.append((code, o))
    return out


if __name__ == "__main__":
    print(f"  {'code':<8} {'tenure':<8} {'entry':<7} {'plants':<10} {'mushrooms':<10} name")
    for _c, _o in OWNERS.items():
        _p = "public" if _o.public else "closed"
        print(f"  {_c:<8} {_o.tenure:<8} {_p:<7} {_o.collect:<10} "
              f"{_o.forage:<10} {_o.name}")
    print("\n  plants = collect mode, mushrooms = forage mode. `free` means personal-use")
    print("  quantities without a permit, and is a mushroom answer only.")
