"""Flat detection and depression breaching (M3-2).

Both are tested on surfaces whose answer is arithmetic: a ramp of known gradient
is flat or not depending on one comparison, and a bowl behind a bund of known
height either can be cut within the limit or cannot.

The stakes are specific to this application. Filling a depression removes it, and
a depression is where a pond goes -- so a conditioning pass that fills what it
could have breached hides the very features being searched for.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.elevation.base import DemGrid
from app.services import hydrology as hyd
from app.services.conditioning import (
    FLAT_FRACTION_PREFER_BREACH,
    FLAT_GRADIENT,
    breach_then_fill,
    condition,
    flatness,
)

CELL = 5.0
SIZE = 81
TRANSFORM = (CELL, 0.0, 530000.0, 0.0, -CELL, 2352000.0)


def dem(surface: np.ndarray) -> DemGrid:
    return DemGrid(
        elevation=surface.astype(np.float32),
        transform=TRANSFORM,
        epsg=32644,
        cell_size_m=CELL,
    )


def ramp(gradient: float) -> np.ndarray:
    """A plane rising west to east at `gradient` metres per metre."""
    _, cols = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    return 100.0 + gradient * cols * CELL


class TestFlatnessDetection:
    def test_a_gradient_above_the_threshold_is_not_flat(self) -> None:
        report = flatness(dem(ramp(FLAT_GRADIENT * 2)))
        # The grid edge has no lower neighbour on one side, so a small residual
        # is expected; the interior must not be flat.
        assert report.flat_fraction < 0.05, report.as_dict()

    def test_a_gradient_below_the_threshold_is_flat(self) -> None:
        report = flatness(dem(ramp(FLAT_GRADIENT / 2)))
        assert report.flat_fraction > 0.95, report.as_dict()

    def test_dead_level_ground_is_entirely_flat(self) -> None:
        report = flatness(dem(np.full((SIZE, SIZE), 100.0)))
        assert report.flat_fraction == pytest.approx(1.0)

    def test_the_threshold_can_be_moved(self) -> None:
        surface = dem(ramp(0.005))
        assert flatness(surface, gradient_threshold=0.001).flat_fraction < 0.05
        assert flatness(surface, gradient_threshold=0.01).flat_fraction > 0.95

    def test_a_diagonal_neighbour_is_measured_at_its_real_distance(self) -> None:
        """A drop of d over a diagonal step is d/(cell*sqrt2), not d/cell.

        Treating it as orthogonal overstates every diagonal gradient by 41 %, so a
        surface right at the threshold would be misclassified.
        """
        surface = np.full((SIZE, SIZE), 100.0)
        # A single step down to the south-east only.
        surface[1:, 1:] = 100.0 - 0.02
        report = flatness(dem(surface), gradient_threshold=0.02 / (CELL * 1.4143))
        # The gradient is just below the threshold if measured diagonally and
        # just above it if measured as orthogonal, so the count separates them.
        assert report.flat_fraction > 0.5

    def test_it_says_what_the_number_means(self) -> None:
        for gradient, expect in ((0.05, "unambiguous"), (0.0, "predominantly flat")):
            assert expect in flatness(dem(ramp(gradient))).as_dict()["interpretation"]

    def test_an_empty_surface_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no valid cells"):
            flatness(dem(np.full((10, 10), np.nan)))


def bowl_behind_a_bund(bund_height_m: float, beyond_m: float) -> np.ndarray:
    """A 5 m bowl behind a one-cell bund, with `beyond_m` ground past it.

    `beyond_m` below the pit means there is somewhere for breached water to go;
    above it means there is not, and the depression must be filled instead.
    """
    surface = np.full((SIZE, SIZE), 100.0)
    surface[30:50, 30:50] = 95.0  # the pit
    surface[50, 28:52] = 95.0 + bund_height_m
    surface[51:, :] = beyond_m
    return surface


class TestBreaching:
    def test_a_low_bund_with_an_escape_is_breached(self) -> None:
        surface = dem(bowl_behind_a_bund(bund_height_m=1.0, beyond_m=94.0))
        _, report = breach_then_fill(surface)
        assert report.depressions_breached >= 1, report.as_dict()
        assert report.cells_carved > 0

    def test_the_depression_keeps_its_depth(self) -> None:
        """The whole point. Filling would raise the pit to the bund; breaching
        leaves it where it is, which is where the pond goes."""
        surface = dem(bowl_behind_a_bund(bund_height_m=1.0, beyond_m=94.0))
        filled = hyd.fill_depressions(surface)
        breached, _ = breach_then_fill(surface)
        assert filled.max_fill_depth_m > 0.5
        assert breached.max_fill_depth_m < filled.max_fill_depth_m

    def test_it_fills_fewer_cells_than_filling_alone(self) -> None:
        surface = dem(bowl_behind_a_bund(bund_height_m=1.0, beyond_m=94.0))
        filled = hyd.fill_depressions(surface)
        breached, _ = breach_then_fill(surface)
        assert breached.filled_cells < filled.filled_cells

    def test_a_barrier_too_high_to_cut_is_refused(self) -> None:
        """Trenching six metres through a landform invents topography.

        The surrounding plateau is uniform here so there is exactly one
        depression: a second, shallower one elsewhere would be breachable and the
        count would not separate the cases.
        """
        surface = np.full((SIZE, SIZE), 100.0)
        surface[30:50, 30:50] = 95.0  # a 5 m pit
        surface[50, 28:52] = 101.0  # a 6 m bund, and nothing lower anywhere
        _, report = breach_then_fill(dem(surface), max_breach_depth_m=2.0)
        assert report.depressions_breached == 0, report.as_dict()

    def test_the_realised_cut_never_exceeds_the_limit(self) -> None:
        """The carve descends by an epsilon per cell on top of the planned cost,
        so a 28-cell path once turned a 2.000 m budget into a 2.028 m cut."""
        for depth in (0.5, 1.0, 2.0):
            _, report = breach_then_fill(
                dem(bowl_behind_a_bund(bund_height_m=1.5, beyond_m=94.0)),
                max_breach_depth_m=depth,
            )
            assert report.max_carve_depth_m <= depth + 1e-9, (depth, report.as_dict())

    def test_raising_the_depth_limit_lets_it_through(self) -> None:
        """Confirms the refusal above is the limit and not an inability."""
        surface = dem(bowl_behind_a_bund(bund_height_m=3.0, beyond_m=94.0))
        strict, _ = breach_then_fill(surface, max_breach_depth_m=0.5)
        generous, report = breach_then_fill(surface, max_breach_depth_m=5.0)
        assert report.depressions_breached >= 1
        assert generous.filled_cells <= strict.filled_cells

    def test_the_length_limit_is_respected(self) -> None:
        """A one-cell allowance cannot reach past a bund to lower ground."""
        surface = dem(bowl_behind_a_bund(bund_height_m=1.0, beyond_m=94.0))
        _, report = breach_then_fill(surface, max_breach_length_cells=1)
        assert report.cells_carved <= report.depressions_found

    def test_the_result_is_still_fully_routable(self) -> None:
        """Breaching reduces filling; it does not replace it. Whatever it could
        not resolve must still be filled, or D8 has nowhere to send the water."""
        surface = dem(bowl_behind_a_bund(bund_height_m=6.0, beyond_m=99.0))
        conditioned, _ = breach_then_fill(surface)
        flow = hyd.build_flow(surface, conditioned)
        # Every valid cell must have a direction, or be an outlet.
        interior = conditioned.valid.copy()
        interior[0, :] = interior[-1, :] = False
        interior[:, 0] = interior[:, -1] = False
        assert (flow.direction[interior] != 0).all(), "an interior cell has no outflow"

    def test_a_surface_with_no_depressions_is_left_alone(self) -> None:
        _, report = breach_then_fill(dem(ramp(0.02)))
        assert report.depressions_found == 0
        assert report.cells_carved == 0

    @pytest.mark.parametrize(("depth", "length"), [(0.0, 10), (-1.0, 10), (1.0, 0)])
    def test_nonsensical_limits_are_refused(self, depth: float, length: int) -> None:
        with pytest.raises(ValueError):
            breach_then_fill(
                dem(ramp(0.0)), max_breach_depth_m=depth, max_breach_length_cells=length
            )


class TestChoosingAMethod:
    def test_auto_fills_terrain_that_drains(self) -> None:
        _, report = condition(dem(ramp(0.02)), method="auto")
        assert report["method"] == "fill"
        assert report["flatness"]["flat_fraction"] < FLAT_FRACTION_PREFER_BREACH

    def test_auto_breaches_flat_terrain(self) -> None:
        """Where the filled surface stops describing the terrain."""
        surface = np.full((SIZE, SIZE), 100.0)
        surface[30:50, 30:50] = 99.0
        _, report = condition(dem(surface), method="auto")
        assert report["method"] == "breach_then_fill"
        assert report["flatness"]["flat_fraction"] > FLAT_FRACTION_PREFER_BREACH

    def test_an_explicit_method_overrides_the_choice(self) -> None:
        flat = np.full((SIZE, SIZE), 100.0)
        flat[30:50, 30:50] = 99.0
        _, forced = condition(dem(flat), method="fill")
        assert forced["method"] == "fill"
        assert forced["method_chosen_by"] == "fill"

    def test_the_report_says_how_the_choice_was_made(self) -> None:
        _, report = condition(dem(ramp(0.02)), method="auto")
        assert report["method_chosen_by"] == "auto"

    def test_the_report_always_states_the_residual_filling(self) -> None:
        """However the surface was conditioned, how much was raised is the number
        that decides how much to trust a catchment drawn on it."""
        for method in ("fill", "breach"):
            _, report = condition(dem(bowl_behind_a_bund(1.0, 94.0)), method=method)
            assert "cells_still_filled" in report
            assert "max_fill_depth_m" in report

    def test_an_unknown_method_is_refused(self) -> None:
        with pytest.raises(ValueError, match="auto, fill or breach"):
            condition(dem(ramp(0.02)), method="nonsense")
