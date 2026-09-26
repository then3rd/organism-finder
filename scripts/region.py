"""Where the screening runs: jurisdiction services, projection, and legal exclusions.

Standard library only - stage 05 imports this too.

Utah and Idaho ship. The point of the record is that every state-specific fact lives here
rather than scattered across the stages, and adding Idaho was mostly - but not entirely -
a data entry. What the port actually found, which is the honest porting checklist:

  * BLM publishes the same service *families* per state office, but not the same
    folders, layer indices or schemas. Idaho's NLCS polygons are layer 0 where Utah's
    are layer 1, and Idaho splits the ADMU service in three.
  * SMA attribute *values* do not port at all. Utah's ADMIN codes and Idaho's
    MGMT_AGNCY codes barely overlap, which is why the ownership registry is per region.
  * A state may simply not publish something. Idaho has no DESIG column and no
    lands-with-wilderness-characteristics layer, so `desig_field` is None and
    `nlcs_flagging` finds nothing. The fields that go None are the interesting part of
    a port: they are where a column disappears from the deliverables rather than
    appearing full of nulls, because a null column would assert we looked.

The SMA service is not a BLM layer that happens to be hosted by BLM - it is the whole
surface-management picture for the state, 11,687 polygons in Utah of which 2,171 are
BLM's, and 15,622 in Idaho. `owner_field` names the column carrying the administering
agency; the codes in it are the keys of that region's registry in scripts/ownership.py.
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
    # Column on the SMA layer naming the administering agency. Per-region because the
    # schema is the state office's, and the *values* are per-region too - they are the
    # keys of the region's registry in scripts/ownership.py. Utah says ADMIN, Idaho says
    # MGMT_AGNCY, and the codes in them barely overlap.
    owner_field: str = "ADMIN"
    # A second column that can contradict the first. Idaho publishes polygons reading
    # MGMT_AGNCY='BLM' with AGNCY_NAME='PRIVATE'; screened on the first alone that is a
    # private inholding in the candidates table with a BLM field office to ring. Where
    # both name an owner the *more restrictive* wins - contradicted ground is treated as
    # closed, the same rule UNKNOWN embodies. None where the layer has no second opinion.
    owner_confirm_field: "str | None" = None
    # Column carrying the legal designation (Wilderness, National Monument, ...). None
    # where the state office publishes no such column, in which case `excluded_desig` is
    # never consulted and the NLCS polygon layers carry the whole exclusion answer.
    desig_field: "str | None" = "DESIG"
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
    # Which `nlcs` entries bar collecting (subtracted in collect mode, flagged otherwise)
    # and which are only ever a flag. Named rather than hard-coded in stage 03 so a state
    # that publishes no lands-with-wilderness-characteristics layer produces no `in_lwc`
    # column at all, rather than a column of False - which would be a claim its data does
    # not make. Entries missing from `nlcs` are skipped.
    nlcs_excluding: tuple = ("wilderness", "wsa", "nm_nca")
    nlcs_flagging: tuple = ("lwc",)
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
    # --- camping (mode="camp") --------------------------------------------------
    # ISO 3166-2 code for an Overpass `area` query. An area clips to the state boundary
    # exactly, where the county bbox would drag in every campsite along the neighbours'
    # borders. Required: a region that forgets it would query OpenStreetMap for nothing.
    osm_area: str = ""
    # National services, narrowed by the county envelope at fetch time like the fire and
    # NHD layers. The USFS layer marks rec *areas* by activity; "Dispersed Camping" there
    # is an area where dispersed camping is managed, not an individual pad.
    usfs_rec: str = (
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/"
        "EDW_RecreationOpportunities_01/MapServer/0"
    )
    usfs_camp_where: str = (
        "markeractivity IN ('Dispersed Camping','Campground Camping','Group Camping',"
        "'Horse Camping','OHV Camping','RV Camping')"
    )
    # BLM's recreation.gov (RIDB) camping facilities. Its State column is not the state
    # name, so the bbox does the narrowing rather than a where clause.
    blm_camp: str = (
        "https://gis.blm.gov/arcgis/rest/services/recreation/"
        "BLM_Natl_Recreation_Sites_Facilities/MapServer/8"
    )
    # USGS 3DEP, for slope cover. Queried in the LANDFIRE grid so the slope class tiles
    # line up with the EVT ones pixel for pixel.
    dem_imageserver: str = (
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
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
    osm_area="US-UT",
    # Utah's NLCS services publish an "(Arc)" boundary-line layer first, so the polygon
    # layer wanted here is layer 1 (layer 2 for wild & scenic river corridors). That is a
    # fact about Utah's services, not a rule - Idaho's publish the polygons at layer 0.
    nlcs={
        "wilderness": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WLD/FeatureServer/1",
        "wsa": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSA/FeatureServer/1",
        "nm_nca": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_NMNCA/FeatureServer/1",
        "lwc": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_LWC/FeatureServer/1",
        "wsr": "https://gis.blm.gov/utarcgis/rest/services/NLCS/BLM_UT_WSR/FeatureServer/2",
    },
)

IDAHO = Region(
    key="id",
    name="Idaho",
    # NAD83 / Idaho Transverse Mercator, in metres. Idaho straddles UTM zones 11N and
    # 12N, so a single UTM zone would distort one end of the state; IDTM is the state
    # standard and is the SMA service's own native spatial reference.
    crs="EPSG:8826",
    sma=("https://gis.blm.gov/idarcgis/rest/services/Lands/"
         "BLM_ID_Surface_Management_Agency/FeatureServer/0"),
    # Idaho splits into three services what Utah publishes as one with two layers. The
    # district-office boundaries are the coarser parent unit and are not used here:
    # .../admin_boundaries/BLM_ID_Administrative_Unit_District_Office_Boundaries/...
    admu_boundary=("https://gis.blm.gov/idarcgis/rest/services/admin_boundaries/"
                   "BLM_ID_Administrative_Unit_Field_Office_Boundaries/FeatureServer/0"),
    admu_office=("https://gis.blm.gov/idarcgis/rest/services/admin_boundaries/"
                 "BLM_ID_Administrative_Unit_Office_Locations/FeatureServer/0"),
    counties=(
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1"
    ),
    counties_where="STATE='16'",
    gbif_state="Idaho",
    osm_area="US-ID",
    # Idaho's SMA layer carries MGMT_AGNCY and AGNCY_NAME and neither a DESIG nor an
    # OWNER column, so `desig_field` is None and `excluded_desig` is never consulted -
    # the three NLCS layers below carry the whole legal-exclusion answer here.
    owner_field="MGMT_AGNCY",
    owner_confirm_field="AGNCY_NAME",
    desig_field=None,
    # Polygons are layer 0 in these services, unlike Utah's. There is no
    # lands-with-wilderness-characteristics layer and no wild & scenic river layer, so
    # `nlcs_flagging` finds nothing and no `in_lwc` column is written for Idaho. That is
    # an absence of data, not a finding of absence - see README's known limitations.
    nlcs={
        "wilderness": ("https://gis.blm.gov/idarcgis/rest/services/special_designations/"
                       "BLM_ID_NLCS_Wilderness_Area/FeatureServer/0"),
        "wsa": ("https://gis.blm.gov/idarcgis/rest/services/special_designations/"
                "BLM_ID_NLCS_Wilderness_Study_Area/FeatureServer/0"),
        "nm_nca": ("https://gis.blm.gov/idarcgis/rest/services/special_designations/"
                   "BLM_ID_NLCS_National_Monuments_and_National_Conservation_Areas/"
                   "FeatureServer/0"),
    },
    # Inherited from the defaults, and worth naming as an assumption rather than a
    # finding: `flowline_where` keeps perennial channels only because in Utah a mapped
    # intermittent channel is usually a dry wash. Northern Idaho has more genuinely
    # seasonal water, so a WATER-condition taxon screened here is using Utah's hydrology
    # judgement until somebody checks it.
)

REGIONS = {r.key: r for r in (UTAH, IDAHO)}
DEFAULT = "ut"

# Keys must stay disjoint from the grid shapes and the taxon slugs, because all three
# share the command line and `resolve` tells them apart by vocabulary rather than by
# position. In practice that means: not "hex", not "square", and not eight characters.


def _known(key):
    if key not in REGIONS:
        raise SystemExit(f"unknown region {key!r}; known: {', '.join(sorted(REGIONS))}")
    return REGIONS[key]


def resolve(argv=(), default=DEFAULT):
    """Region named anywhere in argv - `03_overlay.py junioste square id`.

    A bare key is taken as a *claim* and validated - `resolve("zz")` is an error, not a
    quiet fall back to the default. The argv form cannot do that, because argv also
    carries slugs and grid names; there it is species.resolve that refuses the leftover
    token, which is why an unrecognised region reads as `unknown species 'idahoo'`.
    """
    if isinstance(argv, str):
        return _known(argv)
    import grid as grid_mod

    return _known(grid_mod.pick(argv, REGIONS, "region") or default)
