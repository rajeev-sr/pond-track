"""Pond sizing: stage-storage, prismoidal geometry, depth choice (MC-18)."""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.elevation.base import DemGrid
from app.services.pond import (
    MAX_POND_FOOTPRINT_M2,
    MAX_YIELD_FRACTION,
    SILT_FRACTION,
    design_pond,
    prismoidal_volume,
    stage_storage_curve,
    usable_footprint_m2,
)


def grid(z: np.ndarray, cell: float = 5.0) -> DemGrid:
    return DemGrid(
        elevation=z.astype(np.float32),
        transform=(cell, 0.0, 500_000.0, 0.0, -cell, 2_340_000.0 + z.shape[0] * cell),
        epsg=32643,
        cell_size_m=cell,
    )


def bowl(n: int = 40, depth: float = 5.0, radius: float = 12.0) -> np.ndarray:
    rr, cc = np.mgrid[0:n, 0:n]
    r = np.hypot(rr - n / 2, cc - n / 2)
    return 100.0 - np.where(r < radius, depth * (1.0 - r / radius), 0.0)


class TestPrismoidalVolume:
    def test_reproduces_hld_worked_example(self) -> None:
        """★ HLD §6.9 Step 6: 60 x 45 m top, 3.5 m deep, 1V:1.5H."""
        v, lb, wb = prismoidal_volume(60.0, 45.0, 3.5, 1.5)
        assert lb == pytest.approx(49.5)
        assert wb == pytest.approx(34.5)
        assert v == pytest.approx(7647.6, abs=0.1)

    def test_sits_between_the_two_prisms_it_interpolates(self) -> None:
        """A truncated pyramid holds less than a box of its top area and more
        than a box of its bottom area."""
        v, lb, wb = prismoidal_volume(50.0, 50.0, 3.0, 1.5)
        assert lb * wb * 3.0 < v < 50.0 * 50.0 * 3.0

    def test_monotonic_in_depth(self) -> None:
        vols = [prismoidal_volume(60.0, 60.0, d, 1.5)[0] for d in (1.0, 2.0, 3.0, 4.0)]
        assert vols == sorted(vols)

    def test_gentler_side_slope_costs_volume(self) -> None:
        steep, _, _ = prismoidal_volume(60.0, 60.0, 3.0, 1.0)
        gentle, _, _ = prismoidal_volume(60.0, 60.0, 3.0, 2.5)
        assert gentle < steep

    def test_closure_guard(self) -> None:
        """With side slope z, a depth d eats 2*z*d from each plan dimension."""
        with pytest.raises(ValueError, match="needs a top wider than"):
            prismoidal_volume(8.0, 8.0, 3.5, 1.5)

    def test_exactly_closing_geometry_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            prismoidal_volume(10.5, 10.5, 3.5, 1.5)  # bottom would be zero


