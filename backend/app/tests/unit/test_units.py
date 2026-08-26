"""Unit conversions. Guards the HLD CH-10 failure class."""

from __future__ import annotations

import pytest

from app.core.units import (
    cells_to_area_m2,
    ha_to_km2,
    ha_to_m2,
    km2_to_m2,
    m2_to_ha,
    m2_to_km2,
    m_to_mm,
    mm_to_m,
    runoff_depth_mm_to_volume_m3,
)


class TestScalarConversions:
    def test_mm_metre_roundtrip(self) -> None:
        assert mm_to_m(1000.0) == 1.0
        assert m_to_mm(1.0) == 1000.0
        assert mm_to_m(m_to_mm(3.5)) == pytest.approx(3.5)

    def test_hectare_conversions(self) -> None:
        assert ha_to_m2(1.0) == 10_000.0
        assert m2_to_ha(10_000.0) == 1.0
        assert ha_to_m2(148.6) == 1_486_000.0  # HLD 6.9 catchment

    def test_km2_conversions(self) -> None:
        assert km2_to_m2(1.0) == 1_000_000.0
        assert m2_to_km2(1_486_000.0) == pytest.approx(1.486)

    def test_ha_to_km2(self) -> None:
        assert ha_to_km2(148.6) == pytest.approx(1.486)
        assert ha_to_km2(100.0) == pytest.approx(1.0)


class TestRunoffVolume:
    """The exact computation HLD 6.9 works through by hand."""

    def test_reproduces_hld_worked_example(self) -> None:
        # Q = 361.8 mm over a 148.6 ha catchment -> 537,635 m3
        volume = runoff_depth_mm_to_volume_m3(361.8, ha_to_m2(148.6))
        assert volume == pytest.approx(537_634.8, abs=1.0)

    def test_reproduces_single_storm_from_hld(self) -> None:
        # Q = 13.96 mm for the 60 mm storm -> 20,745 m3
        volume = runoff_depth_mm_to_volume_m3(13.96, ha_to_m2(148.6))
        assert volume == pytest.approx(20_745, abs=5.0)

    def test_the_thousand_fold_error_is_not_possible_silently(self) -> None:
        # Forgetting the /1000 would give 537 million, not 537 thousand. This
        # test exists so that regression is caught, not merely documented.
        volume = runoff_depth_mm_to_volume_m3(361.8, 1_486_000.0)
        assert volume < 1_000_000, "runoff volume looks like mm*m2 - the /1000 is missing"

    def test_zero_depth_gives_zero_volume(self) -> None:
        assert runoff_depth_mm_to_volume_m3(0.0, 1_486_000.0) == 0.0

    def test_rejects_negative_depth(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            runoff_depth_mm_to_volume_m3(-1.0, 1000.0)

    @pytest.mark.parametrize("area", [0.0, -1.0])
    def test_rejects_non_positive_area(self, area: float) -> None:
        with pytest.raises(ValueError, match="positive"):
            runoff_depth_mm_to_volume_m3(10.0, area)


class TestCellsToArea:
    def test_srtm_cell_counting(self) -> None:
        # A 25 km2 AOI at 30 m is 27,778 cells (HLD 9.1).
        assert cells_to_area_m2(27_778, 30.0) == pytest.approx(25_000_200.0)

    def test_hld_9_1_raster_is_small(self) -> None:
        # Sanity-anchors the corrected sizing claim in HLD 9.1: a village AOI is
        # a 167x167 raster, not something that needs gigabytes.
        assert cells_to_area_m2(167 * 167, 30.0) == pytest.approx(25_100_100.0)

    def test_zero_cells(self) -> None:
        assert cells_to_area_m2(0, 30.0) == 0.0

    def test_rejects_bad_inputs(self) -> None:
        with pytest.raises(ValueError):
            cells_to_area_m2(-1, 30.0)
        with pytest.raises(ValueError):
            cells_to_area_m2(10, 0.0)
