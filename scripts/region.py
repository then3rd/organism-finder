"""Where the screening runs: jurisdiction services, projection, and legal exclusions.

Standard library only - stage 05 imports this too.

Only Utah ships today. The point of the record is that every state-specific fact lives
here rather than scattered across the stages, so adding a state is a data entry: BLM
publishes the same service families per state office, though layer indices and SMA
attribute values do vary and need checking against the new state's services.

The SMA service is not a BLM layer that happens to be hosted by BLM - it is the whole
surface-management picture for the state, 11,687 polygons in Utah of which 2,171 are
BLM's. `owner_field` names the column carrying the administering agency; the codes in it
are the keys of scripts/ownership.py.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    # Working projection. Areas must come out metric, so this is a state-appropriate
    # UTM zone / state plane, never a geographic CRS.
    crs: str
    sma: str
    admu_boundary: str
    admu_office: str
    counties: str
    counties_where: str
    nlcs: dict
    # GBIF's stateProvince value, for taxa screened from occurrence records rather than
    # from a range map.
    gbif_state: str
    # Column on the SMA layer naming the administering agency, and the broader
    # ownership class. Both are per-region because the schema is the state office's.
    owner_field: str = "ADMIN"
    tenure_field: str = "OWNER"
    # Everything the SMA layer has. Ownership is a dimension now, not a download filter -
    # narrowing here would make non-BLM ground invisible rather than merely unscreened.
    sma_where: str = "1=1"
    # SMA DESIG values where *collecting* live plants is off the table or needs a
    # different process. These are BLM SMA schema strings, hence per-region. They bar a
    # collect-mode taxon and only flag an observe-mode one: walking into a Wilderness to
    # look at an orchid is exactly what a Wilderness is for.
    excluded_desig: frozenset = field(
        default_factory=lambda: frozenset(
            {"Wilderness", "National Monument", "National Recreation Area"}
        )
    )


UTAH = Region(
    key="ut",
    name="Utah",
    # BLM Utah publishes everything in NAD83 / UTM 12N; stay in it so areas are metric.
    crs="EPSG:26912",
    sma="https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0",
    admu_boundary=(
        "https://gis.blm.gov/utarcgis/rest/services/AdminBoundaries/BLM_UT_ADMU/FeatureServer/0"
    ),
    admu_office=(
        "https://gis.blm.gov/utarcgis/rest/services/AdminBoundaries/BLM_UT_ADMU/FeatureServer/1"
    ),
    counties=(
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1"
    ),
    counties_where="STATE='49'",
    gbif_state="Utah",
    # NLCS services publish an "(Arc)" boundary-line layer first; the polygon layer we
    # want is layer 1 (layer 2 for wild & scenic river corridors).
    nlcs={
        "wilderness": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WLD/FeatureServer/1",
        "wsa": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSA/FeatureServer/1",
        "nm_nca": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_NMNCA/FeatureServer/1",
        "lwc": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_LWC/FeatureServer/1",
        "wsr": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSR/FeatureServer/2",
    },
)

REGIONS = {r.key: r for r in (UTAH,)}
DEFAULT = "ut"


def resolve(key=None):
    key = key or DEFAULT
    if key not in REGIONS:
        raise SystemExit(f"unknown region {key!r}; known: {', '.join(sorted(REGIONS))}")
    return REGIONS[key]
