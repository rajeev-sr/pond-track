"""Monthly water balance (FR-13).

The arithmetic is a loop; what needs pinning down is the physics it must not get
wrong. Storage cannot go negative, cannot exceed capacity, and cannot survive a
dry season that evaporates more than it holds. And the balance must *refuse* to
run without evaporation rather than quietly omitting the term — omitting it
overstates how long the pond lasts, which is the wrong direction to be wrong in.
"""

from __future__ import annotations

import pytest

from app.services import water_balance as wb

#: A deep clay-bedded pond of roughly the Durg design.
POND = {
    "bottom_length_m": 127.9,
    "bottom_width_m": 127.9,
    "depth_m": 4.5,
    "side_slope": 1.5,
    "capacity_m3": 81_682.0,
}

#: Monsoon-dominated, like central India.
RUNOFF = [1.1, 0.1, 0.0, 0.0, 0.1, 40.5, 117.4, 104.1, 62.0, 6.6, 0.2, 0.2]
ET0 = [109, 127, 181, 212, 233, 164, 105, 99, 109, 127, 114, 106]
CATCHMENT_M2 = 1_802_500.0


def run(**over: object) -> wb.WaterBalance:
    kwargs: dict = {
        "monthly_runoff_mm": RUNOFF,
        "catchment_area_m2": CATCHMENT_M2,
        "monthly_et0_mm": ET0,
        "soil_group": "D",
        **POND,
    }
    kwargs.update(over)
    return wb.simulate(**kwargs)  # type: ignore[arg-type]


class TestThePhysicsHolds:
    def test_storage_never_goes_negative(self) -> None:
        balance = run(monthly_runoff_mm=[0.0] * 12)
        assert all(m.storage_m3 >= 0.0 for m in balance.months)

    def test_storage_never_exceeds_capacity(self) -> None:
        balance = run(monthly_runoff_mm=[500.0] * 12)
        for m in balance.months:
            assert m.storage_m3 <= POND["capacity_m3"] + 1e-6, m

    def test_the_excess_is_reported_as_spill_not_lost(self) -> None:
        """A pond that silently swallows its overflow hides the fact that the
        catchment out-yields it, which is a planning conclusion."""
        balance = run(monthly_runoff_mm=[500.0] * 12)
        assert balance.total_spill_m3 > 0

    def test_a_pond_with_no_inflow_empties(self) -> None:
        balance = run(monthly_runoff_mm=[0.0] * 12)
        assert balance.months[-1].storage_m3 == pytest.approx(0.0, abs=1.0)
        assert balance.months_with_water == 0
        assert balance.reliability_pct == 0.0

    def test_losses_cannot_exceed_what_is_there(self) -> None:
        """Otherwise the pond runs a deficit and 'recovers' from a debt next month."""
        balance = run(monthly_runoff_mm=[0.0] * 12, monthly_et0_mm=[5000.0] * 12)
        for m in balance.months:
            assert m.storage_m3 >= 0.0
            assert m.evaporation_m3 >= 0.0

    def test_the_balance_closes_month_to_month(self) -> None:
        balance = run()
        for previous, current in zip(balance.months, balance.months[1:], strict=False):
            expected = (
                previous.storage_m3
                + current.inflow_m3
                - current.evaporation_m3
                - current.seepage_m3
                - current.spill_m3
            )
            assert current.storage_m3 == pytest.approx(expected, abs=1.0), current.month


class TestTheEvaporatingSurfaceShrinks:
    def test_area_falls_as_the_pond_empties(self) -> None:
        """Using the full top area all year would badly overstate losses."""
        balance = run()
        wet = max(balance.months, key=lambda m: m.storage_m3)
        dry = min(balance.months, key=lambda m: m.storage_m3)
        assert dry.surface_area_m2 < wet.surface_area_m2

    def test_an_empty_pond_has_no_surface(self) -> None:
        balance = run(monthly_runoff_mm=[0.0] * 12)
        assert balance.months[-1].surface_area_m2 == pytest.approx(0.0, abs=1.0)

    def test_depth_and_storage_move_together(self) -> None:
        balance = run()
        ordered = sorted(balance.months, key=lambda m: m.storage_m3)
        depths = [m.water_depth_m for m in ordered]
        assert depths == sorted(depths)


