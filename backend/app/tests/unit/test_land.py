"""Available-land identification, against synthetic ground (HLD §6.4).

The pipeline is mostly masks, so the tests are mostly about whether the right
cells survive. Two things get asserted hard because they are the ones that would
fail silently: that an OSM buffer lands in the right *place* (a reprojection
error moves it kilometres without raising), and that the buffer is the right
*size* in metres.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyproj import Transformer

from app.providers.elevation.base import DemGrid
from app.providers.vector.overpass import OsmContext, OsmFeature
from app.services import land

CELL = 5.0
N = 60
EPSG = 32644
ORIGIN_X = 500_000.0
ORIGIN_Y = 2_340_000.0


def flat_grid(n: int = N, cell: float = CELL) -> DemGrid:
    """Dead-flat ground, so slope never excludes anything by accident."""
    return DemGrid(
        elevation=np.full((n, n), 300.0, dtype=np.float32),
        transform=(cell, 0.0, ORIGIN_X, 0.0, -cell, ORIGIN_Y + n * cell),
        epsg=EPSG,
        cell_size_m=cell,
    )


def zero_slope(n: int = N) -> np.ndarray:
    return np.zeros((n, n), dtype=np.float32)


def all_grassland(n: int = N) -> np.ndarray:
    """WorldCover 30 = grassland, which is included ground."""
    return np.full((n, n), 30, dtype=np.uint8)


def lonlat_of(dem: DemGrid, row: int, col: int) -> tuple[float, float]:
    """The lon/lat of a cell centre -- so fixtures are placed by grid position."""
    x, y = dem.xy(row, col)
    lon, lat = Transformer.from_crs(dem.epsg, 4326, always_xy=True).transform(x, y)
    return float(lon), float(lat)


def point_feature(kind: str, lon: float, lat: float, size_deg: float = 1e-5) -> OsmFeature:
    """A tiny closed square at (lon, lat), standing in for a building or tank."""
    ring = (
        (lon, lat),
        (lon + size_deg, lat),
        (lon + size_deg, lat + size_deg),
        (lon, lat + size_deg),
        (lon, lat),
    )
    return OsmFeature(kind=kind, osm_type="way", osm_id=1, tags={}, rings=(ring,))  # type: ignore[arg-type]


class TestTheLandCoverRules:
    def test_bare_grass_and_shrub_are_included_cropland_is_not(self) -> None:
        codes = np.array([[60, 30, 20, 40, 50, 80]], dtype=np.uint8)
        included, _ = land.lulc_masks(codes)
        assert included.tolist() == [[True, True, True, False, False, False]]

    def test_cropland_can_be_opted_in(self) -> None:
        codes = np.array([[40]], dtype=np.uint8)
        included, _ = land.lulc_masks(codes, allow_cropland=True)
        assert included.tolist() == [[True]]

    def test_built_up_water_forest_and_snow_are_excluded(self) -> None:
        codes = np.array([[50, 80, 10, 70, 95, 30]], dtype=np.uint8)
        _, excluded = land.lulc_masks(codes)
        assert excluded.tolist() == [[True, True, True, True, True, False]]


class TestSlopeExcludesSteepGround:
    def test_the_default_is_five_percent_not_siting_s_eight(self) -> None:
        """The two thresholds answer different questions; the HLD fixes 5 % here."""
        assert land.DEFAULT_MAX_SLOPE_PCT == 5.0

    def test_ground_steeper_than_the_threshold_is_dropped(self) -> None:
        dem = flat_grid()
        slope = zero_slope()
        slope[:, :30] = 9.0  # left half too steep
        result = land.available_land(
            dem, slope_pct=slope, land_cover=all_grassland(), kernel_cells=0
        )
        assert not result.available[:, :30].any(), "steep ground survived"
        assert result.available[:, 30:].all(), "flat ground was dropped"
        assert result.removed_by["slope"] == 30 * N


class TestTheOsmExclusionLandsWhereItShould:
    def test_a_building_buffer_is_centred_on_the_building(self) -> None:
        """A reprojection slip moves the buffer kilometres without raising."""
        dem = flat_grid()
        lon, lat = lonlat_of(dem, 30, 30)
        ctx = OsmContext(buildings=[point_feature("building", lon, lat)])
        mask, counts = land.osm_exclusion_mask(ctx, dem)

        assert counts["building"] > 0, "the building rasterised to nothing"
        assert mask[30, 30], "the buffer missed the cell the building sits in"
        rows, cols = np.nonzero(mask)
        # Centroid of the burned area should sit on the building, within a cell.
        assert abs(rows.mean() - 30) <= 1.5, rows.mean()
        assert abs(cols.mean() - 30) <= 1.5, cols.mean()

    def test_the_buffer_is_the_documented_number_of_metres(self) -> None:
        """50 m at a 5 m cell is 10 cells, so the burned patch spans ~21 cells."""
        dem = flat_grid()
        lon, lat = lonlat_of(dem, 30, 30)
        ctx = OsmContext(buildings=[point_feature("building", lon, lat)])
        mask, _ = land.osm_exclusion_mask(ctx, dem)
        rows, cols = np.nonzero(mask)
        span_cells = max(rows.max() - rows.min(), cols.max() - cols.min()) + 1
        expected = 2 * land.BUFFER_M["building"] / CELL
        assert expected - 3 <= span_cells <= expected + 4, (
            f"{span_cells} cells across; a {land.BUFFER_M['building']} m buffer "
            f"at a {CELL} m cell should be about {expected:.0f}"
        )

    def test_water_is_buffered_further_than_a_road_and_a_track_least(self) -> None:
        """The ordering encodes the policy: don't duplicate a tank, do allow access."""
        assert land.BUFFER_M["water"] > land.BUFFER_M["building"]
        assert land.BUFFER_M["building"] > land.BUFFER_M["road"]
        assert land.BUFFER_M["road"] > land.BUFFER_M["track"]

    def test_an_empty_context_removes_nothing(self) -> None:
        dem = flat_grid()
        mask, counts = land.osm_exclusion_mask(OsmContext(), dem)
        assert not mask.any()
        assert set(counts.values()) == {0}

    def test_osm_never_rescues_land_the_cover_rejected(self) -> None:
        """OSM silence is not evidence of open ground, so it only subtracts."""
        dem = flat_grid()
        built_up = np.full((N, N), 50, dtype=np.uint8)  # all built-up
        result = land.available_land(
            dem, slope_pct=zero_slope(), land_cover=built_up, osm=OsmContext(), kernel_cells=0
        )
        assert not result.available.any()
        assert result.parcels == ()


