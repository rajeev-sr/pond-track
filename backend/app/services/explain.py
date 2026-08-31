"""Plain-language explanation of a recommendation (FR-14).

**No language model is involved, and that is a design decision rather than a
limitation.** The obvious way to satisfy "natural-language explanation" is to
hand the numbers to an LLM and print what comes back. That would be worse here
for three reasons that matter more than fluency:

* **Determinism.** The same analysis must produce the same words. A recommendation
  a village acts on cannot vary between two runs of identical inputs, and it has
  to be reproducible months later when someone asks why a site was chosen.
* **Traceability.** Every clause below is generated from a named field, so any
  sentence can be checked against the JSON that produced it. A generated
  paragraph cannot make that promise: it can restate a number correctly and still
  assert a causal claim the data does not support.
* **No dependency.** The system runs offline on a local machine with no
  credentials. Adding a model API would break that for prose.

So this is a template engine over computed values. It reads like prose because
the sentences are written by a person and the *numbers* are substituted -- the
ordering, the emphasis, and which caveat leads are all decided by the data.

The structure is deliberate: **decision, then reason, then limit, then caveat.**
A reader who stops after one sentence should still have the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Wording per binding constraint. The constraint names which variable stopped
#: the design growing; this says what to do about it, which is the half a
#: planner can act on.
CONSTRAINT_SENTENCE: dict[str, str] = {
    "parcel_area": (
        "What limits it is land, not water: the buildable patch around the site "
        "runs out before the depth does. Acquiring or consolidating an adjacent "
        "plot would raise capacity more than any change to the design."
    ),
    "practical_excavation_depth": (
        "What limits it is depth: the pond is already as deep as is practical to "
        "excavate and maintain by ordinary means. Widening it is the cheaper way "
        "to add capacity than digging further."
    ),
    "sustainable_yield_share": (
        "What limits it is the catchment's yield: the pond is already sized to "
        "the share of runoff it is reasonable to impound. A larger structure "
        "would stand part-empty in an average year."
    ),
    "runoff_yield": (
        "What limits it is water. There is not enough runoff to justify a larger "
        "pond here, so the constraint is neither land nor depth."
    ),
    "budget": (
        "What limits it is the cost ceiling, which bound the design before any "
        "physical limit did."
    ),
}

TIER_SENTENCE: dict[str, str] = {
    "full": "",
    "no_soil_lulc": (
        "Soil and land cover were unavailable for this run, so runoff uses an "
        "assumed soil group and land availability did not contribute to the "
        "score. The catchment and the pond geometry are unaffected."
    ),
    "terrain_only": (
        "Only the uploaded terrain was available: no soil, land cover or rainfall "
        "layer answered. This is a terrain-suitability ranking, and the runoff "
        "and pond-sizing figures are absent rather than estimated."
    ),
}

#: Below this the pond captures so little of its catchment that the structure,
#: not the water, is plainly the limiting factor -- worth saying explicitly.
LOW_CAPTURE_PCT = 5.0

#: A score this close together means the ranking is not meaningfully decisive.
NARROW_MARGIN = 3.0


@dataclass(frozen=True)
class Explanation:
    """The paragraph, plus the sentences it was assembled from."""

    summary: str
    sentences: tuple[str, ...]
    caveats: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "sentences": list(self.sentences),
            "caveats": list(self.caveats),
            "generated_by": (
                "deterministic templates over the computed values -- no language "
                "model. The same analysis always produces the same words, and "
                "every clause traces to a named field in the result."
            ),
        }


def _volume(value: float | None) -> str:
    return "—" if value is None else f"{int(round(value)):,} m³"


def _rupees(value: float | None) -> str:
    if value is None:
        return "—"
    text = str(int(round(value)))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join(parts) + "," + tail
    return f"₹{text}"


def _top_criteria(site: dict[str, Any], count: int = 2) -> list[tuple[str, float]]:
    rows = site.get("criteria_breakdown") or []
    ranked = sorted(
        ((str(r.get("criterion", "")), float(r.get("contribution", 0.0))) for r in rows),
        key=lambda pair: -pair[1],
    )
    return ranked[:count]


def explain_site(
    site: dict[str, Any],
    environment: dict[str, Any] | None = None,
    *,
    rank_count: int | None = None,
    runner_up_score: float | None = None,
) -> Explanation:
    """A paragraph explaining one site, assembled from its own numbers."""
    environment = environment or {}
    sentences: list[str] = []
    caveats: list[str] = []

    rank = site.get("rank")
    score = site.get("suitability_score")
    kind = str(site.get("site_kind", "")).replace("_", " ")
    metrics = (site.get("catchment") or {}).get("metrics") or {}
    area_ha = metrics.get("area_ha")

    # 1. The decision.
    opening = (
        f"Site #{rank} is the strongest of the {rank_count} assessed"
        if rank_count
        else f"Site #{rank}"
    )
    if score is not None:
        opening += f", scoring {score:g} out of 100"
    if kind:
        opening += f". It is a {kind}"
    if area_ha:
        opening += f" draining {area_ha:,.0f} hectares"
    sentences.append(opening.rstrip(".") + ".")

    # 2. Why it scored what it did, from the two criteria that carried it.
    top = _top_criteria(site)
    if top:
        named = " and ".join(
            f"{name.replace('_', ' ')} ({value:.2f} of the score)" for name, value in top
        )
        sentences.append(f"The ranking is carried mostly by {named}.")

    # 3. Water.
    runoff = site.get("runoff") or {}
    if runoff.get("available"):
        annual = (runoff.get("annual_mean") or {}).get("runoff_volume_m3")
        dependable = (runoff.get("design_75_percent_dependable") or {}).get("runoff_volume_m3")
        cn = (runoff.get("curve_number") or {}).get("composite_cn_amc2")
        piece = f"The catchment yields about {_volume(annual)} of runoff in an average year"
        if dependable:
            piece += f", and {_volume(dependable)} in three years out of four"
        if cn:
            piece += f" (composite curve number {cn:g})"
        sentences.append(piece + ".")

    # 4. The pond, and what limits it.
    pond = site.get("pond") or {}
    design = pond.get("recommended") if pond.get("available") else None
    if design:
        sentences.append(
            f"A pond of {design['depth_m']:g} m depth and about "
            f"{design['top_length_m']:,.0f} \u00d7 {design['top_width_m']:,.0f} m fits here, "
            f"holding {_volume(design.get('gross_capacity_m3'))} at an indicative cost of "
            f"{_rupees(design.get('estimated_cost_inr'))}."
        )
        binding = str(pond.get("binding_constraint") or "")
        if binding in CONSTRAINT_SENTENCE:
            sentences.append(CONSTRAINT_SENTENCE[binding])
    elif pond.get("reason"):
        sentences.append(f"No pond could be sized here: {pond['reason']}")

    # 5. Whether it holds water through the year.
    balance = pond.get("water_balance") or {}
    if balance.get("available"):
        months = balance.get("months_with_water")
        dry = balance.get("dry_month")
        if months == 12:
            sentences.append(
                "On mean monthly rainfall it holds usable water in all twelve "
                "months, losing "
                f"{_volume((balance.get('annual_losses_m3') or {}).get('evaporation'))} a year "
                "to evaporation and "
                f"{_volume((balance.get('annual_losses_m3') or {}).get('seepage'))} to seepage."
            )
        elif months is not None:
            sentences.append(
                f"On mean monthly rainfall it holds usable water for {months} of "
                f"twelve months, running low from {dry}."
            )

    # 6. Caveats, strongest first. These are the sentences a reader most needs.
    tier = str(environment.get("analysis_tier") or "")
    if TIER_SENTENCE.get(tier):
        caveats.append(TIER_SENTENCE[tier])

    quality = (site.get("catchment") or {}).get("quality") or {}
    if quality.get("touches_survey_edge"):
        caveats.append(
            "The catchment reaches the edge of the surveyed sheet, so its area — "
            "and every figure derived from it — is a lower bound rather than a "
            "measurement."
        )

    capture = None
    if design and runoff.get("available"):
        annual = (runoff.get("annual_mean") or {}).get("runoff_volume_m3")
        if annual:
            capture = 100.0 * float(design["gross_capacity_m3"]) / float(annual)
    if capture is not None and capture < LOW_CAPTURE_PCT:
        caveats.append(
            f"The pond holds only about {capture:.0f}% of the catchment's annual "
            "yield, so most runoff will pass downstream. That is normal, but it "
            "does mean the structure rather than the water is the limiting factor."
        )

    if runner_up_score is not None and score is not None:
        margin = float(score) - float(runner_up_score)
        if margin < NARROW_MARGIN:
            caveats.append(
                f"The margin over the next site is only {margin:.1f} points, which "
                "is inside the uncertainty of the inputs — treat the top two as "
                "comparable rather than ranked."
            )

    water = environment.get("water_exclusion") or {}
    if water.get("confidence") == "none":
        # Leads the caveats when it applies: a site that is already a tank is a
        # wrong answer, not a caveated one.
        caveats.insert(
            0,
            "Existing water bodies could not be excluded on this run — neither "
            "land cover nor OpenStreetMap answered. Terrain alone cannot "
            "distinguish a good pond site from a pond that is already there, "
            "since both are depressions where water collects. Check on imagery "
            "that this site is dry ground before acting on it.",
        )
    elif water.get("confidence") == "partial":
        caveats.append(
            f"Existing water bodies were excluded using {water['sources'][0]} "
            "only, which is not exhaustive — confirm the site is not an existing "
            "tank."
        )

    caveats.append(
        "Land tenure is not modelled: no open dataset carries village-level "
        "ownership, so this site may be privately held. Upload a cadastral layer "
        "to check it, or verify on the ground."
    )

    summary = " ".join(sentences)
    return Explanation(summary=summary, sentences=tuple(sentences), caveats=tuple(caveats))


def explain_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """Explanations for the recommended site and each runner-up."""
    sites = result.get("candidate_sites") or []
    if not sites:
        return {
            "available": False,
            "reason": "the analysis found no candidate sites, so there is nothing to explain",
        }
    environment = result.get("environment") or {}
    runner_up = sites[1].get("suitability_score") if len(sites) > 1 else None

    recommended = explain_site(
        sites[0], environment, rank_count=len(sites), runner_up_score=runner_up
    )
    return {
        "available": True,
        "recommended": recommended.as_dict(),
        "alternatives": [explain_site(site, environment).as_dict() for site in sites[1:]],
    }
