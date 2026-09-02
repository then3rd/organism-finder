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
    # --- habitat conditions (scripts/habitat.py) ------------------------------
    # Both of these are national services rather than state-office ones, so they are the
    # same URL for every region and are narrowed by the county envelope at fetch time.
    # They are still Region fields because the *filters* are not universal: which fcode
    # counts as perennial water is a judgement about the state's hydrology.
    # WFIGS rather than the InterAgencyFirePerimeterHistory view, which is the
    # *finalised* archive and lags badly - it holds 104 Utah perimeters for 2019 and one
    # for 2022. A burn condition asks about the last one to three seasons, so a source
    # six years behind cannot answer it at all. WFIGS runs 2020 to the current fire year.
    fire_perims: str = (
        "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
        "WFIGS_Interagency_Perimeters/FeatureServer/0"
    )
    # Wildfires only. Prescribed burns are in the same layer (9 of 1,385 over Utah) and
    # are deliberately cool and patchy - they are not the stand-replacing event a
    # fire-following fungus responds to.
    fire_where: str = "attr_IncidentTypeCategory = 'WF'"
    # Epoch milliseconds, even through the GeoJSON endpoint. Discovery rather than
    # containment, because the season a fire started is the season the mycelium responds
    # to, and a November fire's containment date lands in the wrong year.
    fire_date_field: str = "attr_FireDiscoveryDateTime"
    fire_name_field: str = "poly_IncidentName"
    fire_acres_field: str = "poly_GISAcres"
    # NHD large-scale flowlines and waterbodies. Field names on this service are
    # lowercase, unlike every BLM layer here.
    hydro_flowline: str = (
        "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6"
    )
    hydro_waterbody: str = (
        "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12"
    )
    # 46006 is perennial stream/river. Intermittent (46003) is deliberately out: in Utah
    # most mapped intermittent channels are dry washes, and buffering those would put
    # "near water" over half the state and mean nothing.
    flowline_where: str = "fcode=46006"
    # Lake/pond (39004/39009-39012) and reservoir (43600 series). Playas and ice are not
    # here for the same reason the washes are not.
    waterbody_where: str = (
        "fcode IN (39004,39009,39010,39011,39012,43600,43601,43613,43617,43618,"
        "43619,43621,43624,43625,43626)"
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
