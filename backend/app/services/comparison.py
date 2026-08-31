"""Side-by-side comparison of candidate sites (FR-12, M10-6).

`GET /suitability/{job_id}/sites` already returns every site's numbers. Printing
them in two columns adds nothing. A comparison is worth having only if it answers
the question the reader is actually holding: **what do I give up by choosing
each one?**

So this reports three things the per-site payloads do not:

* **Who leads on each metric, and by how much.** With the direction stated,
  because "higher is better" is wrong for cost and for time of concentration.
* **Derived decision metrics.** Capture fraction (what share of the catchment's
  yield the pond can actually hold) and cost per m3 of live storage are what a
  block engineer decides on, and neither exists in a single site's payload.
* **The trade-off in words.** A site can lose on capacity and still be the right
  choice because it is limited by something you can fix.

One finding worth stating up front, because it changes how the table should be
read: **cost per cubic metre of gross capacity is identical across sites.** Cost
is excavated volume times a flat rate, so the ratio is a constant of the cost
model, not a property of the site. Reporting it as a differentiator would invite
a decision based on an artefact. Cost per m3 of *live* storage does vary, because
dead storage for silt is a fixed fraction of a differently-shaped pond.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Metric name -> (label, extractor, higher_is_better, unit)
Extractor = Callable[[dict[str, Any]], float | None]


def _catchment_ha(site: dict[str, Any]) -> float | None:
    return ((site.get("catchment") or {}).get("metrics") or {}).get("area_ha")


def _design(site: dict[str, Any]) -> dict[str, Any]:
    pond = site.get("pond") or {}
    return (pond.get("recommended") or {}) if pond.get("available") else {}


def _capacity(site: dict[str, Any]) -> float | None:
    return _design(site).get("gross_capacity_m3")


def _live_storage(site: dict[str, Any]) -> float | None:
    return _design(site).get("live_storage_m3")


def _cost(site: dict[str, Any]) -> float | None:
    return _design(site).get("estimated_cost_inr")


def _annual_runoff(site: dict[str, Any]) -> float | None:
    runoff = site.get("runoff") or {}
    if not runoff.get("available"):
        return None
    return (runoff.get("annual_mean") or {}).get("runoff_volume_m3")


def _capture_fraction_pct(site: dict[str, Any]) -> float | None:
    """What share of the catchment's annual yield the pond can hold.

    The metric that separates a well-matched pond from one dwarfed by its own
    catchment. A very low value is not a fault -- most of the yield is meant to
    pass downstream -- but it does say the structure is not the limiting factor
    on water, so building it bigger would help and building it elsewhere might
    help more.
    """
    capacity, runoff = _capacity(site), _annual_runoff(site)
    if not capacity or not runoff:
        return None
    return 100.0 * capacity / runoff


def _cost_per_live_m3(site: dict[str, Any]) -> float | None:
    cost, live = _cost(site), _live_storage(site)
    if not cost or not live:
        return None
    return cost / live


def _months_with_water(site: dict[str, Any]) -> float | None:
    balance = (site.get("pond") or {}).get("water_balance") or {}
    return balance.get("months_with_water") if balance.get("available") else None


#: The comparison table. Order is the order a reader scans in: how good, how big,
#: how much water, how much money, how reliable.
METRICS: tuple[tuple[str, str, Extractor, bool, str], ...] = (
    ("suitability_score", "Suitability score", lambda s: s.get("suitability_score"), True, "/100"),
    ("catchment_area_ha", "Catchment area", _catchment_ha, True, "ha"),
    ("annual_runoff_m3", "Annual runoff", _annual_runoff, True, "m³"),
    ("gross_capacity_m3", "Pond capacity", _capacity, True, "m³"),
    ("live_storage_m3", "Live storage", _live_storage, True, "m³"),
    ("capture_fraction_pct", "Share of yield held", _capture_fraction_pct, True, "%"),
    ("estimated_cost_inr", "Indicative cost", _cost, False, "₹"),
    ("cost_per_live_m3", "Cost per m³ of live storage", _cost_per_live_m3, False, "₹/m³"),
    ("months_with_water", "Months holding water", _months_with_water, True, "months"),
)

#: A difference smaller than this cannot inform a choice, so no site is credited
#: with leading on it. One per cent is generous: every input here carries more
#: uncertainty than that, and the cost figures are explicitly indicative.
NEGLIGIBLE_SPREAD_PCT = 1.0

#: What each binding constraint means for the decision — the actionable half.
CONSTRAINT_ADVICE: dict[str, str] = {
    "parcel_area": "limited by land, so acquiring adjacent plots would raise capacity",
    "practical_excavation_depth": "at the practical digging limit, so widening is the only way up",
    "sustainable_yield_share": (
        "already sized to its catchment's yield; a bigger pond would stand empty"
    ),
    "runoff_yield": "limited by water, not by land or depth",
    "budget": "stopped by the cost ceiling before any physical limit",
}


@dataclass(frozen=True)
class MetricRow:
    key: str
    label: str
    unit: str
    higher_is_better: bool
    values: dict[int, float | None]
    best_rank: int | None
    spread_pct: float | None
    #: True when every site has the same value, so the row cannot inform a choice.
    uniform: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.key,
            "label": self.label,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "values": {
                str(k): (None if v is None else round(v, 2)) for k, v in self.values.items()
            },
            "best_rank": self.best_rank,
            "spread_pct": None if self.spread_pct is None else round(self.spread_pct, 1),
            "uniform": self.uniform,
        }


def compare(sites: list[dict[str, Any]]) -> dict[str, Any]:
    """A decision-shaped comparison of 2 to 5 candidate sites."""
    if not 2 <= len(sites) <= 5:
        raise ValueError(f"compare between 2 and 5 sites; got {len(sites)}")
    ranks = [int(s.get("rank", i + 1)) for i, s in enumerate(sites)]
    if len(set(ranks)) != len(ranks):
        raise ValueError("the same site was given twice")

    rows: list[MetricRow] = []
    for key, label, extract, higher_better, unit in METRICS:
        values = {rank: extract(site) for rank, site in zip(ranks, sites, strict=True)}
        present = {r: v for r, v in values.items() if v is not None}
        best: int | None = None
        spread: float | None = None
        uniform = False
        if present:
            low, high = min(present.values()), max(present.values())
            if low > 0:
                spread = 100.0 * (high - low) / low
            # Relative, not absolute. Cost per m3 of live storage differs between
            # sites in the second decimal -- about 0.006 % -- because cost is
            # volume times a flat rate and only the dead-storage fraction moves
            # it. An absolute epsilon declared a winner on that, which is the
            # artefact this module's own notes warn against. Below the threshold
            # the row is reported as uniform and picks nobody.
            # Uniformity needs at least two measured values to mean anything.
            # With one -- the others missing a pond, say -- it is the only
            # candidate and wins by default; calling that "uniform" left the
            # single measured site with no winner at all.
            if len(present) >= 2:
                uniform = (high - low) < 1e-9 or (
                    spread is not None and spread < NEGLIGIBLE_SPREAD_PCT
                )
            if not uniform:
                best = (max if higher_better else min)(present, key=lambda r: present[r])
        rows.append(MetricRow(key, label, unit, higher_better, values, best, spread, uniform))

    wins: dict[int, int] = dict.fromkeys(ranks, 0)
    for row in rows:
        if row.best_rank is not None and not row.uniform:
            wins[row.best_rank] += 1

    return {
        "site_count": len(sites),
        "ranks": ranks,
        "metrics": [row.as_dict() for row in rows],
        "leads_on_count": {str(k): v for k, v in wins.items()},
        "trade_offs": [_trade_off(site, rows) for site in sites],
        "notes": _notes(rows),
    }


def _trade_off(site: dict[str, Any], rows: list[MetricRow]) -> dict[str, Any]:
    """One site's case, in the terms a decision is actually made in."""
    rank = int(site.get("rank", 0))
    pond = site.get("pond") or {}
    binding = pond.get("binding_constraint")
    leads = [r.label for r in rows if r.best_rank == rank and not r.uniform]
    trails = [
        r.label for r in rows if r.best_rank is not None and r.best_rank != rank and not r.uniform
    ]
    return {
        "rank": rank,
        "site_kind": site.get("site_kind"),
        "leads_on": leads,
        "behind_on": trails,
        "binding_constraint": binding,
        "what_that_means": CONSTRAINT_ADVICE.get(
            str(binding), "see the constraints evaluated in the full result"
        ),
    }


def _notes(rows: list[MetricRow]) -> list[str]:
    """Warnings about how to read the table, generated from the table itself."""
    notes: list[str] = []
    uniform = [r.label for r in rows if r.uniform and any(v is not None for v in r.values.values())]
    if uniform:
        notes.append(
            "Identical across every site, so these cannot inform a choice: "
            + ", ".join(uniform)
            + ". Cost per cubic metre of gross capacity is one of these by "
            "construction -- cost is excavated volume times a flat rate."
        )
    missing = [r.label for r in rows if all(v is None for v in r.values.values())]
    if missing:
        notes.append(
            "Not available for any site, usually because the analysis ran at a "
            "degraded tier: " + ", ".join(missing) + "."
        )
    notes.append(
        "Scores are comparable only within one analysis: they are normalised "
        "across the candidate set, so a 72 here does not mean the same as a 72 "
        "from a different run."
    )
    return notes
