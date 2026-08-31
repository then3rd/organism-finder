"""Where the screening runs: jurisdiction services, projection, and legal exclusions.

Standard library only - stage 05 imports this too.

Only Utah ships today. The point of the record is that every state-specific fact lives
here rather than scattered across the stages, so adding a state is a data entry: BLM
publishes the same service families per state office, though layer indices and SMA
attribute values do vary and need checking against the new state's services.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    # Working projection. Areas must come out metric, so this is a state-appropriate
    # UTM zone / state plane, never a geographic CRS.
    crs: str
    blm_sma: str
    admu_boundary: str
    admu_office: str
    counties: str
    counties_where: str
    nlcs: dict
    sma_where: str = "ADMIN='BLM'"
    # SMA DESIG values where collecting live plants is off the table or needs a
    # different process. These are BLM SMA schema strings, hence per-region.
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
    blm_sma="https://gis.blm.gov/utarcgis/rest/services/Lands/BLM_UT_SMA/FeatureServer/0",
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