class TestMorphologicalCleaning:
    def test_opening_removes_a_single_stray_cell(self) -> None:
        mask = np.zeros((30, 30), dtype=bool)
        mask[15, 15] = True
        assert not land.clean(mask).any(), "an isolated cell is speckle, not a parcel"

    def test_closing_fills_a_pinhole_in_a_solid_patch(self) -> None:
        mask = np.zeros((30, 30), dtype=bool)
        mask[5:25, 5:25] = True
        mask[15, 15] = False
        cleaned = land.clean(mask)
        assert cleaned[15, 15], "the pinhole was not closed"

    def test_a_solid_block_survives_intact(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:30, 10:30] = True
        cleaned = land.clean(mask)
        # Opening then closing an ellipse over a square rounds its corners
        # slightly; the bulk must survive.
        assert cleaned.sum() >= 0.9 * mask.sum()

    def test_cleaning_can_be_switched_off(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[5, 5] = True
        assert land.clean(mask, kernel_cells=0).sum() == 1


class TestParcelExtraction:
    def test_two_separated_patches_become_two_parcels(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[5:20, 5:20] = True
        mask[35:55, 35:55] = True
        parcels, dropped = extract(mask, dem)
        assert len(parcels) == 2, [p.area_m2 for p in parcels]
        assert dropped == 0

    def test_the_largest_parcel_is_numbered_one(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[5:12, 5:12] = True  # 49 cells
        mask[30:50, 30:50] = True  # 400 cells
        parcels, _ = extract(mask, dem)
        assert parcels[0].parcel_id == 1
        assert parcels[0].area_m2 > parcels[1].area_m2
        assert [p.parcel_id for p in parcels] == [1, 2]

    def test_a_patch_below_the_minimum_area_is_dropped_and_counted(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[5, 5] = True  # one 5 m cell = 25 m2, below the 400 m2 floor
        parcels, dropped = extract(mask, dem)
        assert parcels == ()
        assert dropped == 1

    def test_area_is_cell_count_times_cell_area(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[10:20, 10:20] = True  # 100 cells at 25 m2
        parcels, _ = extract(mask, dem)
        assert parcels[0].area_m2 == pytest.approx(100 * CELL**2)
        assert parcels[0].area_ha == pytest.approx(0.25)

    def test_the_centroid_is_inside_the_analysis_window(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[20:40, 20:40] = True
        parcels, _ = extract(mask, dem)
        lon, lat = parcels[0].centroid_lonlat
        assert 80.0 < lon < 84.0, lon
        assert 20.0 < lat < 22.5, lat

    def test_slope_attributes_come_from_the_parcel_s_own_cells(self) -> None:
        dem = flat_grid()
        slope = zero_slope()
        slope[10:20, 10:20] = 3.0
        slope[40:50, 40:50] = 1.0
        mask = np.zeros((N, N), dtype=bool)
        mask[10:20, 10:20] = True
        parcels, _ = extract(mask, dem, slope_pct=slope)
        assert parcels[0].mean_slope_pct == pytest.approx(3.0)
        assert parcels[0].max_slope_pct == pytest.approx(3.0)

    def test_distance_to_a_road_is_measured_in_metres(self) -> None:
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[20:30, 20:30] = True
        road = np.zeros((N, N), dtype=bool)
        road[20, 10] = True  # 10 cells west of the parcel's nearest edge
        parcels, _ = extract(mask, dem, road_mask=road)
        assert parcels[0].distance_to_road_m == pytest.approx(10 * CELL, rel=0.01)

    def test_ownership_is_null_rather_than_guessed(self) -> None:
        """Tenure needs an uploaded cadastral layer (FR-11); inventing it is worse."""
        dem = flat_grid()
        mask = np.zeros((N, N), dtype=bool)
        mask[20:40, 20:40] = True
        parcels, _ = extract(mask, dem)
        assert parcels[0].as_feature()["properties"]["ownership"] is None


def extract(mask, dem, **kw):  # type: ignore[no-untyped-def]
    return land.extract_parcels(mask, dem, **kw)


class TestTheWholePipeline:
    def test_open_grassland_yields_one_big_parcel(self) -> None:
        dem = flat_grid()
        result = land.available_land(dem, slope_pct=zero_slope(), land_cover=all_grassland())
        assert len(result.parcels) == 1
        # The whole window less whatever the morphology trims at the edges.
        assert result.total_available_m2 > 0.8 * N * N * CELL**2

    def test_a_building_carves_a_hole_in_the_parcel(self) -> None:
        dem = flat_grid()
        lon, lat = lonlat_of(dem, 30, 30)
        ctx = OsmContext(buildings=[point_feature("building", lon, lat)])
        without = land.available_land(dem, slope_pct=zero_slope(), land_cover=all_grassland())
        with_house = land.available_land(
            dem, slope_pct=zero_slope(), land_cover=all_grassland(), osm=ctx
        )
        assert with_house.total_available_m2 < without.total_available_m2
        assert with_house.removed_by["osm_building"] > 0

    def test_the_audit_says_which_rule_removed_what(self) -> None:
        dem = flat_grid()
        slope = zero_slope()
        slope[:10, :] = 20.0
        result = land.available_land(dem, slope_pct=slope, land_cover=all_grassland())
        assert result.removed_by["slope"] == 10 * N
        block = result.as_dict()
        assert block["criteria"]["max_slope_pct"] == 5.0
        assert block["removed_by"]["slope"] == 10 * N

    def test_it_works_without_land_cover_and_says_so(self) -> None:
        dem = flat_grid()
        result = land.available_land(dem, slope_pct=zero_slope(), land_cover=None)
        assert result.parcels, "terrain-only should still yield land"
        assert result.removed_by["land_cover"] == 0

    def test_the_feature_collection_is_valid_geojson(self) -> None:
        dem = flat_grid()
        result = land.available_land(dem, slope_pct=zero_slope(), land_cover=all_grassland())
        fc = result.feature_collection()
        assert fc["type"] == "FeatureCollection"
        assert fc["features"], "no features emitted"
        for feature in fc["features"]:
            assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
            assert feature["properties"]["area_ha"] > 0

    def test_nothing_available_is_an_empty_collection_not_an_error(self) -> None:
        dem = flat_grid()
        result = land.available_land(
            dem, slope_pct=np.full((N, N), 50.0, dtype=np.float32), land_cover=all_grassland()
        )
        assert result.parcels == ()
        assert result.feature_collection()["features"] == []
        assert result.as_dict()["total_available_ha"] == 0
