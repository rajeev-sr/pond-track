"""Parser against the supplied sample contour map (MC-15).

Skipped when the file is absent: the sample is context, not a dependency, and the
generalisation guarantees are proved by the synthetic tests in
`unit/test_contour_kml.py`. This test adds *corroboration on real-world data* --
irregular vertex counts, 1355 lines, label Points, a boundary polygon.

Assertions here deliberately check *structure and self-consistency*, not literal
values, so the suite does not become a snapshot of one file.
"""

from __future__ import annotations

import pathlib

import pytest

from app.providers.elevation.contour_kml import parse_contour_file

pytestmark = pytest.mark.integration

CANDIDATES = [
    pathlib.Path(__file__).parents[4] / "contours_1m.kml",
    pathlib.Path(__file__).parents[3] / "contours_1m.kml",
    pathlib.Path("contours_1m.kml"),
]


@pytest.fixture(scope="module")
def sample_bytes() -> bytes:
    for p in CANDIDATES:
        if p.is_file():
            return p.read_bytes()
    pytest.skip("sample contours_1m.kml not present (optional)")


@pytest.fixture(scope="module")
def parsed(sample_bytes: bytes):  # type: ignore[no-untyped-def]
    return parse_contour_file(sample_bytes, "contours_1m.kml")


class TestSampleParse:
    def test_parses_without_error(self, parsed) -> None:  # type: ignore[no-untyped-def]
        assert parsed.lines_parsed > 0

    def test_every_line_resolved(self, parsed) -> None:  # type: ignore[no-untyped-def]
        assert parsed.lines_unresolved == 0

    def test_strategy_is_one_of_the_supported_four(self, parsed) -> None:  # type: ignore[no-untyped-def]
        assert parsed.elevation_strategy in {
            "coordinate_z",
            "extended_data",
            "placemark_name",
            "folder_name",
        }

    def test_has_relief_sufficient_for_d8(self, parsed) -> None:  # type: ignore[no-untyped-def]
        # HLD CH-2: flat terrain breaks D8 flow routing. >= 20 m is the threshold
        # the plan sets for a usable analysis (M0-15 selection criteria).
        assert parsed.relief_m >= 20.0, f"only {parsed.relief_m} m of relief"

    def test_derived_interval_is_positive_and_consistent(self, parsed) -> None:  # type: ignore[no-untyped-def]
        assert parsed.interval_m is not None
        assert parsed.interval_m > 0
        # Levels should be reachable from the minimum in whole interval steps.
        lo = parsed.levels[0]
        off = [abs(((lv - lo) / parsed.interval_m) % 1.0) for lv in parsed.levels]
        assert max(min(o, 1 - o) for o in off) < 1e-6

    def test_working_crs_is_a_projected_utm_zone(self, parsed) -> None:  # type: ignore[no-untyped-def]
        from app.core.crs import CRSGuard

        assert 32601 <= parsed.utm_epsg <= 32760
        CRSGuard.require_projected(parsed.utm_epsg, "catchment area")

    def test_bounds_are_self_consistent(self, parsed) -> None:  # type: ignore[no-untyped-def]
        b = parsed.bounds
        assert b.min_lon < b.max_lon
        assert b.min_lat < b.max_lat
        clon, clat = b.centroid
        assert b.min_lon <= clon <= b.max_lon
        assert b.min_lat <= clat <= b.max_lat

    def test_all_vertices_lie_inside_the_reported_bounds(self, parsed) -> None:  # type: ignore[no-untyped-def]
        b = parsed.bounds
        for ln in parsed.lines:
            for lon, lat in ln.coords:
                assert b.min_lon <= lon <= b.max_lon
                assert b.min_lat <= lat <= b.max_lat

    def test_every_line_has_at_least_two_vertices(self, parsed) -> None:  # type: ignore[no-untyped-def]
        assert all(ln.vertex_count >= 2 for ln in parsed.lines)

    def test_summary_serialises(self, parsed) -> None:  # type: ignore[no-untyped-def]
        import json

        assert json.loads(json.dumps(parsed.summary()))["elevation_source"] == (
            "uploaded_contour_map"
        )


class TestSamplePerformance:
    def test_parses_within_a_few_seconds(self, sample_bytes: bytes) -> None:
        import time

        t0 = time.perf_counter()
        parse_contour_file(sample_bytes)
        elapsed = time.perf_counter() - t0
        assert elapsed < 10.0, f"parse took {elapsed:.1f}s"
