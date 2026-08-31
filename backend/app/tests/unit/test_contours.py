"""Contour generation from a DEM (M2-5, M2-6).

The surfaces here have contours that can be worked out on paper -- a cone's are
concentric circles of known radius, a plane's are straight parallel lines of
known spacing -- so these check the geometry rather than freezing whatever the
implementation currently emits.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from app.services.contours import (
    DEFAULT_INDEX_EVERY,
    MAX_LEVELS,
    ContourGenerationError,
    generate,
    levels_for,
)

CELL = 5.0
SIZE = 201
#: North-up, 5 m cells, origin somewhere in UTM 44N -- the working CRS the
#: pipeline actually derives for central India.
TRANSFORM = (CELL, 0.0, 530000.0, 0.0, -CELL, 2352000.0)
EPSG = 32644


def cone(peak: float = 300.0, drop_per_m: float = 0.1) -> np.ndarray:
    """A cone. Its contours are concentric circles, radius = (peak - z)/drop."""
    rows, cols = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    centre = (SIZE - 1) / 2
    radius_m = np.hypot(rows - centre, cols - centre) * CELL
    return peak - drop_per_m * radius_m


def ramp(low: float = 100.0, high: float = 140.0) -> np.ndarray:
    """A plane rising west to east. Contours are straight north-south lines."""
    _, cols = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    return low + (high - low) * cols / (SIZE - 1)


def run(surface: np.ndarray, **kwargs: object):
    return generate(
        surface, transform=TRANSFORM, epsg=EPSG, cell_size_m=CELL, **kwargs  # type: ignore[arg-type]
    )


class TestChoosingLevels:
    def test_levels_land_on_multiples_of_the_interval(self) -> None:
        """A contour map's value is that its lines are round numbers.

        Starting at the minimum would give 267.31, 268.31 -- correct, and useless
        to read off.
        """
        assert levels_for(267.31, 298.02, 1.0)[:3] == [268.0, 269.0, 270.0]
        assert levels_for(267.31, 298.02, 5.0)[:3] == [270.0, 275.0, 280.0]

    def test_a_level_exactly_on_the_minimum_is_kept(self) -> None:
        assert levels_for(267.0, 270.0, 1.0) == [267.0, 268.0, 269.0, 270.0]

    def test_repeated_addition_does_not_drift(self) -> None:
        """0.1 + 0.1 ... reaches 3.0000000000000004, which renders in a label."""
        levels = levels_for(0.0, 3.0, 0.1)
        assert levels[-1] == 3.0
        assert all(level == round(level, 6) for level in levels)

    @pytest.mark.parametrize("interval", [0.0, -1.0])
    def test_a_non_positive_interval_is_refused(self, interval: float) -> None:
        with pytest.raises(ContourGenerationError, match="interval must be positive"):
            levels_for(100.0, 200.0, interval)

    def test_a_flat_surface_has_nothing_to_contour(self) -> None:
        with pytest.raises(ContourGenerationError, match="no relief"):
            levels_for(280.0, 280.0, 1.0)

    def test_an_interval_coarser_than_the_relief_is_refused(self) -> None:
        with pytest.raises(ContourGenerationError, match="no levels"):
            levels_for(280.1, 280.9, 5.0)

    def test_an_absurdly_fine_interval_is_refused_with_the_numbers(self) -> None:
        """3,099 levels over 31 m is a mistake, not a request."""
        with pytest.raises(ContourGenerationError, match=f"the limit is {MAX_LEVELS}"):
            levels_for(267.0, 298.0, 0.01)


class TestGeometryOfKnownSurfaces:
    def test_a_cone_produces_closed_rings_of_the_right_radius(self) -> None:
        """At 290 m on a cone peaking at 300 with a 0.1 m/m fall, the contour is
        a circle of radius (300-290)/0.1 = 100 m, circumference 628 m."""
        result = run(cone(), interval_m=10.0, simplify=False)
        at_290 = [line for line in result.lines if line.elevation_m == 290.0]
        assert len(at_290) == 1, "a cone has exactly one contour per level"
        assert at_290[0].length_m == pytest.approx(2 * math.pi * 100.0, rel=0.02)

    def test_a_cone_s_contours_are_centred_on_its_peak(self) -> None:
        result = run(cone(), interval_m=10.0, simplify=False)
        (line,) = [ln for ln in result.lines if ln.elevation_m == 290.0]
        xs = [x for x, _ in line.coordinates]
        ys = [y for _, y in line.coordinates]
        centre = (SIZE - 1) / 2
        expected_x = TRANSFORM[2] + CELL * (centre + 0.5)
        expected_y = TRANSFORM[5] - CELL * (centre + 0.5)
        assert sum(xs) / len(xs) == pytest.approx(expected_x, abs=CELL)
        assert sum(ys) / len(ys) == pytest.approx(expected_y, abs=CELL)

    def test_a_ramp_produces_straight_lines_the_height_of_the_grid(self) -> None:
        result = run(ramp(), interval_m=10.0, simplify=False)
        span_m = (SIZE - 1) * CELL
        for line in result.lines:
            assert line.length_m == pytest.approx(span_m, rel=0.02), line.elevation_m

    def test_a_ramp_s_contours_are_evenly_spaced(self) -> None:
        """40 m over 1000 m, contoured every 10 m, puts lines 250 m apart."""
        result = run(ramp(), interval_m=10.0, simplify=False)
        by_level = sorted(
            (line.elevation_m, sum(x for x, _ in line.coordinates) / len(line.coordinates))
            for line in result.lines
        )
        gaps = [b[1] - a[1] for a, b in itertools.pairwise(by_level)]
        assert all(gap == pytest.approx(250.0, rel=0.05) for gap in gaps), gaps

    def test_the_projected_coordinates_are_in_the_right_place(self) -> None:
        """Not lon/lat, and not off by a grid: still inside the raster's extent."""
        result = run(cone(), interval_m=10.0)
        xs = [x for line in result.lines for x, _ in line.coordinates]
        ys = [y for line in result.lines for _, y in line.coordinates]
        assert TRANSFORM[2] <= min(xs) and max(xs) <= TRANSFORM[2] + SIZE * CELL
        assert TRANSFORM[5] - SIZE * CELL <= min(ys) and max(ys) <= TRANSFORM[5]


