"""Contour -> DEM interpolation (MC-7).

The tests that carry the weight are in `TestAnalyticSurfaces`: they build
contours for a surface whose interpolated values are known in closed form, so
they verify the *mathematics* rather than re-recording whatever the code
currently produces.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from pyproj import Transformer

from app.providers.elevation.contour_kml import ContourParseError, parse_contour_file
from app.services.interpolate import (
    MAX_CELL_M,
    RESOLUTION_LADDER,
    contours_to_dem,
    derive_cell_size_m,
    polyline_length,
    resample_polyline,
)
from app.tests.synthetic_kml import build_kml, concentric_rings, tilted_plane, twin_basins


def _dem(lines, **kw):  # type: ignore[no-untyped-def]
    return contours_to_dem(parse_contour_file(build_kml(lines)), **kw)


class TestResamplePolyline:
    def test_densifies_a_long_segment(self) -> None:
        pts = np.array([[0.0, 0.0], [100.0, 0.0]])
        out = resample_polyline(pts, 10.0)
        assert len(out) == 11
        assert out[0].tolist() == [0.0, 0.0]
        assert out[-1].tolist() == pytest.approx([100.0, 0.0])

    def test_decimates_an_over_dense_line(self) -> None:
        pts = np.column_stack([np.linspace(0, 100, 500), np.zeros(500)])
        out = resample_polyline(pts, 10.0)
        assert len(out) < 20

    def test_endpoints_are_always_preserved(self) -> None:
        pts = np.array([[3.0, 7.0], [50.0, 9.0], [90.0, 1.0]])
        out = resample_polyline(pts, 5.0)
        assert out[0].tolist() == pytest.approx(pts[0].tolist())
        assert out[-1].tolist() == pytest.approx(pts[-1].tolist())

    def test_spacing_is_respected(self) -> None:
        pts = np.array([[0.0, 0.0], [100.0, 0.0]])
        out = resample_polyline(pts, 7.0)
        gaps = np.hypot(*np.diff(out, axis=0).T)
        assert gaps.max() <= 7.0 + 1e-6

    def test_length_is_preserved_for_a_straight_line(self) -> None:
        pts = np.array([[0.0, 0.0], [60.0, 80.0]])
        assert polyline_length(resample_polyline(pts, 5.0)) == pytest.approx(100.0)

    def test_short_line_collapses_to_endpoints(self) -> None:
        assert len(resample_polyline(np.array([[0.0, 0.0], [1.0, 0.0]]), 10.0)) == 2

    def test_degenerate_input_is_returned_unchanged(self) -> None:
        one = np.array([[1.0, 2.0]])
        assert resample_polyline(one, 5.0).tolist() == one.tolist()


class TestDeriveCellSize:
    def test_uses_the_area_over_length_identity(self) -> None:
        # Parallel contours 20 m apart across a 1000x1000 m area: 50 lines of
        # 1000 m each => L = 50_000 m, and A/L = 20 m recovers the spacing.
        cell, spacing = derive_cell_size_m(1_000_000.0, 50_000.0)
        assert spacing == pytest.approx(20.0)
        assert cell == pytest.approx(10.0)  # spacing / 2, already on the ladder

    def test_snaps_to_a_legible_value(self) -> None:
        cell, _ = derive_cell_size_m(1_000_000.0, 66_000.0)  # spacing 15.15 -> 7.6
        assert cell in RESOLUTION_LADDER

    def test_clamped_at_the_coarse_end(self) -> None:
        cell, _ = derive_cell_size_m(1_000_000.0, 100.0)  # spacing 10 km
        assert cell == MAX_CELL_M

    def test_clamped_at_the_fine_end(self) -> None:
        cell, _ = derive_cell_size_m(1_000.0, 1_000_000.0)  # spacing 1 mm
        assert cell >= 1.0

    def test_zero_length_is_rejected(self) -> None:
        with pytest.raises(ContourParseError, match="zero total length"):
            derive_cell_size_m(1000.0, 0.0)

    def test_finer_contours_give_a_finer_grid(self) -> None:
        coarse, _ = derive_cell_size_m(1_000_000.0, 20_000.0)
        fine, _ = derive_cell_size_m(1_000_000.0, 200_000.0)
        assert fine < coarse


class TestAnalyticSurfaces:
    """★ Surfaces whose interpolated values are known in closed form."""

    def test_tilted_plane_interpolates_linearly_between_contours(self) -> None:
        # Contours at 10/20/30/40 m on parallel lines of constant latitude.
        # Halfway between two of them the surface must read the mean of the two.
        lines = tilted_plane(levels=(10.0, 20.0, 30.0, 40.0), step_deg=0.002, span_deg=0.02)
        parsed = parse_contour_file(build_kml(lines))
        dem, _ = contours_to_dem(parsed, cell_size_m=5.0, smooth_sigma_cells=0.0)

        tf = Transformer.from_crs(4326, parsed.utm_epsg, always_xy=True)
        x_mid, _ = tf.transform(77.0 + 0.01, 21.0)
        _, y_lo = tf.transform(77.0 + 0.01, 21.0)  # the 10 m contour
        _, y_hi = tf.transform(77.0 + 0.01, 21.0 + 0.002)  # the 20 m contour

        assert dem.sample(x_mid, (y_lo + y_hi) / 2.0) == pytest.approx(15.0, abs=0.3)
        assert dem.sample(x_mid, y_lo + 0.25 * (y_hi - y_lo)) == pytest.approx(12.5, abs=0.4)
        assert dem.sample(x_mid, y_lo + 0.75 * (y_hi - y_lo)) == pytest.approx(17.5, abs=0.4)

    def test_tilted_plane_gradient_is_constant(self) -> None:
        lines = tilted_plane(levels=(10.0, 20.0, 30.0, 40.0), step_deg=0.002, span_deg=0.02)
        dem, _ = _dem(lines, cell_size_m=5.0, smooth_sigma_cells=0.0)
        col = dem.shape[1] // 2
        prof = dem.elevation[:, col]
        prof = prof[np.isfinite(prof)]
        d = np.diff(prof)[1:-1]
        # A uniform slope: every step down the column changes elevation equally.
        # The first and last steps are excluded: those cells sit on the convex
        # hull edge and are only partially covered, so their step is a fraction
        # of a full one. That is an edge effect of the clip, not a gradient
        # error -- the interior is constant to ~1e-5 m.
        assert np.std(d) < 0.01 * abs(np.mean(d)), f"gradient not uniform: {np.unique(d)}"

    def test_inverted_cone_has_its_minimum_at_the_centre(self) -> None:
        # concentric_rings raises elevation with radius -> a bowl.
        centre = (77.0, 21.0)
        parsed = parse_contour_file(build_kml(concentric_rings(center=centre)))
        dem, _ = contours_to_dem(parsed, cell_size_m=5.0)
        tf = Transformer.from_crs(4326, parsed.utm_epsg, always_xy=True)
        cx, cy = tf.transform(*centre)

        row, col = dem.rowcol(cx, cy)
        finite = dem.elevation[np.isfinite(dem.elevation)]
        # The centre must sit within a whisker of the global minimum.
        assert dem.elevation[row, col] == pytest.approx(float(finite.min()), abs=0.6)

    def test_inverted_cone_elevation_rises_with_radius(self) -> None:
        centre = (77.0, 21.0)
        parsed = parse_contour_file(build_kml(concentric_rings(center=centre)))
        dem, _ = contours_to_dem(parsed, cell_size_m=5.0)
        tf = Transformer.from_crs(4326, parsed.utm_epsg, always_xy=True)
        cx, cy = tf.transform(*centre)

        samples = []
        for r in (20.0, 60.0, 100.0, 140.0):
            vals = [
                dem.sample(cx + r * math.cos(t), cy + r * math.sin(t))
                for t in np.linspace(0, 2 * math.pi, 12, endpoint=False)
            ]
            samples.append(float(np.nanmean(vals)))
        assert samples == sorted(samples), f"not monotonic in radius: {samples}"

    def test_twin_basins_keep_two_distinct_minima(self) -> None:
        parsed = parse_contour_file(build_kml(twin_basins()))
        dem, _ = contours_to_dem(parsed, cell_size_m=5.0)
        tf = Transformer.from_crs(4326, parsed.utm_epsg, always_xy=True)
        for lon in (77.00, 77.02):
            x, y = tf.transform(lon, 21.0)
            row, col = dem.rowcol(x, y)
            local = dem.elevation[max(0, row - 2) : row + 3, max(0, col - 2) : col + 3]
            assert np.nanmin(local) == pytest.approx(50.0, abs=1.0)
        # The ridge between them must be higher than either basin floor.
        xm, ym = tf.transform(77.01, 21.0)
        assert dem.sample(xm, ym) > 50.5


class TestGridProperties:
    def test_relief_survives_smoothing(self) -> None:
        parsed = parse_contour_file(build_kml(concentric_rings(levels=(10.0, 20.0, 30.0))))
        dem, rep = contours_to_dem(parsed)
        # De-terracing must not flatten the terrain it is cleaning up.
        assert rep.relief_m == pytest.approx(parsed.relief_m, rel=0.05)

    def test_interpolated_values_stay_within_the_contour_range(self) -> None:
        parsed = parse_contour_file(build_kml(concentric_rings(levels=(100.0, 110.0, 120.0))))
        dem, _ = contours_to_dem(parsed)
        finite = dem.elevation[np.isfinite(dem.elevation)]
        # A TIN interpolates, never extrapolates: no over/undershoot.
        assert finite.min() >= 100.0 - 0.01
        assert finite.max() <= 120.0 + 0.01

    def test_outside_the_hull_is_nodata_not_extrapolated(self) -> None:
        dem, rep = _dem(concentric_rings())
        assert rep.hull_coverage_pct < 100.0
        assert np.isnan(dem.elevation).any()

    def test_crs_is_projected_and_metric(self) -> None:
        from app.core.crs import CRSGuard

        dem, _ = _dem(concentric_rings())
        CRSGuard.require_projected(dem.epsg, "area calculation")

    def test_georeferencing_round_trips(self) -> None:
        dem, _ = _dem(concentric_rings())
        for row, col in ((0, 0), (5, 7), (dem.shape[0] - 1, dem.shape[1] - 1)):
            x, y = dem.xy(row, col)
            assert dem.rowcol(x, y) == (row, col)

    def test_transform_is_north_up(self) -> None:
        dem, _ = _dem(concentric_rings())
        a, b, _c, d, e, _f = dem.transform
        assert a > 0 and e < 0 and b == 0 and d == 0

    def test_bounds_enclose_every_cell_centre(self) -> None:
        dem, _ = _dem(concentric_rings())
        min_x, min_y, max_x, max_y = dem.bounds_m
        for row, col in ((0, 0), (dem.shape[0] - 1, dem.shape[1] - 1)):
            x, y = dem.xy(row, col)
            assert min_x <= x <= max_x
            assert min_y <= y <= max_y

    def test_sampling_outside_the_grid_raises(self) -> None:
        dem, _ = _dem(concentric_rings())
        with pytest.raises(IndexError):
            dem.sample(1e9, 1e9)

    def test_deterministic(self) -> None:
        a, _ = _dem(concentric_rings())
        b, _ = _dem(concentric_rings())
        assert np.array_equal(
            np.nan_to_num(a.elevation, nan=-9999), np.nan_to_num(b.elevation, nan=-9999)
        )


class TestResolutionControl:
    def test_derived_by_default_and_flagged(self) -> None:
        _, rep = _dem(concentric_rings())
        assert rep.cell_size_derived is True
        assert rep.cell_size_m in RESOLUTION_LADDER

    def test_explicit_override_is_honoured_and_flagged(self) -> None:
        # step_deg keeps the synthetic extent small: these tests exercise the
        # resolution knob, and a fine cell over a 2 km extent would build a
        # million-cell grid for no extra assurance.
        _, rep = _dem(concentric_rings(step_deg=0.0003), cell_size_m=3.0)
        assert rep.cell_size_derived is False
        assert rep.cell_size_m == 3.0

    def test_finer_cells_give_a_bigger_grid(self) -> None:
        small = concentric_rings(step_deg=0.0003)
        _, coarse = _dem(small, cell_size_m=10.0)
        _, fine = _dem(small, cell_size_m=2.0)
        assert fine.grid_width > coarse.grid_width

    @pytest.mark.parametrize("bad", [0.0, 0.5, 100.0, -5.0])
    def test_out_of_range_cell_size_is_rejected(self, bad: float) -> None:
        with pytest.raises(ContourParseError, match="cell_size_m must be between"):
            _dem(concentric_rings(), cell_size_m=bad)

    def test_report_is_json_serialisable(self) -> None:
        import json

        _, rep = _dem(concentric_rings())
        d = json.loads(json.dumps(rep.as_dict()))
        for k in (
            "grid_resolution_m",
            "interpolation_method",
            "hull_coverage_pct",
            "mean_contour_spacing_m",
            "vertices_after_resample",
        ):
            assert k in d

    def test_provenance_carries_both_parse_and_interpolation_facts(self) -> None:
        dem, _ = _dem(concentric_rings())
        assert dem.provenance["elevation_source"] == "uploaded_contour_map"
        assert dem.provenance["interpolation_method"] == "linear_tin_delaunay"
        assert "elevation_strategy" in dem.provenance
