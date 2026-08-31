"""Plain-language explanation (FR-14), with no language model involved.

The property that justifies the template approach is **determinism**: the same
analysis must produce the same words, every time, so a recommendation can be
reproduced months later when someone asks why a site was chosen. A generated
paragraph cannot promise that. So that is what is tested hardest here, alongside
the rule that every clause must come from a real field rather than an assertion
the data does not support.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import explain


def site(
    rank: int = 1,
    *,
    score: float = 81.3,
    area_ha: float = 180.25,
    binding: str = "practical_excavation_depth",
    capacity: float = 81_682.0,
    runoff: float | None = 599_007.0,
    touches_edge: bool = False,
    months: int | None = 12,
) -> dict[str, Any]:
    return {
        "rank": rank,
        "suitability_score": score,
        "site_kind": "channel_position",
        "catchment": {
            "metrics": {"area_ha": area_ha},
            "quality": {"touches_survey_edge": touches_edge},
        },
        "criteria_breakdown": [
            {"criterion": "flow_accumulation", "contribution": 0.28},
            {"criterion": "depression_depth", "contribution": 0.19},
            {"criterion": "slope", "contribution": 0.11},
        ],
        "runoff": {
            "available": runoff is not None,
            "annual_mean": {"runoff_volume_m3": runoff},
            "design_75_percent_dependable": {"runoff_volume_m3": 418_652.0},
            "curve_number": {"composite_cn_amc2": 87.7},
        },
        "pond": {
            "available": True,
            "binding_constraint": binding,
            "recommended": {
                "depth_m": 4.5,
                "top_length_m": 141.4,
                "top_width_m": 141.4,
                "gross_capacity_m3": capacity,
                "estimated_cost_inr": 12_048_099.0,
            },
            "water_balance": (
                {
                    "available": True,
                    "months_with_water": months,
                    "dry_month": "May",
                    "annual_losses_m3": {"evaporation": 33_818.0, "seepage": 14_255.0},
                }
                if months is not None
                else {"available": False}
            ),
        },
    }


class TestNoLanguageModelIsInvolved:
    def test_the_same_input_gives_the_same_words(self) -> None:
        """The property the whole approach exists for."""
        first = explain.explain_site(site())
        second = explain.explain_site(site())
        assert first.summary == second.summary
        assert first.caveats == second.caveats

    def test_it_says_how_it_was_generated(self) -> None:
        block = explain.explain_site(site()).as_dict()
        assert "no language model" in block["generated_by"]

    def test_the_module_imports_nothing_that_calls_out(self) -> None:
        import inspect

        source = inspect.getsource(explain)
        for forbidden in ("openai", "anthropic", "requests", "httpx", "urllib"):
            assert forbidden not in source, f"{forbidden} has no business here"


class TestEveryClauseComesFromAField:
    def test_it_names_the_score_and_the_catchment(self) -> None:
        summary = explain.explain_site(site(), rank_count=5).summary
        assert "81.3" in summary
        assert "180 hectares" in summary
        assert "5 assessed" in summary

    def test_it_names_the_two_criteria_that_carried_the_score(self) -> None:
        summary = explain.explain_site(site()).summary
        assert "flow accumulation" in summary
        assert "depression depth" in summary
        # The third contributed less and is deliberately not named.
        assert "slope (" not in summary

    def test_it_quotes_the_pond_and_its_cost_in_indian_grouping(self) -> None:
        summary = explain.explain_site(site()).summary
        assert "81,682" in summary
        assert "1,20,48,099" in summary, "cost should use lakh/crore grouping"

    def test_it_explains_the_binding_constraint_in_words(self) -> None:
        summary = explain.explain_site(site(binding="parcel_area")).summary
        assert "limits it is land" in summary
        assert "adjacent plot" in summary

    @pytest.mark.parametrize(
        "binding",
        ["parcel_area", "practical_excavation_depth", "sustainable_yield_share", "runoff_yield"],
    )
    def test_every_constraint_has_wording(self, binding: str) -> None:
        assert binding in explain.CONSTRAINT_SENTENCE

    def test_an_unknown_constraint_is_simply_omitted(self) -> None:
        """Better a shorter paragraph than an invented explanation."""
        summary = explain.explain_site(site(binding="something_new")).summary
        assert "What limits it" not in summary
        assert summary


class TestTheCaveats:
    def test_tenure_is_always_flagged(self) -> None:
        """The largest gap between recommended and buildable, on every site."""
        caveats = explain.explain_site(site()).caveats
        assert any("tenure" in c.lower() for c in caveats)

    def test_a_clipped_catchment_is_flagged_as_a_lower_bound(self) -> None:
        caveats = explain.explain_site(site(touches_edge=True)).caveats
        assert any("lower bound" in c for c in caveats)

    def test_a_degraded_tier_is_explained(self) -> None:
        caveats = explain.explain_site(site(), {"analysis_tier": "terrain_only"}).caveats
        assert any("terrain-suitability ranking" in c for c in caveats)

    def test_a_full_tier_adds_no_tier_caveat(self) -> None:
        caveats = explain.explain_site(site(), {"analysis_tier": "full"}).caveats
        assert not any("unavailable for this run" in c for c in caveats)

    def test_a_pond_dwarfed_by_its_catchment_is_called_out(self) -> None:
        """Capacity 13,929 against 1,286,044 m3 of yield is about 1 %."""
        caveats = explain.explain_site(site(capacity=13_929.0, runoff=1_286_044.0)).caveats
        assert any("limiting factor" in c for c in caveats)

    def test_a_well_matched_pond_is_not(self) -> None:
        caveats = explain.explain_site(site()).caveats
        assert not any("limiting factor" in c for c in caveats)

    def test_a_narrow_margin_warns_against_reading_it_as_a_ranking(self) -> None:
        caveats = explain.explain_site(site(score=81.3), runner_up_score=80.1).caveats
        assert any("comparable rather than ranked" in c for c in caveats)

    def test_a_clear_margin_does_not(self) -> None:
        caveats = explain.explain_site(site(score=81.3), runner_up_score=60.0).caveats
        assert not any("comparable rather than ranked" in c for c in caveats)


class TestDegradedInputs:
    def test_no_runoff_still_produces_a_paragraph(self) -> None:
        summary = explain.explain_site(site(runoff=None)).summary
        assert "81.3" in summary
        assert "runoff in an average year" not in summary

    def test_no_water_balance_omits_the_reliability_sentence(self) -> None:
        summary = explain.explain_site(site(months=None)).summary
        assert "twelve months" not in summary

    def test_a_pond_that_could_not_be_sized_says_why(self) -> None:
        broken = site()
        broken["pond"] = {"available": False, "reason": "the parcel is too small to close"}
        assert "too small to close" in explain.explain_site(broken).summary

    def test_an_analysis_with_no_sites_reports_unavailable(self) -> None:
        out = explain.explain_analysis({"candidate_sites": []})
        assert out["available"] is False
        assert "nothing to explain" in out["reason"]


class TestTheWholeAnalysis:
    def test_it_explains_the_runner_up_sites_too(self) -> None:
        out = explain.explain_analysis(
            {
                "candidate_sites": [site(1), site(2, score=72.3), site(3, score=70.1)],
                "environment": {"analysis_tier": "full"},
            }
        )
        assert out["available"] is True
        assert len(out["alternatives"]) == 2

    def test_the_recommended_site_gets_the_margin_check(self) -> None:
        out = explain.explain_analysis(
            {"candidate_sites": [site(1, score=81.3), site(2, score=80.0)]}
        )
        assert any("comparable rather than ranked" in c for c in out["recommended"]["caveats"])
