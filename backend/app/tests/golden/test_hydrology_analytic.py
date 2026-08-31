"""Golden tests for the hydrology, on surfaces whose answers are known exactly.

These are the evidence that the D8 implementation is *correct*, not merely
self-consistent. Two bugs were caught here that produced plausible-looking but
wrong catchments:

  1. Inverted neighbour-offset signs, so `dc=+1` compared against the West
     neighbour while labelling it East. `TestNeighbourSlices` pins that.
  2. Filling depressions to *exactly* the spill elevation, leaving the filled
     cell tied with its own outflow neighbour and the water stuck.
     `TestPriorityFloodInvariant` pins that.

Everything here uses hand-built arrays, so an assertion failure localises to the
algorithm rather than to the interpolation upstream of it.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.elevation.base import DemGrid
from app.services import hydrology as hyd
from app.services.hydrology import NEIGHBOURS, neighbour_slices

CELL = 1.0
# ESRI D8 codes
E, SE, S, SW, W, NW, N, NE = 1, 2, 4, 8, 16, 32, 64, 128


def grid(z: np.ndarray, cell: float = CELL) -> DemGrid:
    """Wrap a raw array as a north-up DemGrid in a metric CRS."""
    return DemGrid(
        elevation=z.astype(np.float32),
        transform=(cell, 0.0, 0.0, 0.0, -cell, float(z.shape[0]) * cell),
        epsg=32643,
        cell_size_m=cell,
    )


def plane_south(n: int = 16) -> np.ndarray:
    """Elevation falls as row increases, i.e. downhill toward the south."""
    return np.tile(np.arange(n, dtype=float)[::-1][:, None], (1, n))


def condition_and_route(z: np.ndarray, cell: float = CELL):  # type: ignore[no-untyped-def]
    dem = grid(z, cell)
    cond = hyd.fill_depressions(dem)
    return dem, cond, hyd.build_flow(dem, cond)


class TestNeighbourSlices:
    """The offset arithmetic, pinned. Inverting it routes water backwards."""

    @pytest.mark.parametrize(("dr", "dc", "code"), [(d[0], d[1], d[2]) for d in NEIGHBOURS])
    def test_slices_align_cell_with_its_true_neighbour(self, dr: int, dc: int, code: int) -> None:
        shape = (5, 7)
        rr = np.arange(shape[0])[:, None].repeat(shape[1], 1)
        cc = np.arange(shape[1])[None, :].repeat(shape[0], 0)
        h, t = neighbour_slices(shape, dr, dc)
        assert np.array_equal(rr[h] + dr, rr[t]), f"row offset wrong for code {code}"
        assert np.array_equal(cc[h] + dc, cc[t]), f"col offset wrong for code {code}"

    def test_slices_never_leave_the_array(self) -> None:
        shape = (4, 6)
        for dr, dc, _code, _diag in NEIGHBOURS:
            h, t = neighbour_slices(shape, dr, dc)
            for sl in (*h, *t):
                assert sl.start >= 0
            assert h[0].stop <= shape[0] and t[0].stop <= shape[0]
            assert h[1].stop <= shape[1] and t[1].stop <= shape[1]

    def test_the_eight_codes_are_the_esri_convention(self) -> None:
        # 32 64 128 / 16  0  1 / 8  4  2  -- row increases southward.
        expected = {
            (0, 1): E,
            (1, 1): SE,
            (1, 0): S,
            (1, -1): SW,
            (0, -1): W,
            (-1, -1): NW,
            (-1, 0): N,
            (-1, 1): NE,
        }
        actual = {(dr, dc): code for dr, dc, code, _ in NEIGHBOURS}
        assert actual == expected


class TestPriorityFloodInvariant:
    """After conditioning, D8 must be *total*: every non-outlet cell drains."""

    def test_clean_plane_needs_no_filling(self) -> None:
        _, cond, _ = condition_and_route(plane_south())
        assert cond.filled_cells == 0
        assert cond.max_fill_depth_m == 0.0

    def test_no_interior_cell_lacks_a_lower_neighbour(self) -> None:
        z = plane_south()
        z[6:10, 6:10] -= 5.0  # carve a pit
        dem, cond, _ = condition_and_route(z)
        stuck = hyd.cells_without_lower_neighbour(cond.filled, cond.valid)
        edge = np.zeros_like(cond.valid)
        edge[0, :] = edge[-1, :] = True
        edge[:, 0] = edge[:, -1] = True
        outlets = cond.valid & (edge | hyd._dilate(~cond.valid))
        # Subset, not equality: most outlets still drain to another outlet.
        assert np.all(stuck <= outlets), "an interior cell has nowhere to drain"

    def test_pit_is_raised_above_its_outflow_not_level_with_it(self) -> None:
        z = plane_south()
        z[6:10, 6:10] -= 5.0
        _, cond, flow = condition_and_route(z)
        # The pit spills south, where the plane sits at 5.0.
        pit = cond.filled[6:10, 6:10]
        assert pit.min() > 5.0, "filled to exactly the spill level -- water cannot leave"
        assert pit.max() < 5.0 + 1e-3, "epsilon accumulated far more than expected"
        assert not (flow.direction[6:10, 6:10] == 0).any(), "a filled pit cell is still a sink"

    def test_fill_depth_records_the_real_depth(self) -> None:
        """Fill depth is (spill level - original), not the depth of the carve.

        plane_south gives z[row] = 15 - row, so before carving z[7]=8 and z[8]=7.
        Subtracting 3 leaves the pit floor at 5 and 4. The lowest surrounding
        cell is row 9 at elevation 6, so 6 is the spill level and the deepest
        cell (4) is raised by 2 m -- not by the 3 m that was carved out.
        """
        z = plane_south()
        z[7:9, 7:9] -= 3.0
        _, cond, _ = condition_and_route(z)
        assert cond.filled_cells == 4
        assert cond.max_fill_depth_m == pytest.approx(2.0, abs=0.01)

    def test_conditioning_never_lowers_the_surface(self) -> None:
        z = plane_south()
        z[5:11, 5:11] -= 4.0
        dem, cond, _ = condition_and_route(z)
        orig = dem.elevation.astype(np.float64)
        assert np.all(cond.filled[cond.valid] >= orig[cond.valid] - 1e-9)

    def test_nodata_is_treated_as_an_outlet(self) -> None:
        z = plane_south()
        z[:, 12:] = np.nan  # survey edge partway across
        _, cond, flow = condition_and_route(z)
        assert cond.outlet_cells > 0
        assert np.isnan(cond.filled[0, 13])
        assert not flow.valid[0, 13]


class TestFlowDirection:
    def test_uniform_south_slope_routes_due_south(self) -> None:
        _, _, flow = condition_and_route(plane_south())
        interior = flow.direction[1:-1, 1:-1]
        assert np.all(interior == S), f"got codes {np.unique(interior)}"

    def test_reversing_the_slope_reverses_the_flow(self) -> None:
        _, _, flow = condition_and_route(plane_south()[::-1].copy())
        assert np.all(flow.direction[1:-1, 1:-1] == N)

    def test_west_and_east(self) -> None:
        z = np.tile(np.arange(16, dtype=float)[::-1][None, :], (16, 1))  # falls eastward
        _, _, flow = condition_and_route(z)
        assert np.all(flow.direction[1:-1, 1:-1] == E)
        _, _, flow2 = condition_and_route(z[:, ::-1].copy())
        assert np.all(flow2.direction[1:-1, 1:-1] == W)

    def test_diagonal_distance_weighting_is_applied(self) -> None:
        """The test that catches an unweighted D8.

        z = -(row + 0.2*col): the drop due south is 1.0 over one cell, and the
        drop to the south-east is 1.2 over sqrt(2) cells. Weighted, south is
        steeper (1.00 vs 0.85) and must win. *Unweighted*, south-east looks
        steeper (1.2 vs 1.0) and would win -- so this discriminates.
        """
        rr, cc = np.mgrid[0:16, 0:16]
        z = -(rr + 0.2 * cc).astype(float)
        _, _, flow = condition_and_route(z)
        interior = flow.direction[1:-1, 1:-1]
        assert np.all(interior == S), (
            f"expected due south (weighted), got {np.unique(interior)} -- "
            "diagonal distance weighting is probably missing"
        )

    def test_true_diagonal_slope_routes_diagonally(self) -> None:
        rr, cc = np.mgrid[0:16, 0:16]
        _, _, flow = condition_and_route(-(rr + cc).astype(float))
        assert np.all(flow.direction[1:-1, 1:-1] == SE)


class TestFlowAccumulation:
    def test_plane_accumulates_one_column_per_column(self) -> None:
        n = 16
        _, _, flow = condition_and_route(plane_south(n))
        # Every cell drains straight down its own column, so the bottom row
        # carries exactly n cells and the top row carries 1.
        assert flow.accumulation[-1, :].tolist() == [n] * n
        assert flow.accumulation[0, :].tolist() == [1] * n

    def test_accumulation_increases_downstream(self) -> None:
        _, _, flow = condition_and_route(plane_south())
        col = flow.accumulation[:, 8]
        assert np.all(np.diff(col) > 0)

    def test_every_cell_is_counted_exactly_once(self) -> None:
        """Total flow leaving through the termini equals the number of cells."""
        _, cond, flow = condition_and_route(plane_south())
        termini = (flow.direction == 0) & flow.valid
        assert int(flow.accumulation[termini].sum()) == int(flow.valid.sum())

    def test_conservation_holds_with_a_depression_present(self) -> None:
        z = plane_south()
        z[6:10, 6:10] -= 5.0
        _, _, flow = condition_and_route(z)
        termini = (flow.direction == 0) & flow.valid
        assert int(flow.accumulation[termini].sum()) == int(flow.valid.sum())

    def test_minimum_accumulation_is_one(self) -> None:
        _, _, flow = condition_and_route(plane_south())
        assert int(flow.accumulation[flow.valid].min()) == 1

    def test_no_cycles_are_produced(self) -> None:
        # flow_accumulation raises on a cycle; a rough surface is the stress case.
        rng = np.random.default_rng(20260826)
        z = plane_south(24) * 3.0 + rng.normal(0, 0.8, (24, 24))
        _, _, flow = condition_and_route(z)
        assert int(flow.accumulation.max()) > 0


class TestCatchment:
    def test_outlet_accumulation_equals_catchment_size(self) -> None:
        """The defining invariant of a correct delineation."""
        dem, _, flow = condition_and_route(plane_south())
        cat = hyd.delineate_catchment(dem, flow, 15, 8)
        assert cat.accumulation_at_outlet == cat.cell_count

    def test_plane_catchment_is_exactly_the_column_above(self) -> None:
        n = 16
        dem, _, flow = condition_and_route(plane_south(n))
        cat = hyd.delineate_catchment(dem, flow, n - 1, 8)
        assert cat.cell_count == n
        assert np.array_equal(np.nonzero(cat.mask)[1], np.full(n, 8))

    def test_catchment_grows_downstream(self) -> None:
        dem, _, flow = condition_and_route(plane_south())
        sizes = [hyd.delineate_catchment(dem, flow, r, 8).cell_count for r in (3, 7, 11, 15)]
        assert sizes == sorted(sizes)
        assert sizes[0] < sizes[-1]

    def test_area_uses_the_cell_size(self) -> None:
        dem, _, flow = condition_and_route(plane_south(), cell=10.0)
        cat = hyd.delineate_catchment(dem, flow, 15, 8)
        assert cat.area_m2 == pytest.approx(cat.cell_count * 100.0)
        assert cat.area_ha == pytest.approx(cat.area_m2 / 1e4)
        assert cat.area_km2 == pytest.approx(cat.area_m2 / 1e6)

    def test_divide_is_not_crossed(self) -> None:
        """Two valleys separated by a ridge must not share a catchment."""
        rr, cc = np.mgrid[0:24, 0:24]
        # V-shaped ridge down the middle, both halves sloping south.
        z = (24 - rr) + 4.0 * np.abs(cc - 11.5) / 11.5
        dem, _, flow = condition_and_route(z)
        left = hyd.delineate_catchment(dem, flow, 23, 3)
        right = hyd.delineate_catchment(dem, flow, 23, 20)
        assert not (left.mask & right.mask).any(), "catchments overlap across the divide"

    def test_out_of_range_outlet_raises(self) -> None:
        dem, _, flow = condition_and_route(plane_south())
        with pytest.raises(IndexError):
            hyd.delineate_catchment(dem, flow, 999, 0)

    def test_edge_contact_is_reported(self) -> None:
        dem, _, flow = condition_and_route(plane_south())
        cat = hyd.delineate_catchment(dem, flow, 15, 8)
        assert cat.touches_grid_edge is True

    def test_deterministic(self) -> None:
        dem, _, flow = condition_and_route(plane_south())
        a = hyd.delineate_catchment(dem, flow, 15, 8)
        b = hyd.delineate_catchment(dem, flow, 15, 8)
        assert np.array_equal(a.mask, b.mask)


class TestSnapping:
    def test_snap_moves_onto_the_drainage_line(self) -> None:
        """HLD CH-12: an unsnapped click off the channel gives a tiny catchment."""
        rr, cc = np.mgrid[0:24, 0:24]
        z = (24 - rr) + 3.0 * np.abs(cc - 12) / 12.0  # channel along col 12
        dem, _, flow = condition_and_route(z)
        off = hyd.delineate_catchment(dem, flow, 20, 5, snap_radius_cells=0)
        on = hyd.delineate_catchment(dem, flow, 20, 5, snap_radius_cells=8)
        assert on.cell_count > off.cell_count
        assert on.snapped_from == (20, 5)
        assert on.snap_distance_m > 0

    def test_no_snap_reports_no_movement(self) -> None:
        dem, _, flow = condition_and_route(plane_south())
        cat = hyd.delineate_catchment(dem, flow, 15, 8, snap_radius_cells=0)
        assert cat.snapped_from is None
        assert cat.snap_distance_m == 0.0

    def test_snapping_never_lowers_accumulation(self) -> None:
        rr, cc = np.mgrid[0:24, 0:24]
        z = (24 - rr) + 3.0 * np.abs(cc - 12) / 12.0
        dem, _, flow = condition_and_route(z)
        before = int(flow.accumulation[20, 5])
        cat = hyd.delineate_catchment(dem, flow, 20, 5, snap_radius_cells=8)
        assert cat.accumulation_at_outlet >= before


class TestDerivedMetrics:
    def test_slope_of_a_known_plane_is_exact(self) -> None:
        # 1 m fall per 5 m cell = 20 %.
        _, cond, _ = condition_and_route(plane_south(20), cell=5.0)
        sl = hyd.slope_percent(cond.filled, 5.0)
        assert sl[5:-5, 5:-5].mean() == pytest.approx(20.0, abs=0.5)

    def test_flat_terrain_has_zero_slope(self) -> None:
        _, cond, _ = condition_and_route(np.full((16, 16), 42.0))
        sl = hyd.slope_percent(cond.filled, 1.0)
        assert abs(float(np.nanmean(sl[4:-4, 4:-4]))) < 0.01

    def test_longest_flow_path_matches_the_column_length(self) -> None:
        n = 16
        dem, _, flow = condition_and_route(plane_south(n), cell=10.0)
        cat = hyd.delineate_catchment(dem, flow, n - 1, 8)
        length = hyd.longest_flow_path_m(flow.direction, cat, 10.0)
        # n cells in a straight line: (n-1) steps of 10 m.
        assert length == pytest.approx((n - 1) * 10.0)

    def test_diagonal_path_counts_root_two(self) -> None:
        rr, cc = np.mgrid[0:12, 0:12]
        dem, _, flow = condition_and_route(-(rr + cc).astype(float), cell=10.0)
        cat = hyd.delineate_catchment(dem, flow, 11, 11)
        length = hyd.longest_flow_path_m(flow.direction, cat, 10.0)
        assert length > 11 * 10.0, "diagonal steps appear to be counted as 1 cell"

    def test_metrics_are_internally_consistent(self) -> None:
        dem, cond, flow = condition_and_route(plane_south(), cell=5.0)
        cat = hyd.delineate_catchment(dem, flow, 15, 8)
        m = hyd.catchment_metrics(dem, cond, flow, cat)
        assert m["cell_count"] == cat.cell_count
        assert m["area_ha"] == pytest.approx(cat.area_ha, abs=1e-6)
        assert m["relief_m"] == pytest.approx(
            float(m["elevation_max_m"]) - float(m["elevation_min_m"]), abs=0.01  # type: ignore[arg-type]
        )
        assert float(m["compactness_coefficient"]) >= 1.0  # type: ignore[arg-type]

    def test_metrics_serialise(self) -> None:
        import json

        dem, cond, flow = condition_and_route(plane_south())
        cat = hyd.delineate_catchment(dem, flow, 15, 8)
        json.dumps(hyd.catchment_metrics(dem, cond, flow, cat))

    def test_kirpich_tc_matches_the_formula(self) -> None:
        dem, cond, flow = condition_and_route(plane_south(20), cell=10.0)
        cat = hyd.delineate_catchment(dem, flow, 19, 10)
        m = hyd.catchment_metrics(dem, cond, flow, cat)
        length = float(m["longest_flow_path_m"])  # type: ignore[arg-type]
        relief = float(m["relief_m"])  # type: ignore[arg-type]
        expected = 0.01947 * length**0.77 * (relief / length) ** -0.385
        assert float(m["time_of_concentration_min"]) == pytest.approx(  # type: ignore[arg-type]
            expected, rel=0.01
        )

    def test_stream_network_thresholds(self) -> None:
        _, _, flow = condition_and_route(plane_south())
        many = hyd.stream_network(flow, 2)
        few = hyd.stream_network(flow, 12)
        assert int(many.sum()) > int(few.sum())