class TestStageStorage:
    def test_volume_and_area_both_increase_with_depth(self) -> None:
        dem = grid(bowl())
        curve = stage_storage_curve(dem, 20, 20, max_depth_m=4.0)
        assert [p.volume_m3 for p in curve] == sorted(p.volume_m3 for p in curve)
        assert [p.area_m2 for p in curve] == sorted(p.area_m2 for p in curve)

    def test_the_curve_stops_where_terrain_stops_containing_the_water(self) -> None:
        """The capped path, which the bowl fixture never reaches.

        A gentle plane has no rim, so the fill spreads to the area cap. Before
        this was fixed the curve carried on past that point and *fell* as depth
        rose -- 79,473 m3 at 2.00 m against 45,336 m3 at 4.25 m on the real Durg
        sheet. A capped fill sums whichever cells the traversal popped first, and
        those subsets are not nested across levels.
        """
        n = 60
        rr, _cc = np.mgrid[0:n, 0:n]
        plane = 100.0 + 0.01 * rr  # 1 % slope, nothing to impound against
        dem = grid(plane, cell=5.0)
        curve = stage_storage_curve(dem, n // 2, n // 2, max_depth_m=4.5)

        assert curve[-1].unbounded, "a featureless plane must hit the area cap"
        assert not any(
            p.unbounded for p in curve[:-1]
        ), "the curve continued past the point where containment failed"
        assert curve[-1].depth_m < 4.5, "the curve should have stopped early"

    def test_volumes_never_fall_as_depth_rises_even_on_open_ground(self) -> None:
        """Storage cannot decrease with depth. This is the invariant that broke."""
        for surface, label in (
            (bowl(), "bowl"),
            (np.full((40, 40), 100.0), "dead flat"),
            (100.0 + 0.01 * np.mgrid[0:60, 0:60][0], "gentle plane"),
        ):
            curve = stage_storage_curve(grid(surface, cell=5.0), 20, 20, max_depth_m=4.5)
            vols = [p.volume_m3 for p in curve]
            assert vols == sorted(vols), f"{label}: storage fell as depth rose -- {vols}"
            areas = [p.area_m2 for p in curve]
            assert areas == sorted(areas), f"{label}: flooded area fell as depth rose"

    def test_starts_empty(self) -> None:
        curve = stage_storage_curve(grid(bowl()), 20, 20)
        assert curve[0].depth_m == 0.0
        assert curve[0].volume_m3 == 0.0

    def test_water_level_tracks_the_site_elevation(self) -> None:
        z = bowl()
        dem = grid(z)
        curve = stage_storage_curve(dem, 20, 20, max_depth_m=2.0)
        base = float(z[20, 20])
        for p in curve:
            assert p.water_level_m == pytest.approx(base + p.depth_m)

    def test_a_bowl_contains_water_and_flat_ground_does_not(self) -> None:
        """The point of measuring from the DEM: terrain shape matters.

        Not "a bowl holds more" -- on unbounded flat ground the fill simply
        spreads and the raw volume is *larger*, which is exactly why an uncapped
        stage-storage curve is misleading. What distinguishes them is containment:
        the bowl reaches a rim, the plain hits the area cap.
        """
        deep = stage_storage_curve(
            grid(bowl(depth=6.0)), 20, 20, max_depth_m=3.0, max_area_m2=10_000.0
        )
        flat = stage_storage_curve(
            grid(np.full((40, 40), 100.0)), 20, 20, max_depth_m=3.0, max_area_m2=10_000.0
        )
        assert not deep[-1].unbounded, "the bowl should be contained by its rim"
        assert flat[-1].unbounded, "flat ground cannot impound water"

    def test_a_ridge_confines_the_pond(self) -> None:
        """Flood-fill from the site: a hollow across a ridge is a different pond."""
        z = np.full((40, 40), 100.0)
        z[5:15, 5:15] = 95.0  # basin A, containing the site
        z[25:35, 25:35] = 95.0  # basin B, unconnected
        curve = stage_storage_curve(grid(z), 10, 10, max_depth_m=4.0)
        # Only basin A's 100 cells can be flooded at 25 m^2 each.
        assert curve[-1].area_m2 <= 100 * 25.0 * 1.2

    def test_rejects_a_nodata_site(self) -> None:
        z = bowl()
        z[20, 20] = np.nan
        with pytest.raises(ValueError, match="no elevation"):
            stage_storage_curve(grid(z), 20, 20)


class TestUsableFootprint:
    def test_measures_the_connected_patch(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:20, 10:20] = True  # 100 cells at 25 m^2 = 2500 m^2
        area, capped = usable_footprint_m2(mask, 15, 15, 5.0)
        assert area == pytest.approx(2500.0)
        assert capped is False

    def test_does_not_cross_a_gap(self) -> None:
        """The buildable patch at the site, not every buildable cell on the map."""
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:15, 10:15] = True
        mask[30:38, 30:38] = True  # a bigger, unconnected patch
        area, _ = usable_footprint_m2(mask, 12, 12, 5.0)
        assert area == pytest.approx(25 * 25.0)

    def test_caps_a_large_tract_and_reports_it(self) -> None:
        area, capped = usable_footprint_m2(np.ones((400, 400), dtype=bool), 200, 200, 5.0)
        assert area == pytest.approx(MAX_POND_FOOTPRINT_M2)
        assert capped is True

    def test_site_outside_the_mask_returns_one_cell(self) -> None:
        area, _ = usable_footprint_m2(np.zeros((20, 20), dtype=bool), 10, 10, 5.0)
        assert area == pytest.approx(25.0)

    def test_rejects_an_out_of_range_site(self) -> None:
        with pytest.raises(ValueError, match="outside the grid"):
            usable_footprint_m2(np.ones((10, 10), dtype=bool), 99, 0, 5.0)


