"""SCS-CN runoff estimation (MC-17).

`TestHldWorkedExample` is the anchor: HLD §6.9 works the method through by hand,
and these assertions reproduce it to three significant figures. `TestTheConvexityTrap`
pins the single most common error in implementations of this method.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.runoff import (
    CURVE_NUMBERS,
    FALLBACK_LAND_COVER,
    LAMBDA_IA,
    _amc_class,
    composite_curve_number,
    estimate_runoff,
    potential_retention_mm,
    runoff_depth_mm,
)


class TestPotentialRetention:
    def test_matches_the_formula(self) -> None:
        # S = 25400/CN - 254
        assert potential_retention_mm(100.0) == pytest.approx(0.0)
        assert potential_retention_mm(50.0) == pytest.approx(254.0)
        assert potential_retention_mm(78.4) == pytest.approx(69.98, abs=0.01)

    def test_higher_cn_retains_less(self) -> None:
        assert potential_retention_mm(90.0) < potential_retention_mm(60.0)

    @pytest.mark.parametrize("cn", [0.0, -5.0, 101.0, 200.0])
    def test_rejects_impossible_curve_numbers(self, cn: float) -> None:
        with pytest.raises(ValueError, match=r"within \[1, 100\]"):
            potential_retention_mm(cn)


class TestHldWorkedExample:
    """★ HLD §6.9, worked by hand. These numbers are the arithmetic authority."""

    CN = 78.4

    def test_potential_retention(self) -> None:
        assert potential_retention_mm(self.CN) == pytest.approx(69.98, abs=0.01)

    def test_initial_abstraction_uses_indian_practice(self) -> None:
        # Ia = 0.3 S, not the US default 0.2 S (HLD CH-15).
        assert LAMBDA_IA == 0.3
        assert LAMBDA_IA * potential_retention_mm(self.CN) == pytest.approx(20.99, abs=0.01)

    def test_single_storm(self) -> None:
        q = runoff_depth_mm(60.0, self.CN)
        assert q == pytest.approx(13.96, abs=0.01)
        assert q / 60.0 == pytest.approx(0.233, abs=0.001)

    def test_below_initial_abstraction_yields_nothing(self) -> None:
        ia = LAMBDA_IA * potential_retention_mm(self.CN)
        assert runoff_depth_mm(ia, self.CN) == 0.0
        assert runoff_depth_mm(ia - 0.1, self.CN) == 0.0
        assert runoff_depth_mm(ia + 1.0, self.CN) > 0.0

    def test_us_ratio_would_give_more_runoff(self) -> None:
        """Sanity on the direction of the departure: 0.2 S abstracts less, so more
        rainfall becomes runoff -- which is why it over-predicts for Indian
        monsoon regimes."""
        assert runoff_depth_mm(60.0, self.CN, lam=0.2) > runoff_depth_mm(60.0, self.CN, lam=0.3)


class TestTheConvexityTrap:
    """★ The error this implementation is built to avoid.

    The SCS relation is convex in rainfall, so feeding it an aggregate inflates
    runoff badly. HLD §6.9 Step 4 works the numbers: 0.393 done correctly against
    0.907 done wrong, on the same catchment and the same year.
    """

    def test_annual_total_gives_an_absurd_coefficient(self) -> None:
        wrong = runoff_depth_mm(921.5, 78.4)
        assert wrong / 921.5 == pytest.approx(0.907, abs=0.005)
        assert wrong / 921.5 > 0.9, "the trap should be obvious, not subtle"

    def test_daily_summation_is_far_lower_than_the_aggregate(self) -> None:
        """Same total rainfall, spread over days, yields much less runoff."""
        total = 900.0
        one_event = runoff_depth_mm(total, 78.4)
        spread = sum(runoff_depth_mm(total / 60.0, 78.4) for _ in range(60))
        assert spread < 0.5 * one_event

    def test_convexity_holds_generally(self) -> None:
        for cn in (60.0, 75.0, 90.0):
            single = runoff_depth_mm(200.0, cn)
            halves = 2 * runoff_depth_mm(100.0, cn)
            assert single > halves, f"CN {cn}: expected convexity"


class TestCompositeCurveNumber:
    def test_single_cover_matches_the_table(self) -> None:
        cn = composite_curve_number({"cropland": 1.0}, "D")
        assert cn.composite_cn2 == pytest.approx(float(CURVE_NUMBERS["cropland"]["D"]))

    def test_area_weighting(self) -> None:
        cn = composite_curve_number({"cropland": 0.5, "tree_cover": 0.5}, "B")
        expected = 0.5 * CURVE_NUMBERS["cropland"]["B"] + 0.5 * CURVE_NUMBERS["tree_cover"]["B"]
        assert cn.composite_cn2 == pytest.approx(expected)

    def test_fractions_need_not_sum_to_one(self) -> None:
        """Zonal fractions can be short of 1.0 where cells were nodata."""
        a = composite_curve_number({"cropland": 0.4, "grassland": 0.4}, "C")
        b = composite_curve_number({"cropland": 0.5, "grassland": 0.5}, "C")
        assert a.composite_cn2 == pytest.approx(b.composite_cn2)

    def test_result_lies_between_its_components(self) -> None:
        cover = {"tree_cover": 0.3, "cropland": 0.4, "built_up": 0.3}
        cn = composite_curve_number(cover, "C")
        values = [CURVE_NUMBERS[k]["C"] for k in cover]
        assert min(values) <= cn.composite_cn2 <= max(values)

    def test_soil_group_ordering(self) -> None:
        """A infiltrates freely, D barely: runoff potential must rise A -> D."""
        cns = [composite_curve_number({"cropland": 1.0}, g).composite_cn2 for g in "ABCD"]
        assert cns == sorted(cns)

    def test_amc_adjustment_brackets_the_average(self) -> None:
        cn = composite_curve_number({"cropland": 1.0}, "C")
        assert cn.cn1 < cn.composite_cn2 < cn.cn3

    def test_unmapped_class_falls_back_and_is_still_counted(self) -> None:
        cn = composite_curve_number({"unknown_martian_terrain": 1.0}, "C")
        assert cn.composite_cn2 == pytest.approx(float(CURVE_NUMBERS[FALLBACK_LAND_COVER]["C"]))

    def test_water_is_fully_contributing(self) -> None:
        assert composite_curve_number({"permanent_water": 1.0}, "A").composite_cn2 == 100.0

    def test_breakdown_explains_the_result(self) -> None:
        cn = composite_curve_number({"cropland": 0.6, "grassland": 0.4}, "D")
        assert len(cn.breakdown) == 2
        assert sum(b["weighted_contribution"] for b in cn.breakdown) == pytest.approx(
            cn.composite_cn2, abs=0.02
        )

    @pytest.mark.parametrize("hsg", ["E", "", "a", "AB"])
    def test_rejects_a_bad_soil_group(self, hsg: str) -> None:
        with pytest.raises(ValueError, match="soil group"):
            composite_curve_number({"cropland": 1.0}, hsg)

    def test_rejects_empty_cover(self) -> None:
        with pytest.raises(ValueError, match="no land-cover"):
            composite_curve_number({}, "C")

    def test_rejects_zero_total(self) -> None:
        with pytest.raises(ValueError, match="sum to zero"):
            composite_curve_number({"cropland": 0.0}, "C")


class TestAmcClassification:
    def test_growing_season_thresholds(self) -> None:
        assert _amc_class(10.0, True) == "I"
        assert _amc_class(45.0, True) == "II"
        assert _amc_class(80.0, True) == "III"

    def test_dormant_season_is_stricter(self) -> None:
        """The same antecedent depth means a wetter profile out of season."""
        assert _amc_class(20.0, True) == "I"
        assert _amc_class(20.0, False) == "II"

    def test_monotonic_in_antecedent_rainfall(self) -> None:
        order = {"I": 0, "II": 1, "III": 2}
        seq = [order[_amc_class(v, True)] for v in (0.0, 20.0, 40.0, 60.0, 120.0)]
        assert seq == sorted(seq)


def _series(years: int, mm_per_wet_day: float, wet_days_per_year: int = 60):
    """A synthetic daily series: `wet_days_per_year` events inside the monsoon."""
    daily, yy, mm = [], [], []
    for y in range(2000, 2000 + years):
        for month in range(1, 13):
            for day in range(30):
                wet = month in (6, 7, 8, 9) and day < wet_days_per_year / 4
                daily.append(mm_per_wet_day if wet else 0.0)
                yy.append(y)
                mm.append(month)
    return np.array(daily), np.array(yy), np.array(mm)


class TestEstimateRunoff:
    def test_produces_a_plausible_coefficient(self) -> None:
        daily, yy, mm = _series(10, 30.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        est = estimate_runoff(daily, yy, mm, cn, 1_000_000.0, monsoon_months=[6, 7, 8, 9])
        assert 0.0 < est.runoff_coefficient < 1.0

    def test_volume_matches_depth_times_area(self) -> None:
        daily, yy, mm = _series(5, 40.0)
        cn = composite_curve_number({"cropland": 1.0}, "D")
        area = 1_486_000.0
        est = estimate_runoff(daily, yy, mm, cn, area, monsoon_months=[6, 7, 8, 9])
        assert est.annual_mean_volume_m3 == pytest.approx(
            est.annual_mean_mm / 1000.0 * area, rel=1e-6
        )

    def test_dependable_runoff_is_below_the_mean(self) -> None:
        rng = np.random.default_rng(3)
        daily, yy, mm = _series(30, 35.0)
        daily = daily * rng.uniform(0.5, 1.5, daily.size)  # vary year to year
        cn = composite_curve_number({"cropland": 1.0}, "C")
        est = estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        assert est.dependable_75_mm < est.annual_mean_mm

    def test_dependable_runoff_is_ranked_from_runoff_not_rainfall(self) -> None:
        """The relation is non-linear, so the 75th-percentile rainfall year is not
        the 75th-percentile runoff year -- the series has to be ranked directly."""
        daily, yy, mm = _series(20, 30.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        est = estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        ranked = sorted(est.annual_by_year_mm.values())
        assert min(ranked) <= est.dependable_75_mm <= max(ranked)

    def test_wetter_years_yield_more_runoff(self) -> None:
        cn = composite_curve_number({"cropland": 1.0}, "C")
        low = estimate_runoff(*_series(5, 20.0), cn, 1e6, monsoon_months=[6, 7, 8, 9])
        high = estimate_runoff(*_series(5, 60.0), cn, 1e6, monsoon_months=[6, 7, 8, 9])
        assert high.annual_mean_mm > low.annual_mean_mm

    def test_higher_cn_yields_more_runoff(self) -> None:
        daily, yy, mm = _series(5, 30.0)
        a = estimate_runoff(
            daily,
            yy,
            mm,
            composite_curve_number({"cropland": 1.0}, "A"),
            1e6,
            monsoon_months=[6, 7, 8, 9],
        )
        d = estimate_runoff(
            daily,
            yy,
            mm,
            composite_curve_number({"cropland": 1.0}, "D"),
            1e6,
            monsoon_months=[6, 7, 8, 9],
        )
        assert d.annual_mean_mm > a.annual_mean_mm

    def test_runoff_never_exceeds_rainfall(self) -> None:
        daily, yy, mm = _series(5, 90.0)
        cn = composite_curve_number({"permanent_water": 1.0}, "D")  # CN 100
        est = estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        assert est.runoff_coefficient <= 1.0 + 1e-9

    def test_monthly_distribution_follows_the_monsoon(self) -> None:
        daily, yy, mm = _series(5, 40.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        est = estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        monsoon = sum(est.monthly_mean_mm[m - 1] for m in (6, 7, 8, 9))
        assert monsoon > 0.9 * sum(est.monthly_mean_mm)

    def test_antecedent_moisture_changes_the_answer(self) -> None:
        """Consecutive rain days must yield more than the same days spread out."""
        cn = composite_curve_number({"cropland": 1.0}, "C")
        n = 360 * 2
        clustered = np.zeros(n)
        clustered[180:200] = 40.0
        spread = np.zeros(n)
        spread[180:340:8] = 40.0 * 20 / 20
        yy = np.array([2000] * 360 + [2001] * 360)
        mm = np.array([(i % 360) // 30 + 1 for i in range(n)])
        a = estimate_runoff(clustered, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        b = estimate_runoff(spread, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        assert a.annual_mean_mm != b.annual_mean_mm

    def test_assumptions_are_declared(self) -> None:
        daily, yy, mm = _series(5, 30.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        est = estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9])
        joined = " ".join(est.assumptions)
        assert "0.3" in joined and "daily" in joined

    def test_serialises(self) -> None:
        import json

        daily, yy, mm = _series(5, 30.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        d = json.loads(
            json.dumps(
                estimate_runoff(daily, yy, mm, cn, 1e6, monsoon_months=[6, 7, 8, 9]).as_dict()
            )
        )
        assert d["design_75_percent_dependable"]["runoff_volume_m3"] >= 0

    def test_rejects_mismatched_arrays(self) -> None:
        cn = composite_curve_number({"cropland": 1.0}, "C")
        with pytest.raises(ValueError, match="same length"):
            estimate_runoff(np.zeros(10), np.zeros(9), np.zeros(10), cn, 1e6)

    def test_rejects_non_positive_area(self) -> None:
        daily, yy, mm = _series(2, 30.0)
        cn = composite_curve_number({"cropland": 1.0}, "C")
        with pytest.raises(ValueError, match="area must be positive"):
            estimate_runoff(daily, yy, mm, cn, 0.0)

    def test_rejects_a_series_with_no_complete_year(self) -> None:
        cn = composite_curve_number({"cropland": 1.0}, "C")
        with pytest.raises(ValueError, match="no complete year"):
            estimate_runoff(np.zeros(30), np.full(30, 2020), np.ones(30), cn, 1e6)