class TestSimplification:
    def test_it_removes_most_vertices(self) -> None:
        """Marching squares emits one per cell crossing; a browser will not draw
        tens of thousands per level."""
        result = run(cone(), interval_m=5.0, simplify=True)
        assert result.vertices_after_simplify < result.vertices_before_simplify * 0.5

    def test_it_does_not_move_the_line_measurably(self) -> None:
        """A cone's circumference is known, so this is checkable rather than
        just "smaller"."""
        plain = run(cone(), interval_m=10.0, simplify=False)
        simple = run(cone(), interval_m=10.0, simplify=True)
        for a, b in zip(
            sorted(plain.lines, key=lambda ln: ln.elevation_m),
            sorted(simple.lines, key=lambda ln: ln.elevation_m),
            strict=True,
        ):
            assert b.length_m == pytest.approx(a.length_m, rel=0.03), a.elevation_m

    def test_the_tolerance_scales_with_the_cell(self) -> None:
        result = run(cone(), interval_m=10.0, simplify=True)
        assert 0 < result.simplify_tolerance_m < CELL

    def test_disabling_it_reports_a_zero_tolerance(self) -> None:
        assert run(cone(), interval_m=10.0, simplify=False).simplify_tolerance_m == 0.0


class TestIndexContours:
    def test_every_fifth_level_is_an_index_contour(self) -> None:
        """Compared against the levels that produced lines, not all of them.

        A level sitting exactly on the surface's minimum or maximum can trace
        nothing at all, so the set of drawn levels is a subset of the requested
        ones -- comparing against the full list fails for a reason that has
        nothing to do with index spacing.
        """
        result = run(ramp(low=100.0, high=200.0), interval_m=2.0)
        drawn = {ln.elevation_m for ln in result.lines}
        expected = {lvl for lvl in result.levels[::DEFAULT_INDEX_EVERY] if lvl in drawn}
        assert {ln.elevation_m for ln in result.lines if ln.is_index} == expected
        assert expected, "no index contours were drawn at all"

    def test_the_interval_can_be_changed(self) -> None:
        result = run(ramp(low=100.0, high=200.0), interval_m=2.0, index_every=10)
        drawn = {ln.elevation_m for ln in result.lines}
        expected = {lvl for lvl in result.levels[::10] if lvl in drawn}
        assert {ln.elevation_m for ln in result.lines if ln.is_index} == expected
        assert expected

    def test_one_means_every_line(self) -> None:
        result = run(ramp(), interval_m=10.0, index_every=1)
        assert all(line.is_index for line in result.lines)

    def test_zero_is_refused(self) -> None:
        with pytest.raises(ContourGenerationError, match="at least 1"):
            run(cone(), interval_m=10.0, index_every=0)


