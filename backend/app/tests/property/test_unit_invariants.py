"""Property tests for the invariants the plan asks for (IMPLEMENTATION_PLAN 7).

These catch whole classes of sign and scale errors that example-based tests miss.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.crs import utm_epsg_for, utm_zone_for
from app.core.units import (
    ha_to_m2,
    m2_to_ha,
    m_to_mm,
    mm_to_m,
    runoff_depth_mm_to_volume_m3,
)

finite = {"allow_nan": False, "allow_infinity": False}


@given(st.floats(min_value=0.0, max_value=1e6, **finite))
def test_mm_metre_roundtrip_preserves_value(mm: float) -> None:
    # Not bit-exact: dividing then multiplying by 1000 costs a few ULPs. What
    # matters is that no *material* precision is lost, so a relative tolerance
    # is the honest assertion here.
    assert m_to_mm(mm_to_m(mm)) == pytest.approx(mm, rel=1e-12, abs=1e-12)


@given(st.floats(min_value=0.0, max_value=1e9, **finite))
def test_hectare_roundtrip_preserves_value(ha: float) -> None:
    assert m2_to_ha(ha_to_m2(ha)) == pytest.approx(ha, rel=1e-12, abs=1e-12)


@given(
    depth=st.floats(min_value=0.0, max_value=5000.0, **finite),
    area=st.floats(min_value=1.0, max_value=1e9, **finite),
)
def test_runoff_volume_is_monotonic_in_depth(depth: float, area: float) -> None:
    """More rain can never produce less runoff."""
    assert runoff_depth_mm_to_volume_m3(depth + 1.0, area) > runoff_depth_mm_to_volume_m3(
        depth, area
    )


@given(
    depth=st.floats(min_value=0.1, max_value=5000.0, **finite),
    a=st.floats(min_value=1.0, max_value=1e8, **finite),
)
def test_runoff_volume_is_monotonic_in_area(depth: float, a: float) -> None:
    """A larger catchment can never yield less runoff at the same depth."""
    assert runoff_depth_mm_to_volume_m3(depth, a * 2) > runoff_depth_mm_to_volume_m3(depth, a)


@given(
    depth=st.floats(min_value=0.0, max_value=5000.0, **finite),
    area=st.floats(min_value=1.0, max_value=1e9, **finite),
)
def test_runoff_volume_never_exceeds_rainfall_volume(depth: float, area: float) -> None:
    """Runoff volume equals depth x area exactly; it can never exceed it.

    This is the dimensional guard: if the /1000 were dropped, volume would come
    out 1000x the rainfall volume and this property would fail immediately.
    """
    rainfall_volume = mm_to_m(depth) * area
    assert runoff_depth_mm_to_volume_m3(depth, area) <= rainfall_volume + 1e-9


@given(st.floats(min_value=-180.0, max_value=180.0, **finite))
def test_utm_zone_always_in_range(lon: float) -> None:
    assert 1 <= utm_zone_for(lon) <= 60


@given(
    lon=st.floats(min_value=-180.0, max_value=180.0, **finite),
    lat=st.floats(min_value=-90.0, max_value=90.0, **finite),
)
def test_utm_epsg_is_always_a_valid_wgs84_utm_code(lon: float, lat: float) -> None:
    epsg = utm_epsg_for(lon, lat)
    assert 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760


@given(
    lon=st.floats(min_value=-180.0, max_value=180.0, **finite),
    lat=st.floats(min_value=0.0, max_value=90.0, **finite),
)
def test_northern_and_southern_codes_differ_by_100(lon: float, lat: float) -> None:
    if lat == 0.0:
        return  # the equator is defined as northern
    assert utm_epsg_for(lon, -lat) - utm_epsg_for(lon, lat) == 100


@settings(max_examples=50)
@given(st.floats(min_value=68.0, max_value=97.5, **finite))
def test_all_indian_longitudes_land_in_zones_42_to_47(lon: float) -> None:
    assert 42 <= utm_zone_for(lon) <= 47