class TestSeepage:
    def test_clay_leaks_far_less_than_sand(self) -> None:
        clay = run(soil_group="D")
        sand = run(soil_group="A")
        assert sand.annual_seepage_m3 > clay.annual_seepage_m3 * 5

    def test_the_rate_used_is_reported(self) -> None:
        """It is the largest uncertainty in the model, so it cannot be implicit."""
        balance = run(soil_group="D")
        assert balance.seepage_mm_per_day == wb.SEEPAGE_MM_PER_DAY["D"]
        assert balance.as_dict()["parameters"]["seepage_mm_per_day"] == 2.0

    def test_an_unknown_soil_group_falls_back_rather_than_failing(self) -> None:
        balance = run(soil_group=None)
        assert balance.seepage_mm_per_day == wb.DEFAULT_SEEPAGE_MM_PER_DAY

    def test_it_can_be_overridden_for_a_lined_pond(self) -> None:
        lined = run(seepage_mm_per_day=0.2)
        unlined = run()
        assert lined.annual_seepage_m3 < unlined.annual_seepage_m3
        assert lined.months_with_water >= unlined.months_with_water


class TestItRefusesRatherThanGuessing:
    def test_no_evaporation_data_is_an_error(self) -> None:
        """Omitting the term would overstate how long the pond holds water."""
        with pytest.raises(ValueError, match="evapotranspiration"):
            run(monthly_et0_mm=None)

    def test_a_short_series_is_refused(self) -> None:
        with pytest.raises(ValueError, match="12 monthly"):
            run(monthly_runoff_mm=[1.0] * 6)

    def test_a_pond_with_no_capacity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no depth or no capacity"):
            run(capacity_m3=0.0)


class TestTheReportedAnswer:
    def test_it_names_the_month_it_runs_dry(self) -> None:
        """Measured after the peak: the question is when it runs out, not whether
        January of a pond that has not filled yet is empty."""
        # A *small* catchment, so the pond is water-limited. With the full
        # 1.8 km2 even a reduced runoff series overfills an 81,682 m3 pond and it
        # correctly never dries -- which is what the first version of this test
        # got wrong.
        balance = run(
            catchment_area_m2=120_000.0,
            monthly_runoff_mm=[0, 0, 0, 0, 0, 20, 40, 30, 10, 0, 0, 0],
        )
        assert balance.months_with_water < 12, "the pond should be water-limited here"
        assert balance.dry_month in wb.MONTHS

    def test_a_pond_that_holds_all_year_reports_no_dry_month(self) -> None:
        balance = run()
        if balance.months_with_water == 12:
            assert balance.dry_month is None

    def test_reliability_matches_the_months_counted(self) -> None:
        balance = run()
        assert balance.reliability_pct == pytest.approx(100.0 * balance.months_with_water / 12.0)

    def test_every_assumption_is_stated(self) -> None:
        balance = run()
        blob = " ".join(balance.assumptions).lower()
        for topic in ("evaporation", "seepage", "unlined", "average year"):
            assert topic in blob, topic

    def test_the_result_is_the_repeating_cycle_not_the_first_year(self) -> None:
        """Starting empty, year one under-reports; the spin-up removes that."""
        assert wb.SPIN_UP_YEARS >= 2
        balance = run()
        # A settled cycle: the last month leads back into the first consistently.
        expected = (
            balance.months[-1].storage_m3
            + balance.months[0].inflow_m3
            - balance.months[0].evaporation_m3
            - balance.months[0].seepage_m3
            - balance.months[0].spill_m3
        )
        assert balance.months[0].storage_m3 == pytest.approx(expected, abs=1.0)
