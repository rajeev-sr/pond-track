"""The drawn rectangle: validation, measurement, and the mask that holds siting.

Every rejection has to say what is wrong in words a person drawing on a map can
act on -- "inverted", "below the minimum", "over the limit" -- because the UI
shows the message as it comes back.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.providers.elevation.base import Bounds
from app.services import area
from app.tests.area_fakes import DURG_BBOX, fake_dem

CAP = 100.0


class TestAValidRectangle:
    def test_the_sample_sheets_extent_is_accepted_and_measured(self) -> None:
        box = area.validate_bbox(DURG_BBOX, max_km2=CAP)
        assert box.as_list() == DURG_BBOX
        # 3.24 km x 2.63 km at 21°N, worked by hand rather than taken from pyproj.
        assert box.area_km2 == pytest.approx(8.53, rel=0.01)

    def test_integers_and_numeric_strings_are_numbers_too(self) -> None:
        """JSON from a hand-written Postman body is not always floats."""
        box = area.validate_bbox([81, 21, "81.05", 21.05], max_km2=CAP)
        assert box.min_lon == 81.0 and box.max_lon == 81.05

    def test_it_exposes_bounds_and_geojson_for_the_response(self) -> None:
        box = area.validate_bbox(DURG_BBOX, max_km2=CAP)
        assert box.bounds == Bounds(*DURG_BBOX)
        ring = box.as_geojson()["coordinates"][0]
        assert ring[0] == ring[-1]
        assert {round(p[0], 4) for p in ring} == {DURG_BBOX[0], DURG_BBOX[2]}


class TestRejections:
    @pytest.mark.parametrize(
        "bbox",
        [
            pytest.param([81.3, 21.24, 81.28, 21.26], id="longitudes_swapped"),
            pytest.param([81.28, 21.26, 81.31, 21.24], id="latitudes_swapped"),
            pytest.param([81.28, 21.24, 81.28, 21.26], id="zero_width"),
        ],
    )
    def test_an_inverted_or_empty_box_says_which_order_is_expected(self, bbox) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(area.AreaError) as caught:
            area.validate_bbox(bbox, max_km2=CAP)
        message = str(caught.value)
        assert "min_lon" in message or "latitudes" in message

    @pytest.mark.parametrize(
        "bbox",
        [
            pytest.param([81.28, 21.24, 81.31], id="three_values"),
            pytest.param([81.28, 21.24, 81.31, 21.26, 0.0], id="five_values"),
            pytest.param("81.28,21.24,81.31,21.26", id="a_string"),
        ],
    )
    def test_it_needs_exactly_four_values(self, bbox) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(area.AreaError, match="four numbers"):
            area.validate_bbox(bbox, max_km2=CAP)

    @pytest.mark.parametrize(
        "bbox",
        [
            pytest.param([81.28, "north", 81.31, 21.26], id="text"),
            pytest.param([81.28, None, 81.31, 21.26], id="null"),
        ],
    )
    def test_values_must_be_numbers(self, bbox) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(area.AreaError, match="numbers"):
            area.validate_bbox(bbox, max_km2=CAP)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_values_must_be_finite(self, bad: float) -> None:
        with pytest.raises(area.AreaError, match="finite"):
            area.validate_bbox([81.28, 21.24, bad, 21.26], max_km2=CAP)

    def test_longitude_out_of_range(self) -> None:
        with pytest.raises(area.AreaError, match="longitudes"):
            area.validate_bbox([179.99, 21.24, 180.5, 21.26], max_km2=CAP)

    def test_latitude_out_of_range(self) -> None:
        with pytest.raises(area.AreaError, match="latitudes"):
            area.validate_bbox([81.28, 89.99, 81.31, 90.5], max_km2=CAP)

    def test_a_box_too_small_for_a_catchment_is_a_400_not_a_413(self) -> None:
        # ~110 m x 110 m: 0.012 km².
        with pytest.raises(area.AreaError) as caught:
            area.validate_bbox([81.29, 21.25, 81.291, 21.251], max_km2=CAP)
        assert not isinstance(caught.value, area.AreaTooLargeError)
        assert "minimum" in str(caught.value)

    def test_a_box_over_the_cap_is_too_large_and_carries_both_figures(self) -> None:
        # One degree square at 21°N: about 11,500 km².
        with pytest.raises(area.AreaTooLargeError) as caught:
            area.validate_bbox([81.0, 21.0, 82.0, 22.0], max_km2=CAP)
        assert caught.value.max_km2 == CAP
        assert 11_000 < caught.value.area_km2 < 12_000
        assert "100 km²" in str(caught.value)

    def test_too_large_is_still_an_area_error(self) -> None:
        """So a caller catching AreaError alone still gets a message, not a 500."""
        assert issubclass(area.AreaTooLargeError, area.AreaError)

    def test_the_cap_is_the_callers_not_a_constant(self) -> None:
        area.validate_bbox(DURG_BBOX, max_km2=9.0)
        with pytest.raises(area.AreaTooLargeError):
            area.validate_bbox(DURG_BBOX, max_km2=8.0)


class TestGeodesicArea:
    def test_a_degree_square_at_the_equator(self) -> None:
        """111.32 km x 110.57 km -- the textbook figure."""
        assert area.geodesic_area_km2(0.0, 0.0, 1.0, 1.0) == pytest.approx(12_308, rel=0.003)

    def test_the_same_box_shrinks_with_latitude(self) -> None:
        """Why the cap is measured on the ellipsoid: in degrees these are equal."""
        equator = area.geodesic_area_km2(0.0, 0.0, 1.0, 1.0)
        sixty = area.geodesic_area_km2(0.0, 60.0, 1.0, 61.0)
        # Not exactly the cosine: the ellipsoid's meridian degree lengthens toward
        # the pole, which a sphere would not show.
        assert sixty / equator == pytest.approx(math.cos(math.radians(60.5)), rel=0.02)


class TestTheMaskOnTheGrid:
    @pytest.fixture(scope="class")
    def grid_and_box(self):  # type: ignore[no-untyped-def]
        box = area.validate_bbox(DURG_BBOX, max_km2=CAP)
        return fake_dem(box.bounds, buffer_m=500.0), box

    def test_the_buffer_is_outside_and_the_middle_is_inside(self, grid_and_box) -> None:  # type: ignore[no-untyped-def]
        dem, box = grid_and_box
        inside = area.inside_mask(dem, box)
        assert inside.shape == dem.shape
        rows, cols = inside.shape
        assert inside[rows // 2, cols // 2]
        assert not inside[0, :].any() and not inside[-1, :].any()
        assert not inside[:, 0].any() and not inside[:, -1].any()

    def test_the_inside_cells_add_up_to_the_drawn_area(self, grid_and_box) -> None:  # type: ignore[no-untyped-def]
        dem, box = grid_and_box
        inside = area.inside_mask(dem, box)
        measured_km2 = inside.sum() * dem.cell_size_m**2 / 1e6
        assert measured_km2 == pytest.approx(box.area_km2, rel=0.03)

    def test_the_buffer_is_about_as_wide_as_asked(self, grid_and_box) -> None:  # type: ignore[no-untyped-def]
        dem, box = grid_and_box
        inside = area.inside_mask(dem, box)
        rows_inside = np.flatnonzero(inside.any(axis=1))
        top_buffer_m = rows_inside[0] * dem.cell_size_m
        assert 400.0 <= top_buffer_m <= 600.0

    def test_grid_bounds_cover_the_box_and_its_buffer(self, grid_and_box) -> None:  # type: ignore[no-untyped-def]
        dem, box = grid_and_box
        g = area.grid_bounds_4326(dem)
        # 500 m is ~0.0045° of latitude; allow for the cell-rounding of the grid.
        assert g.min_lat < box.min_lat - 0.004 and g.max_lat > box.max_lat + 0.004
        assert g.min_lon < box.min_lon - 0.004 and g.max_lon > box.max_lon + 0.004
