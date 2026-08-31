"""The siting veto: where a pond must not go.

Terrain scoring likes exactly the wrong places. Flow accumulation and depression
depth are the model's two strongest signals, and both peak where water already
is -- an existing tank, or a river. Measured on the sample sheet with land cover
removed, three of five recommended sites landed inside permanent water.

The distinction these tests protect hardest is the one that is easy to get
backwards: a **river** must be excluded, a **stream** must not. A check dam
belongs on a small nala; excluding every mapped waterway would reject the correct
answer while looking safer.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.elevation.base import DemGrid
from app.providers.vector.overpass import OsmContext, OsmFeature
from app.services import exclusions

CELL = 5.0
N = 40


def dem() -> DemGrid:
    return DemGrid(
        elevation=np.full((N, N), 300.0, dtype=np.float32),
        transform=(CELL, 0.0, 500_000.0, 0.0, -CELL, 2_340_000.0 + N * CELL),
        epsg=32644,
        cell_size_m=CELL,
    )


def lonlat(grid: DemGrid, row: int, col: int) -> tuple[float, float]:
    from pyproj import Transformer

    x, y = grid.xy(row, col)
    lon, lat = Transformer.from_crs(grid.epsg, 4326, always_xy=True).transform(x, y)
    return float(lon), float(lat)


def feature(kind: str, lon: float, lat: float, tags: dict[str, str], size: float = 1e-5):
    ring = (
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    )
    return OsmFeature(kind=kind, osm_type="way", osm_id=1, tags=tags, rings=(ring,))  # type: ignore[arg-type]


class TestRiversAreExcludedButStreamsAreNot:
    """The distinction the whole module turns on."""

    def test_a_river_is_excluded(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        osm = OsmContext(water=[feature("water", lon, lat, {"waterway": "river"})])
        result = exclusions.build(grid, osm=osm)
        assert result.mask[20, 20], "a pond cannot be built in a river"
        assert result.removed_by["major_watercourse"] > 0

    def test_a_canal_is_excluded(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        osm = OsmContext(water=[feature("water", lon, lat, {"waterway": "canal"})])
        assert exclusions.build(grid, osm=osm).mask[20, 20]

    @pytest.mark.parametrize("waterway", ["stream", "drain", "ditch"])
    def test_a_small_channel_is_left_alone(self, waterway: str) -> None:
        """A check dam belongs *on* a nala. Excluding these rejects the answer."""
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        osm = OsmContext(water=[feature("water", lon, lat, {"waterway": waterway})])
        result = exclusions.build(grid, osm=osm)
        assert not result.mask.any(), f"{waterway} must not be excluded"

    def test_a_river_is_buffered_but_a_tank_is_not(self) -> None:
        """You cannot dam a river, and the land beside one floods. The bank of an
        existing tank is perfectly good ground for a new bund."""
        assert exclusions.SITING_BUFFER_M["major_watercourse"] > 0
        assert exclusions.SITING_BUFFER_M["standing_water"] == 0.0

    def test_the_river_buffer_reaches_beyond_the_channel(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        osm = OsmContext(water=[feature("water", lon, lat, {"waterway": "river"})])
        mask = exclusions.build(grid, osm=osm).mask
        # 50 m at a 5 m cell is ten cells; a neighbour well outside the polygon
        # must still be excluded.
        assert mask[20, 25], "the buffer did not extend past the channel itself"


class TestStandingWater:
    def test_an_existing_tank_is_excluded(self) -> None:
        """It scores maximally on depression depth *because it is already a pond*."""
        grid = dem()
        lon, lat = lonlat(grid, 10, 10)
        osm = OsmContext(water=[feature("water", lon, lat, {"natural": "water"})])
        result = exclusions.build(grid, osm=osm)
        assert result.mask[10, 10]
        assert result.removed_by["standing_water"] > 0

    def test_land_cover_water_is_excluded_independently(self) -> None:
        codes = np.full((N, N), 30, dtype=np.uint8)
        codes[5:8, 5:8] = 80
        result = exclusions.build(dem(), land_cover_codes=codes)
        assert result.mask[6, 6]
        assert "land cover" in result.sources

    @pytest.mark.parametrize("code", [80, 90, 95])
    def test_every_water_class_counts(self, code: int) -> None:
        codes = np.full((N, N), 30, dtype=np.uint8)
        codes[5, 5] = code
        assert exclusions.build(dem(), land_cover_codes=codes).mask[5, 5]


class TestBuiltInfrastructure:
    def test_a_building_is_excluded(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 15, 15)
        osm = OsmContext(buildings=[feature("building", lon, lat, {"building": "house"})])
        assert exclusions.build(grid, osm=osm).mask[15, 15]

    def test_a_road_is_excluded(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 15, 15)
        osm = OsmContext(roads=[feature("road", lon, lat, {"highway": "secondary"})])
        assert exclusions.build(grid, osm=osm).mask[15, 15]

    def test_a_cart_track_is_not(self) -> None:
        """Access to a pond site, not an obstruction."""
        grid = dem()
        lon, lat = lonlat(grid, 15, 15)
        osm = OsmContext(tracks=[feature("track", lon, lat, {"highway": "track"})])
        assert not exclusions.build(grid, osm=osm).mask.any()

    def test_built_up_land_cover_is_excluded(self) -> None:
        codes = np.full((N, N), 30, dtype=np.uint8)
        codes[3, 3] = 50
        assert exclusions.build(dem(), land_cover_codes=codes).mask[3, 3]


class TestTheTerrainFallback:
    def test_a_huge_upstream_area_is_treated_as_a_watercourse(self) -> None:
        """The backstop when no vector data answers at all."""
        grid = dem()
        acc = np.zeros((N, N), dtype=np.float64)
        # 2001 ha at 0.0025 ha per cell.
        acc[9, 9] = (exclusions.DEFAULT_MAX_UPSTREAM_HA + 1) / ((CELL**2) / 10_000.0)
        result = exclusions.build(grid, flow_accumulation=acc)
        assert result.mask[9, 9]
        assert result.removed_by["major_channel_by_area"] == 1

    def test_a_normal_nala_survives_it(self) -> None:
        """The sample sheet's best site drains 180 ha and must not be rejected."""
        grid = dem()
        acc = np.full((N, N), 180.0 / ((CELL**2) / 10_000.0), dtype=np.float64)
        assert not exclusions.build(grid, flow_accumulation=acc).mask.any()


