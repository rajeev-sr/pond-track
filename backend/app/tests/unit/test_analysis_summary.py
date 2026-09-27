"""The `summary` block: the three results the brief names, read off the site.

The expected water volume is the one figure computed here rather than copied, so
each branch of it is pinned: storage-limited, inflow-limited, no rainfall, and
no pond.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.contour_analysis import _summary


def site(
    *,
    live: float | None = 5_000.0,
    gross: float | None = 6_500.0,
    mean: float | None = 12_000.0,
    dependable: float | None = 8_000.0,
    pond_reason: str = "no buildable land at the site",
) -> dict[str, Any]:
    pond: dict[str, Any]
    if live is None:
        pond = {"available": False, "reason": pond_reason}
    else:
        pond = {
            "available": True,
            "recommended": {"live_storage_m3": live, "gross_capacity_m3": gross},
        }
    runoff: dict[str, Any]
    if dependable is None:
        runoff = {"available": False, "reason": "rainfall data was unavailable"}
    else:
        runoff = {
            "available": True,
            "annual_mean": {"runoff_volume_m3": mean},
            "design_75_percent_dependable": {"runoff_volume_m3": dependable},
        }
    return {
        "rank": 1,
        "suitability_score": 0.81,
        "location": {"lon": 81.2961, "lat": 21.2512},
        "catchment": {"metrics": {"area_ha": 180.4, "area_km2": 1.804}},
        "pond": pond,
        "runoff": runoff,
    }


class TestTheThreeResults:
    def test_location_and_catchment_come_straight_from_the_site(self) -> None:
        s = _summary(site())
        assert s["available"] is True
        assert s["pond_location"] == {"lat": 21.2512, "lon": 81.2961}
        assert s["catchment_area_ha"] == 180.4
        assert s["catchment_area_km2"] == 1.804
        assert s["site_rank"] == 1

    def test_storage_smaller_than_inflow_limits_the_volume(self) -> None:
        s = _summary(site(live=5_000.0, dependable=8_000.0))
        assert s["expected_water_volume_m3"] == 5_000.0
        assert s["expected_water_volume_limited_by"] == "storage"

    def test_inflow_smaller_than_storage_limits_the_volume(self) -> None:
        """Storage the catchment cannot fill in a normal year is not collectable."""
        s = _summary(site(live=9_000.0, dependable=4_000.0))
        assert s["expected_water_volume_m3"] == 4_000.0
        assert s["expected_water_volume_limited_by"] == "inflow"

    def test_the_dependable_inflow_is_used_not_the_mean(self) -> None:
        s = _summary(site(live=20_000.0, mean=15_000.0, dependable=9_000.0))
        assert s["expected_water_volume_m3"] == 9_000.0

    def test_both_inputs_are_reported_beside_the_answer(self) -> None:
        s = _summary(site())
        assert s["pond_capacity_m3"] == {"gross": 6_500.0, "live": 5_000.0}
        assert s["annual_inflow_m3"] == {"mean": 12_000.0, "dependable_75_percent": 8_000.0}
        assert "75 %" in s["expected_water_volume_basis"]


class TestDegradedInputs:
    def test_without_rainfall_the_volume_falls_back_to_storage_and_says_so(self) -> None:
        s = _summary(site(dependable=None))
        assert s["expected_water_volume_m3"] == 5_000.0
        assert s["expected_water_volume_limited_by"] == "storage"
        assert "rainfall was unavailable" in s["expected_water_volume_basis"]
        assert s["annual_inflow_m3"] == {"mean": None, "dependable_75_percent": None}

    def test_without_a_pond_there_is_no_volume_and_the_reason_is_given(self) -> None:
        s = _summary(site(live=None, pond_reason="the site is on a 14 % slope"))
        assert s["available"] is True  # the location and catchment still stand
        assert s["expected_water_volume_m3"] is None
        assert s["expected_water_volume_basis"] == "the site is on a 14 % slope"

    def test_no_site_at_all(self) -> None:
        s = _summary(None)
        assert s["available"] is False
        assert s["reason"]
        assert s["pond_location"] is None
        assert s["expected_water_volume_m3"] is None

    @pytest.mark.parametrize("volume", [4_321.987, 4_321.04])
    def test_the_volume_is_rounded_to_a_tenth(self, volume: float) -> None:
        s = _summary(site(live=volume, dependable=99_999.0))
        assert s["expected_water_volume_m3"] == round(volume, 1)