class TestDesignPond:
    def test_produces_a_consistent_design(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=4000.0)
        assert d.depth_m > 0
        assert d.bottom_length_m < d.top_length_m
        assert d.gross_capacity_m3 > 0
        assert d.live_storage_m3 == pytest.approx((1 - SILT_FRACTION) * d.gross_capacity_m3)
        assert d.dead_storage_m3 == pytest.approx(SILT_FRACTION * d.gross_capacity_m3)

    def test_names_a_binding_constraint_it_actually_evaluated(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=4000.0)
        assert d.binding_constraint in d.constraints_evaluated

    def test_small_plot_binds_on_geometry(self) -> None:
        # 100 m2 gives a 10 m side, and a 1V:1.5H pit that deep would need more
        # than 13.5 m -- so the plan area, not the excavation limit, binds.
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=100.0)
        assert d.binding_constraint == "plan_area_geometry"
        assert d.depth_m < 4.5

    def test_large_plot_binds_on_excavation_depth(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0)
        assert d.binding_constraint == "practical_excavation_depth"

    def test_yield_cap_limits_capacity(self) -> None:
        """Never impound more than a sustainable share of the catchment's yield."""
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0, annual_runoff_m3=5_000.0)
        assert d.gross_capacity_m3 <= MAX_YIELD_FRACTION * 5_000.0 * 1.02
        assert d.binding_constraint == "sustainable_yield_share"

    def test_generous_yield_does_not_bind(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=8_000.0, annual_runoff_m3=1e9)
        assert d.binding_constraint != "sustainable_yield_share"

    def test_water_table_caps_depth(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0, water_table_depth_m=3.0)
        assert d.depth_m == pytest.approx(2.0, abs=0.01)  # table - 1 m clearance
        assert d.binding_constraint == "water_table_clearance"

    def test_missing_water_table_is_flagged_not_ignored(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=8_000.0)
        assert any("water-table" in r or "groundwater" in r for r in d.recommendations)

    def test_budget_reduces_depth(self) -> None:
        rich = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0)
        poor = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0, budget_inr=50_000.0)
        assert poor.depth_m <= rich.depth_m

    def test_fill_ratio_is_reported(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=4_000.0, annual_runoff_m3=500_000.0)
        assert d.fill_ratio is not None
        assert 0.0 < d.fill_ratio < 1.0

    def test_cost_scales_with_volume(self) -> None:
        small = design_pond(grid(bowl()), 20, 20, available_area_m2=1_000.0)
        big = design_pond(grid(bowl()), 20, 20, available_area_m2=20_000.0)
        assert big.estimated_cost_inr > small.estimated_cost_inr

    def test_terrain_capacity_is_reported_alongside_the_geometry(self) -> None:
        """Two independent answers to different questions, both shown."""
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=4_000.0)
        assert d.terrain_capacity_m3 >= 0
        assert d.gross_capacity_m3 > 0

    def test_a_plot_too_small_for_a_viable_pond_warns(self) -> None:
        d = design_pond(grid(bowl()), 20, 20, available_area_m2=30.0)
        assert d.warnings
        assert any("minimum" in w or "too small" in w for w in d.warnings)

    def test_serialises(self) -> None:
        import json

        d = json.loads(
            json.dumps(design_pond(grid(bowl()), 20, 20, available_area_m2=4_000.0).as_dict())
        )
        assert d["binding_constraint"]
        assert d["recommended"]["gross_capacity_m3"] > 0
        assert len(d["stage_storage_curve"]) > 1