class TestItReportsWhatItCouldCheck:
    def test_both_vector_sources_is_high_confidence(self) -> None:
        codes = np.full((N, N), 30, dtype=np.uint8)
        result = exclusions.build(dem(), osm=OsmContext(), land_cover_codes=codes)
        assert result.confidence == "high"

    def test_one_source_is_partial(self) -> None:
        assert exclusions.build(dem(), osm=OsmContext()).confidence == "partial"

    def test_nothing_but_terrain_says_so_loudly(self) -> None:
        result = exclusions.build(dem(), flow_accumulation=np.zeros((N, N)))
        assert result.confidence == "terrain-only"
        blob = " ".join(result.notes)
        assert "could not be excluded" in blob
        assert "already there" in blob

    def test_the_audit_names_each_rule(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        osm = OsmContext(
            water=[feature("water", lon, lat, {"waterway": "river"})],
            buildings=[feature("building", *lonlat(grid, 5, 5), {"building": "yes"})],
        )
        block = exclusions.build(grid, osm=osm).as_dict()
        assert block["removed_by"]["major_watercourse"] > 0
        assert block["removed_by"]["building"] > 0
        assert block["excluded_cells"] > 0

    def test_an_empty_context_excludes_nothing(self) -> None:
        result = exclusions.build(dem(), osm=OsmContext())
        assert not result.mask.any()


def metric_ribbon(tags: dict[str, str], *, length_m: float, width_m: float, epsg: int = 32644):
    """A rectangle of exactly `length_m` x `width_m`, expressed in lon/lat.

    The shape test measures metres, so the fixture is built in metres and
    converted -- guessing a degree size gave a 0.36 ha "river" and tested the
    wrong thing. `classify_water` needs no raster, only a CRS, so no DEM here.
    """
    from pyproj import Transformer

    x0, y0 = 500_000.0, 2_340_000.0
    tf = Transformer.from_crs(epsg, 4326, always_xy=True)
    corners = [
        (x0, y0),
        (x0 + length_m, y0),
        (x0 + length_m, y0 + width_m),
        (x0, y0 + width_m),
        (x0, y0),
    ]
    ring = tuple((float(lon), float(lat)) for lon, lat in (tf.transform(x, y) for x, y in corners))
    return OsmFeature(kind="water", osm_type="way", osm_id=7, tags=tags, rings=(ring,))  # type: ignore[arg-type]


class TestTheArealRiverRegression:
    """OSM maps a large river twice, and we used to recognise only one of them.

    The centreline is `waterway=river`; the wide body actually rendered on the
    map is `natural=water` + `water=river` and carries **no `waterway` tag**. It
    therefore fell through to the standing-water rule and its 0 m buffer -- the
    rule written for a village tank, whose bank is legitimately good ground.

    Measured on the sample sheet: the Shivnath's 563.6 ha body was classified
    that way, the river runs a median 181 m wide so the centreline's 50 m buffer
    never reached the bank, and 64.7 ha of bank and floodplain stayed
    recommendable. Siting duly returned a site 50 m from the river.
    """

    def test_an_areal_river_is_a_major_watercourse(self) -> None:
        f = feature("water", 81.0, 21.0, {"natural": "water", "water": "river"})
        assert exclusions.classify_water(f) == "major"

    def test_a_river_centreline_is_still_a_major_watercourse(self) -> None:
        f = feature("water", 81.0, 21.0, {"waterway": "river"})
        assert exclusions.classify_water(f) == "major"

    def test_an_areal_canal_is_a_major_watercourse(self) -> None:
        f = feature("water", 81.0, 21.0, {"natural": "water", "water": "canal"})
        assert exclusions.classify_water(f) == "major"

    @pytest.mark.parametrize("value", ["lake", "pond", "reservoir", "basin", "lagoon"])
    def test_a_tank_is_standing_water_and_keeps_its_usable_bank(self, value: str) -> None:
        f = feature("water", 81.0, 21.0, {"natural": "water", "water": value})
        assert exclusions.classify_water(f) == "standing"

    @pytest.mark.parametrize("value", ["stream", "drain", "ditch"])
    def test_an_areal_minor_channel_is_still_never_excluded(self, value: str) -> None:
        """The check-dam rule has to survive reading the second tag too."""
        f = feature("water", 81.0, 21.0, {"natural": "water", "water": value})
        assert exclusions.classify_water(f) == "minor"

    def test_the_areal_river_gets_the_river_buffer_not_the_tank_buffer(self) -> None:
        """The behaviour, not just the label: a 50 m standoff must appear."""
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        areal = feature("water", lon, lat, {"natural": "water", "water": "river"})
        mask = exclusions.build(grid, osm=OsmContext(water=[areal])).mask
        assert mask[20, 20], "the water itself must be excluded"
        # 50 m at 5 m cells is ten cells; well beyond the feature, inside the buffer.
        assert mask[20, 26], "the bank of a river must be excluded, unlike a tank's"

    def test_a_tank_of_the_same_shape_leaves_its_bank_available(self) -> None:
        """The contrast that proves the buffer is class-driven, not universal."""
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        tank = feature("water", lon, lat, {"natural": "water", "water": "pond"})
        mask = exclusions.build(grid, osm=OsmContext(water=[tank])).mask
        assert mask[20, 20], "the water itself is still excluded"
        assert not mask[20, 26], "a tank's bank stays buildable"


class TestTheUnlabelledWaterShapeTest:
    """`natural=water` with no `water=*` subtag: shape decides, conservatively.

    Deliberately quiet. A misjudged tank costs one candidate beside an existing
    tank; a missed river puts a pond in a river, so both conditions -- long-and-
    thin *and* larger than 2 ha -- have to hold.
    """

    def test_a_long_thin_large_body_is_treated_as_a_channel(self) -> None:
        # 1.2 km x 60 m = 7.2 ha at 20:1 -- an unlabelled river reach.
        ribbon = metric_ribbon({"natural": "water"}, length_m=1200.0, width_m=60.0)
        assert exclusions.classify_water(ribbon, 32644) == "major"

    def test_a_compact_body_of_the_same_area_is_not(self) -> None:
        # 268 m square = the same 7.2 ha, but 1:1 -- a large village tank.
        blob = metric_ribbon({"natural": "water"}, length_m=268.0, width_m=268.0)
        assert exclusions.classify_water(blob, 32644) == "standing"

    def test_a_long_thin_but_small_body_is_not(self) -> None:
        """A field channel is thin and harmless; the area floor keeps it out."""
        # 300 m x 5 m = 0.15 ha at 60:1: elongated, but far under 2 ha.
        sliver = metric_ribbon({"natural": "water"}, length_m=300.0, width_m=5.0)
        assert exclusions.classify_water(sliver, 32644) == "standing"

    def test_without_a_crs_the_shape_test_is_skipped_rather_than_guessed(self) -> None:
        ribbon = metric_ribbon({"natural": "water"}, length_m=1200.0, width_m=60.0)
        assert exclusions.classify_water(ribbon, None) == "standing"

    def test_an_explicit_subtag_always_beats_the_shape_test(self) -> None:
        """A long thin reservoir is a reservoir; tags are evidence, shape is a guess."""
        ribbon = metric_ribbon(
            {"natural": "water", "water": "reservoir"}, length_m=1200.0, width_m=60.0
        )
        assert exclusions.classify_water(ribbon, 32644) == "standing"

    def test_a_shape_promotion_is_reported(self) -> None:
        ribbon = metric_ribbon({"natural": "water"}, length_m=1200.0, width_m=60.0)
        result = exclusions.build(dem(), osm=OsmContext(water=[ribbon]))
        assert any("shape alone" in n for n in result.notes)


class TestLandCoverCarriesAMargin:
    """Raw class pixels give no margin, so the waterline itself stayed sitable.

    This is also the only water protection left when OSM is the source that
    failed, which is why it is not left undilated.
    """

    def test_water_pixels_are_dilated(self) -> None:
        grid = dem()
        codes = np.zeros((N, N), dtype=np.uint8)
        codes[20, 20] = 80  # permanent water
        mask = exclusions.build(grid, land_cover_codes=codes).mask
        assert mask[20, 20]
        assert mask[20, 23], "20 m at 5 m cells must reach four cells"
        assert not mask[20, 30], "and no further"

    def test_the_margin_is_round_not_square(self) -> None:
        """A square element would reach 1.41x further on the diagonal."""
        grid = dem()
        codes = np.zeros((N, N), dtype=np.uint8)
        codes[20, 20] = 80
        mask = exclusions.build(grid, land_cover_codes=codes).mask
        assert not mask[24, 24], "the corner of the bounding square is outside 20 m"

    def test_built_up_is_not_dilated_by_the_water_margin(self) -> None:
        grid = dem()
        codes = np.zeros((N, N), dtype=np.uint8)
        codes[20, 20] = 50  # built-up
        mask = exclusions.build(grid, land_cover_codes=codes).mask
        assert mask[20, 20]
        assert not mask[20, 23]

    def test_a_zero_margin_is_honoured(self) -> None:
        grid = dem()
        codes = np.zeros((N, N), dtype=np.uint8)
        codes[20, 20] = 80
        mask = exclusions.build(grid, land_cover_codes=codes, land_cover_water_buffer_m=0.0).mask
        assert mask[20, 20]
        assert not mask[20, 21]


class TestTheRelationGapIsReported:
    """A river mapped as a multipolygon relation is absent, not mis-buffered.

    `build_query` asks for ways only, on measured cost grounds. The supplement
    that fetches relations is best-effort, so whether it landed has to be
    visible: "no relations here" and "relations not fetched" are different facts.
    """

    def test_a_window_without_relations_says_so(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        ctx = OsmContext(water=[feature("water", lon, lat, {"waterway": "river"})])
        ctx.water_relations = False
        assert any("relations" in n for n in exclusions.build(grid, osm=ctx).notes)

    def test_a_window_with_relations_does_not_warn(self) -> None:
        grid = dem()
        lon, lat = lonlat(grid, 20, 20)
        ctx = OsmContext(water=[feature("water", lon, lat, {"waterway": "river"})])
        ctx.water_relations = True
        assert not any("relations" in n for n in exclusions.build(grid, osm=ctx).notes)
