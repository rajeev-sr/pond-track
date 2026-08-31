"""Contours to DEM and back again.

`services.interpolate` turns contour lines into a grid; `services.contours`
turns a grid back into contour lines. Running the real 1 m survey through both
and comparing the result to the input tests the interpolation in a way nothing
else here does -- a systematic error in the TIN, the CRS, the affine transform or
the row/column ordering shows up as regenerated contours that do not sit on the
originals, and almost nowhere else.

Marked `slow` rather than `golden`-fixture-based: the assertions are on
*agreement between two independent code paths*, not on a stored snapshot, so
there is nothing to go stale.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from app.providers.elevation.contour_kml import parse_contour_file
from app.services import contours as gen
from app.services.interpolate import contours_to_dem

pytestmark = pytest.mark.slow

SAMPLE = Path(__file__).resolve().parents[4] / "contours_1m.kml"


@pytest.fixture(scope="module")
def round_trip():
    """Parse the sample survey, interpolate a DEM, regenerate 1 m contours."""
    if not SAMPLE.exists():
        pytest.skip(f"no sample contour map at {SAMPLE}")
    parsed = parse_contour_file(SAMPLE.read_bytes(), SAMPLE.name)
    dem, report = contours_to_dem(parsed)
    regenerated = gen.generate(
        dem.elevation,
        transform=dem.transform,
        epsg=dem.epsg,
        cell_size_m=dem.cell_size_m,
        interval_m=1.0,
    )
    return parsed, dem, report, regenerated


class TestTheSurfaceSurvivesTheRoundTrip:
    def test_the_elevation_range_is_preserved(self, round_trip) -> None:
        """Within one contour interval. The interpolated peak sits slightly below
        the true one because a TIN cannot rise above its highest input vertex --
        which is why the tolerance is an interval and not zero."""
        parsed, _, _, regenerated = round_trip
        summary = parsed.summary()
        assert regenerated.elevation_min_m == pytest.approx(summary["elevation_min_m"], abs=1.0)
        assert regenerated.elevation_max_m == pytest.approx(summary["elevation_max_m"], abs=1.0)

    def test_the_levels_land_on_the_same_metres(self, round_trip) -> None:
        parsed, _, _, regenerated = round_trip
        summary = parsed.summary()
        assert all(level == round(level) for level in regenerated.levels)
        assert min(regenerated.levels) >= math.floor(summary["elevation_min_m"])
        assert max(regenerated.levels) <= math.ceil(summary["elevation_max_m"])

    def test_almost_every_input_level_comes_back(self, round_trip) -> None:
        """One may be lost at each end where the TIN cannot quite reach."""
        parsed, _, _, regenerated = round_trip
        expected = parsed.summary()["levels"]
        assert (
            len(regenerated.levels) >= expected - 2
        ), f"{len(regenerated.levels)} levels regenerated from {expected} surveyed"

    def test_the_total_length_is_the_same_order(self, round_trip) -> None:
        """Not equal, and it should not be: marching squares closes a ring around
        every local feature, while a surveyed line stops at the sheet edge. Within
        half again is agreement; a factor of two would mean something is wrong."""
        _, _, report, regenerated = round_trip
        surveyed = report.as_dict()["total_contour_length_m"]
        regenerated_length = sum(line.length_m for line in regenerated.lines)
        ratio = regenerated_length / surveyed
        assert 0.7 < ratio < 1.5, f"length ratio {ratio:.3f}"


class TestTheInterpolationReproducesTheSurvey:
    """Tested on elevations, not on geometry, and the reason matters.

    A contour's horizontal position is its elevation divided by the local slope,
    so on nearly-flat ground a vertical error of 0.1 m moves the line tens of
    metres sideways. Comparing regenerated contours to surveyed ones by distance
    therefore fails on gentle terrain for reasons that are geometry rather than
    error -- 8 % of vertices sat more than three cells away, almost all of them
    on the flats. Sampling the DEM at the surveyed vertices removes the slope
    amplification entirely and tests the interpolation directly.
    """

    def test_the_dem_holds_the_surveyed_elevation_at_the_surveyed_points(self, round_trip) -> None:
        parsed, dem, _, _ = round_trip
        from pyproj import Transformer

        to_utm = Transformer.from_crs(4326, dem.epsg, always_xy=True)

        errors: list[float] = []
        outside = 0
        for line in parsed.lines[::7]:
            lons = [lon for lon, _ in line.coords[::20]]
            lats = [lat for _, lat in line.coords[::20]]
            if not lons:
                continue
            xs, ys = to_utm.transform(lons, lats)
            for x, y in zip(xs, ys, strict=True):
                try:
                    value = dem.sample(float(x), float(y))
                except IndexError:
                    outside += 1
                    continue
                if math.isfinite(value):
                    errors.append(abs(value - line.elevation_m))

        assert len(errors) > 500, f"only {len(errors)} points sampled"
        residuals = np.asarray(errors)
        median = float(np.median(residuals))
        p95 = float(np.percentile(residuals, 95))
        # A TIN interpolated *from* these lines must pass through them. The
        # residual is not zero because the grid samples at cell centres rather
        # than on the line, so half a cell of horizontal offset on a 1-in-15
        # slope is a few centimetres of vertical difference.
        assert median < 0.15, f"median residual {median:.3f} m"
        assert p95 < 0.60, f"95th percentile residual {p95:.3f} m"
        assert outside < len(errors), "most surveyed points fell outside the grid"

    def test_nothing_is_displaced_wholesale(self, round_trip) -> None:
        """A coarse geometric check, which is what geometry can actually catch.

        A CRS mix-up, a swapped affine, or a row/column transposition displaces
        everything by hundreds of metres to kilometres. The tolerance is three
        times the mean contour spacing so the flat-ground amplification above
        cannot trip it, while an error of that class still cannot hide.
        """
        parsed, dem, report, regenerated = round_trip
        spacing = report.as_dict()["mean_contour_spacing_m"]
        tolerance = 3.0 * spacing

        from pyproj import Transformer

        to_utm = Transformer.from_crs(4326, dem.epsg, always_xy=True)
        collected: dict[int, list[tuple[float, float]]] = {}
        for line in parsed.lines:
            lons = [lon for lon, _ in line.coords]
            lats = [lat for _, lat in line.coords]
            xs, ys = to_utm.transform(lons, lats)
            collected.setdefault(round(line.elevation_m), []).extend(zip(xs, ys, strict=True))
        surveyed = {level: np.asarray(points) for level, points in collected.items() if points}

        checked = far = 0
        for line in regenerated.lines:
            reference = surveyed.get(round(line.elevation_m))
            if reference is None or not len(reference):
                continue
            for x, y in line.coordinates[::40]:
                checked += 1
                distances = np.hypot(reference[:, 0] - x, reference[:, 1] - y)
                if float(distances.min()) > tolerance:
                    far += 1

        assert checked > 200, f"only {checked} vertices sampled; the join failed"
        stray = far / checked
        assert stray < 0.02, (
            f"{stray:.1%} of regenerated vertices are more than {tolerance:.0f} m "
            f"({far} of {checked}) from any surveyed line at the same elevation"
        )


class TestSimplificationIsWorthDoing:
    def test_it_removes_most_of_the_vertices(self, round_trip) -> None:
        _, _, _, regenerated = round_trip
        assert regenerated.vertices_after_simplify < regenerated.vertices_before_simplify / 3

    def test_and_the_result_is_still_a_map(self, round_trip) -> None:
        """Reduction is worthless if it leaves too few lines to read."""
        _, _, _, regenerated = round_trip
        assert len(regenerated.lines) > 100
        assert any(line.is_index for line in regenerated.lines)


class TestTheDrainageNetworkOfRealTerrain:
    """Horton's laws, which a toy network cannot exhibit.

    A hand-built test grid has channels one or two cells long, so mean length by
    order says nothing. On the real survey it does: the laws of stream numbers and
    stream lengths are empirical regularities of natural drainage, and a network
    that violates them has almost certainly been extracted wrongly.
    """

    @pytest.fixture(scope="class")
    def network(self, round_trip):
        from app.services import hydrology as hyd
        from app.services import streams

        _, dem, _, _ = round_trip
        flow = hyd.build_flow(dem, hyd.fill_depressions(dem))
        catchment = hyd.delineate_catchment(dem, flow, 359, 178, snap_radius_cells=30)
        return (
            streams.extract(
                flow,
                transform=dem.transform,
                cell_size_m=dem.cell_size_m,
                threshold_ha=1.0,
                within=catchment,
            ),
            catchment,
        )

    def test_the_network_is_dendritic(self, network) -> None:
        result, _ = network
        assert result.max_order >= 3, f"only {result.max_order} orders found"

    def test_stream_numbers_fall_with_order(self, network) -> None:
        """Horton's law of stream numbers."""
        result, _ = network
        counts = [
            sum(1 for r in result.reaches if r.order == order)
            for order in range(1, result.max_order + 1)
        ]
        assert counts == sorted(counts, reverse=True), counts

    def test_the_bifurcation_ratio_is_physically_plausible(self, network) -> None:
        """Natural networks sit around 3-5. A ratio below 1 is impossible and was
        the symptom of cutting reaches at every junction rather than at every
        change of order."""
        result, _ = network
        counts = {
            order: sum(1 for r in result.reaches if r.order == order)
            for order in range(1, result.max_order + 1)
        }
        ratios = [counts[o] / counts[o + 1] for o in sorted(counts) if counts.get(o + 1)]
        assert ratios, "no ratios to check"
        assert all(ratio > 1.0 for ratio in ratios), ratios
        # The lowest orders have the largest samples, so check the first ratio
        # against the textbook range rather than the noisy tail.
        assert 2.5 < ratios[0] < 6.0, ratios

    def test_mean_stream_length_grows_with_order(self, network) -> None:
        """Horton's law of stream lengths."""
        result, _ = network
        means = []
        for order in range(1, result.max_order + 1):
            lengths = [r.length_m for r in result.reaches if r.order == order]
            means.append(sum(lengths) / len(lengths))
        assert means == sorted(means), [round(m) for m in means]

    def test_the_drainage_density_is_reported_against_the_catchment(self, network) -> None:
        result, catchment = network
        assert result.drainage_density_km_per_km2 is not None
        expected = (result.total_length_m / 1000.0) / (catchment.area_m2 / 1e6)
        assert result.drainage_density_km_per_km2 == pytest.approx(expected, rel=1e-6)

    def test_every_reach_lies_inside_the_catchment(self, network) -> None:
        """Restricting to a catchment must actually restrict."""
        result, catchment = network
        for reach in result.reaches:
            for cell in reach.cells:
                assert catchment.mask[cell], f"reach cell {cell} is outside the catchment"
