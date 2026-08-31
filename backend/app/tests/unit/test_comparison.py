"""Side-by-side site comparison (FR-12).

The value of this module is not the arithmetic — it is refusing to manufacture a
decision. A metric that barely differs must pick no winner, and a metric that is
constant by construction must say so. Both were wrong in the first version.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import comparison


def site(
    rank: int,
    *,
    score: float = 70.0,
    catchment_ha: float = 100.0,
    capacity: float | None = 20_000.0,
    live: float | None = 18_000.0,
    cost: float | None = 3_000_000.0,
    runoff: float | None = 200_000.0,
    binding: str = "practical_excavation_depth",
    months: int | None = 12,
    kind: str = "channel_position",
) -> dict[str, Any]:
    pond: dict[str, Any] = {"available": capacity is not None, "binding_constraint": binding}
    if capacity is not None:
        pond["recommended"] = {
            "gross_capacity_m3": capacity,
            "live_storage_m3": live,
            "estimated_cost_inr": cost,
        }
    if months is not None:
        pond["water_balance"] = {"available": True, "months_with_water": months}
    return {
        "rank": rank,
        "suitability_score": score,
        "site_kind": kind,
        "catchment": {"metrics": {"area_ha": catchment_ha}},
        "runoff": {"available": runoff is not None, "annual_mean": {"runoff_volume_m3": runoff}},
        "pond": pond,
    }


def row(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(r for r in result["metrics"] if r["metric"] == key)


class TestItPicksTheRightWinner:
    def test_higher_is_better_for_capacity(self) -> None:
        out = comparison.compare([site(1, capacity=10_000), site(2, capacity=50_000)])
        assert row(out, "gross_capacity_m3")["best_rank"] == 2

    def test_lower_is_better_for_cost(self) -> None:
        """A comparison that treats cost like capacity recommends the priciest."""
        out = comparison.compare([site(1, cost=9_000_000), site(2, cost=2_000_000)])
        assert row(out, "estimated_cost_inr")["best_rank"] == 2
        assert row(out, "estimated_cost_inr")["higher_is_better"] is False

    def test_it_counts_how_many_metrics_each_site_leads(self) -> None:
        out = comparison.compare(
            [site(1, capacity=50_000, cost=9_000_000), site(2, capacity=10_000, cost=1_000_000)]
        )
        assert out["leads_on_count"]["1"] >= 1
        assert out["leads_on_count"]["2"] >= 1


class TestItRefusesToManufactureADifference:
    def test_a_negligible_spread_picks_nobody(self) -> None:
        """Cost per m3 of live storage differs by ~0.006 % between real sites,
        because cost is volume times a flat rate. An absolute epsilon declared a
        winner on that, which is a decision based on an artefact."""
        out = comparison.compare(
            [
                site(1, capacity=80_000, live=73_514, cost=12_048_099),
                site(2, capacity=22_270, live=20_043, cost=3_284_766),
            ]
        )
        cost_per = row(out, "cost_per_live_m3")
        assert cost_per["uniform"] is True
        assert cost_per["best_rank"] is None

    def test_identical_values_pick_nobody(self) -> None:
        out = comparison.compare([site(1, months=12), site(2, months=12)])
        assert row(out, "months_with_water")["uniform"] is True

    def test_a_real_difference_still_picks_someone(self) -> None:
        out = comparison.compare([site(1, months=12), site(2, months=6)])
        months = row(out, "months_with_water")
        assert months["uniform"] is False
        assert months["best_rank"] == 1

    def test_the_notes_explain_the_uniform_rows(self) -> None:
        out = comparison.compare([site(1), site(2)])
        blob = " ".join(out["notes"])
        assert "cannot inform a choice" in blob
        assert "flat rate" in blob

    def test_it_warns_that_scores_are_run_relative(self) -> None:
        """Normalised across the candidate set, so 72 here is not 72 elsewhere."""
        out = comparison.compare([site(1), site(2)])
        assert any("comparable only within one analysis" in n for n in out["notes"])


class TestTheDerivedMetrics:
    def test_capture_fraction_separates_a_matched_pond_from_a_dwarfed_one(self) -> None:
        """The real finding on the sample sheet: one site holds 30 % of its
        yield and another holds 1 %, and neither payload says so on its own."""
        out = comparison.compare(
            [
                site(1, capacity=20_000, runoff=66_000),  # ~30 %
                site(2, capacity=13_929, runoff=1_286_044),  # ~1 %
            ]
        )
        values = row(out, "capture_fraction_pct")["values"]
        assert values["1"] == pytest.approx(30.3, abs=0.5)
        assert values["2"] == pytest.approx(1.1, abs=0.5)
        assert row(out, "capture_fraction_pct")["best_rank"] == 1

    def test_a_site_without_a_pond_reports_none_rather_than_zero(self) -> None:
        """Zero would rank it last on capacity as though it were measured."""
        out = comparison.compare([site(1), site(2, capacity=None)])
        assert row(out, "gross_capacity_m3")["values"]["2"] is None
        assert row(out, "gross_capacity_m3")["best_rank"] == 1

    def test_a_metric_missing_everywhere_is_called_out(self) -> None:
        out = comparison.compare([site(1, runoff=None), site(2, runoff=None)])
        assert any("degraded tier" in n for n in out["notes"])


class TestTheTradeOffSummary:
    def test_each_site_gets_its_binding_constraint_explained(self) -> None:
        out = comparison.compare(
            [site(1, binding="parcel_area"), site(2, binding="sustainable_yield_share")]
        )
        first, second = out["trade_offs"]
        assert "acquiring adjacent plots" in first["what_that_means"]
        assert "stand empty" in second["what_that_means"]

    def test_it_says_what_each_site_leads_and_trails_on(self) -> None:
        out = comparison.compare([site(1, capacity=50_000), site(2, capacity=10_000)])
        first = out["trade_offs"][0]
        assert "Pond capacity" in first["leads_on"]
        assert isinstance(first["behind_on"], list)

    def test_an_unknown_constraint_does_not_break_it(self) -> None:
        out = comparison.compare([site(1, binding="something_new"), site(2)])
        assert out["trade_offs"][0]["what_that_means"]


class TestItRefusesBadInput:
    def test_one_site_is_not_a_comparison(self) -> None:
        with pytest.raises(ValueError, match="between 2 and 5"):
            comparison.compare([site(1)])

    def test_more_than_five_is_refused(self) -> None:
        with pytest.raises(ValueError, match="between 2 and 5"):
            comparison.compare([site(i) for i in range(1, 7)])

    def test_the_same_site_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="same site"):
            comparison.compare([site(1), site(1)])