class TestNodata:
    def test_no_contour_rings_a_hole_in_the_data(self) -> None:
        """`find_contours` has no nodata concept, and both obvious fixes fail.

        Filling holes with a value below every level does *not* keep the tracing
        inside the data: the step from fill to real ground crosses every level on
        the way up, so a contour is drawn around the hole for each one -- a
        smooth closed line exactly following the survey edge, which reads as
        terrain rather than as an artefact.

        Tested on an interior hole rather than a clipped edge, because a clipped
        edge is ambiguous: cut a west-to-east ramp down the middle and the lowest
        surviving level genuinely does coincide with the cut. A rectangular hole
        punched into a smooth ramp has no such excuse -- nothing about the
        terrain puts a contour around it.
        """
        surface = ramp(low=100.0, high=200.0)
        hole = (slice(60, 140), slice(60, 140))
        surface[hole] = np.nan
        result = run(surface, interval_m=10.0, simplify=False)

        west = TRANSFORM[2] + CELL * 60
        east = TRANSFORM[2] + CELL * 140
        north = TRANSFORM[5] - CELL * 60
        south = TRANSFORM[5] - CELL * 140

        for line in result.lines:
            inside = [(x, y) for x, y in line.coordinates if west < x < east and south < y < north]
            assert not inside, (
                f"the {line.elevation_m} m contour has {len(inside)} vertices "
                "inside a hole in the survey"
            )

    def test_a_contour_is_still_traced_on_both_sides_of_a_hole(self) -> None:
        """Splitting on validity must not silently drop the whole line.

        The 150 m contour crosses the hole, so it should survive as two runs --
        one to the north of it and one to the south -- rather than vanishing.
        """
        surface = ramp(low=100.0, high=200.0)
        surface[60:140, 60:140] = np.nan
        result = run(surface, interval_m=10.0, simplify=False)
        crossing = [ln for ln in result.lines if ln.elevation_m == 150.0]
        assert (
            len(crossing) >= 2
        ), f"expected the contour through the hole to split, got {len(crossing)}"

    def test_levels_come_from_the_valid_cells_only(self) -> None:
        surface = cone()
        surface[:20, :20] = np.nan
        result = run(surface, interval_m=10.0)
        assert math.isfinite(result.elevation_min_m)
        assert result.elevation_max_m == pytest.approx(300.0, abs=1.0)

    def test_an_entirely_empty_surface_is_refused(self) -> None:
        with pytest.raises(ContourGenerationError, match="no valid elevations"):
            run(np.full((50, 50), np.nan), interval_m=1.0)


class TestTheReport:
    def test_it_states_what_was_generated(self) -> None:
        report = run(cone(), interval_m=10.0).report()
        for key in (
            "interval_m",
            "index_every",
            "level_count",
            "line_count",
            "index_line_count",
            "total_length_m",
            "vertex_reduction_pct",
            "simplify_tolerance_m",
            "working_crs_epsg",
        ):
            assert key in report, key

    def test_the_reduction_percentage_is_consistent(self) -> None:
        result = run(cone(), interval_m=5.0)
        report = result.report()
        expected = 100.0 * (1.0 - result.vertices_after_simplify / result.vertices_before_simplify)
        assert report["vertex_reduction_pct"] == pytest.approx(expected, abs=0.1)

    def test_the_crs_is_carried_through(self) -> None:
        assert run(cone(), interval_m=10.0).report()["working_crs_epsg"] == EPSG
