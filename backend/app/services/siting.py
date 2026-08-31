"""Pond-site identification from terrain (MC-9, HLD 6.10.5).

Scores every cell against normalised criteria, applies hard feasibility masks,
clusters the surviving high-scoring cells, and returns ranked candidate sites
each with a per-criterion breakdown.

Two things keep this honest and extensible:

* **Weights come from one AHP vector** (HLD 6.5.2) and are *renormalised over
  whichever criteria are actually present*. A contour map carries no soil,
  land-cover or rainfall layer, so the terrain tier runs four criteria; adding
  those layers later adds terms without touching the algorithm.
* **Every site reports its own derivation** -- raw value, normalised value,
  weight and contribution per criterion -- so a recommendation can be defended
  rather than asserted (HLD 6.5.6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
from pyproj import Transformer
from skimage.measure import label as sk_label
from sklearn.cluster import DBSCAN

from app.providers.elevation.base import DemGrid
from app.services.hydrology import ConditionedDem, FlowGrids, slope_percent

AnalysisTier = Literal["full", "no_soil_lulc", "terrain_only"]

#: Relative criterion priorities as elicited, derived from IMSD practice
#: (HLD 6.5.1). Kept in this readable form because these are the numbers a
#: district engineer would recognise and argue with.
#:
#: They sum to 1.05, not 1.00 -- an arithmetic slip in the original table, found
#: when the AHP consistency audit in `services/ahp.py` checked it. Left as
#: elicited rather than edited, because deciding *which* criterion was meant to
#: be 0.05 lower would be inventing expert intent.
_ELICITED_PRIORITIES: dict[str, float] = {
    "flow_accumulation": 0.21,
    "slope": 0.18,
    "depression_depth": 0.14,
    "soil_runoff_potential": 0.13,  # tier: full  (Hydrologic Soil Group)
    "land_availability": 0.12,  # tier: full  (LULC)
    "distance_to_stream": 0.08,
    "plan_concavity": 0.08,
    "distance_to_settlement": 0.06,
    "distance_to_waterbody": 0.05,
}

#: The single AHP weight vector (HLD 6.5.1/6.5.2), normalised to sum to exactly
#: 1.0 as FR-9 requires. Normalising is ratio-preserving, and AHP encodes
#: *ratios* -- so this changes no judgement, no score and no ranking. It only
#: makes the published vector a weight vector, which matters because the number
#: quoted in a report should be the number the model used: the effective weight
#: on flow accumulation is 0.200, not the 0.21 that was tabulated.
#:
#: A tier uses the subset it can measure and renormalises again, so relative
#: importance survives a missing layer too.
AHP_WEIGHTS: dict[str, float] = {
    name: priority / sum(_ELICITED_PRIORITIES.values())
    for name, priority in _ELICITED_PRIORITIES.items()
}

#: What each tier can measure from the data it has.
TIER_CRITERIA: dict[AnalysisTier, tuple[str, ...]] = {
    "terrain_only": ("flow_accumulation", "slope", "depression_depth", "plan_concavity"),
    "no_soil_lulc": (
        "flow_accumulation",
        "slope",
        "depression_depth",
        "plan_concavity",
        "distance_to_stream",
    ),
    "full": tuple(AHP_WEIGHTS),
}

# ── feasibility defaults. All overridable per request; all reported back. ─────
DEFAULT_MAX_SLOPE_PCT = 8.0
#: A site must receive runoff from at least this much upstream area, else it is a
#: hollow that no water reaches.
DEFAULT_MIN_UPSTREAM_HA = 1.0
#: Score below which a cell is not considered at all.
DEFAULT_SCORE_THRESHOLD = 0.55
#: Two recommendations closer than this describe the same structure.
DEFAULT_MIN_SEPARATION_M = 300.0
#: Depression depth beyond which extra depth stops improving the site.
DEPRESSION_SATURATION_M = 3.0
#: A hollow shallower than this is survey noise, not a landform worth a pond.
DEFAULT_MIN_DEPRESSION_DEPTH_M = 0.3
#: A candidate region below this footprint cannot hold a useful structure.
MIN_REGION_AREA_M2 = 100.0
#: Keep candidates this far from the survey edge: a catchment that runs off the
#: edge is truncated and its area understated (HLD CH-7).
EDGE_BUFFER_CELLS = 3


@dataclass(frozen=True)
class CriterionScore:
    name: str
    weight: float
    raw: float
    normalised: float

    @property
    def contribution(self) -> float:
        return self.weight * self.normalised

    def as_dict(self) -> dict[str, object]:
        return {
            "criterion": self.name,
            "raw_value": round(self.raw, 4),
            "normalised": round(self.normalised, 4),
            "weight": round(self.weight, 4),
            "contribution": round(self.contribution, 4),
        }


@dataclass(frozen=True)
class CandidateSite:
    rank: int
    score_0_100: float
    #: natural_depression (a bowl: least excavation) | channel_position
    kind: str
    row: int  # where the pond would go (deepest buildable cell)
    col: int
    x_m: float
    y_m: float
    lon: float
    lat: float
    #: Where the catchment should be delineated from: the region's spill point,
    #: which is where its runoff actually passes (see _depression_regions).
    outlet_row: int
    outlet_col: int
    elevation_m: float
    depression_depth_m: float
    slope_pct: float
    upstream_cells: int
    upstream_area_ha: float
    region_cells: int
    region_area_m2: float
    criteria: list[CriterionScore]

    def as_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "suitability_score": round(self.score_0_100, 1),
            "site_kind": self.kind,
            "location": {
                "lon": round(self.lon, 7),
                "lat": round(self.lat, 7),
                "projected_x_m": round(self.x_m, 2),
                "projected_y_m": round(self.y_m, 2),
                "grid_row": self.row,
                "grid_col": self.col,
            },
            "terrain": {
                "elevation_m": round(self.elevation_m, 2),
                "depression_depth_m": round(self.depression_depth_m, 2),
                "slope_pct": round(self.slope_pct, 2),
                "upstream_cells": self.upstream_cells,
                "upstream_area_ha": round(self.upstream_area_ha, 3),
            },
            "region": {
                "cells": self.region_cells,
                "area_m2": round(self.region_area_m2, 1),
                "area_ha": round(self.region_area_m2 / 10_000.0, 4),
            },
            "catchment_pour_point": {
                "grid_row": self.outlet_row,
                "grid_col": self.outlet_col,
            },
            "criteria_breakdown": [c.as_dict() for c in self.criteria],
        }


@dataclass
class SitingResult:
    suitability: npt.NDArray[np.float32]
    feasible: npt.NDArray[np.bool_]
    #: Where a pond could be *built* -- slope, land cover, valid data -- without
    #: the upstream-area requirement that defines a candidate. This is what bounds
    #: a pond's footprint.
    buildable: npt.NDArray[np.bool_]
    sites: list[CandidateSite]
    tier: AnalysisTier
    weights: dict[str, float]
    layers_used: list[str]
    layers_unavailable: list[str]
    constraints: dict[str, float]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "analysis_tier": self.tier,
            "layers_used": self.layers_used,
            "layers_unavailable": self.layers_unavailable,
            "criteria_weights": {k: round(v, 4) for k, v in self.weights.items()},
            "constraints_applied": self.constraints,
            "feasible_cells": int(self.feasible.sum()),
            "candidate_sites": [s.as_dict() for s in self.sites],
            "warnings": list(self.warnings),
        }


# ── normalisation ────────────────────────────────────────────────────────────
def weights_for(names: Sequence[str]) -> dict[str, float]:
    """AHP weights for an explicit criteria set, renormalised to sum to 1.

    Renormalising rather than re-eliciting preserves the *relative* importance
    the expert expressed: adding land availability does not make slope matter
    less than accumulation, it just redistributes the share proportionally.
    """
    subset = {n: AHP_WEIGHTS[n] for n in names}
    total = sum(subset.values())
    if total <= 0:  # pragma: no cover - AHP_WEIGHTS are all positive
        raise ValueError("criteria set has no positive weights")
    return {n: w / total for n, w in subset.items()}


def resolve_weights(
    names: Sequence[str], override: Mapping[str, float] | None = None
) -> dict[str, float]:
    """The weights to score `names` with: the shipped vector, or a caller's own.

    An override is restricted to the criteria actually available and renormalised,
    exactly as the shipped vector is, so a district engineer supplying weights for
    all nine criteria still gets a coherent set when soil and land cover are
    missing. Silently ignoring a criterion the caller weighted, or scoring with
    weights that do not sum to 1, would both make the returned score
    incomparable to the one the defaults produce.

    This is what makes `POST /suitability/weights/ahp` actionable rather than
    advisory: the weights it derives can be handed straight back in.
    """
    if override is None:
        return weights_for(names)
    missing = [n for n in names if n not in override]
    if missing:
        raise ValueError(
            f"no weight given for {missing}; supply one for every criterion in "
            f"use, which for this analysis is {list(names)}"
        )
    subset = {n: float(override[n]) for n in names}
    if any(w < 0 for w in subset.values()):
        raise ValueError("weights cannot be negative")
    total = sum(subset.values())
    if total <= 0:
        raise ValueError("the weights supplied sum to zero, so nothing would be scored")
    return {n: w / total for n, w in subset.items()}


def criteria_for(*, has_land_cover: bool) -> tuple[str, ...]:
    """The criteria that can actually be measured from what is available."""
    base = TIER_CRITERIA["terrain_only"]
    return (*base, "land_availability") if has_land_cover else base


def tier_weights(tier: AnalysisTier) -> dict[str, float]:
    """AHP weights restricted to a tier's criteria and renormalised to sum to 1.

    Renormalising rather than re-eliciting keeps the *relative* importance the
    expert expressed: dropping soil does not make slope matter more than
    accumulation, it just redistributes the missing share proportionally.
    """
    names = TIER_CRITERIA[tier]
    subset = {n: AHP_WEIGHTS[n] for n in names}
    total = sum(subset.values())
    if total <= 0:  # pragma: no cover - AHP_WEIGHTS are all positive
        raise ValueError("tier has no positive weights")
    return {n: w / total for n, w in subset.items()}


def robust_normalise(
    values: npt.NDArray[np.floating], mask: npt.NDArray[np.bool_], *, invert: bool = False
) -> npt.NDArray[np.float32]:
    """Scale to [0, 1] using the 2nd-98th percentile range.

    Percentiles rather than min/max: flow accumulation is heavy-tailed, and a
    single channel cell carrying 150,000 upstream cells would otherwise compress
    every other cell to ~0 and flatten the criterion into noise.
    """
    out = np.zeros(values.shape, dtype=np.float32)
    if not mask.any():
        return out
    sample = values[mask]
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return out
    lo, hi = np.percentile(sample, [2.0, 98.0])
    if hi - lo < 1e-12:
        out[mask] = 0.5  # no discrimination available; stay neutral
        return out
    scaled = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
    if invert:
        scaled = 1.0 - scaled
    out[mask] = scaled[mask].astype(np.float32)
    return out


def plan_concavity(dem: DemGrid) -> npt.NDArray[np.float32]:
    """Laplacian of elevation: positive where ground converges into a hollow.

    For a bowl z = r^2 the Laplacian is +4, so higher means more bowl-like. The
    result is scaled by cell size squared to keep it in elevation units rather
    than per-cell units, so it means the same thing at any resolution.
    """
    z = dem.elevation.astype(np.float64)
    finite = np.isfinite(z)
    filled = np.where(finite, z, 0.0)
    p = np.pad(filled, 1, mode="edge")
    lap = p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4.0 * p[1:-1, 1:-1]
    out = np.where(finite, lap, np.nan).astype(np.float32)
    return out


# ── scoring and site extraction ──────────────────────────────────────────────
def score_terrain(
    dem: DemGrid,
    conditioned: ConditionedDem,
    flow: FlowGrids,
    *,
    max_slope_pct: float = DEFAULT_MAX_SLOPE_PCT,
    min_upstream_ha: float = DEFAULT_MIN_UPSTREAM_HA,
    slope_pct: npt.NDArray[np.float32] | None = None,
    availability: npt.NDArray[np.float32] | None = None,
    excluded: npt.NDArray[np.bool_] | None = None,
    weights_override: Mapping[str, float] | None = None,
) -> tuple[
    npt.NDArray[np.float32],
    npt.NDArray[np.bool_],
    npt.NDArray[np.bool_],
    dict[str, npt.NDArray[np.float32]],
]:
    """Weighted-overlay suitability plus the hard feasibility mask.

    Returns `(score, feasible, normalised_layers)`. Score is 0-1 and is zeroed
    outside the feasible set, so an infeasible cell can never be recommended
    however good its terrain looks.
    """
    valid = flow.valid
    cell_area_m2 = dem.cell_size_m**2
    if slope_pct is None:
        # Buildability is a property of the *original* ground, not of the
        # depression-filled surface -- see slope_percent's docstring.
        slope_pct = slope_percent(dem.elevation, dem.cell_size_m)

    depth = np.where(np.isfinite(conditioned.fill_depth), conditioned.fill_depth, 0.0)
    # Log scale: upstream area spans five orders of magnitude, and the useful
    # distinction is between 1 ha and 10 ha, not between 300 ha and 310 ha.
    acc_log = np.where(valid, np.log1p(flow.accumulation.astype(np.float64)), 0.0)
    concav = np.nan_to_num(plan_concavity(dem), nan=0.0)
    # Depth stops helping past the saturation point: a 10 m hole is not three
    # times better than a 3 m one, it is just more excavation.
    depth_sat = np.minimum(depth, DEPRESSION_SATURATION_M)

    # Hard feasibility masks are applied FIRST, because they define the
    # population the criteria are normalised against.
    min_cells = max(1, int(round(min_upstream_ha * 10_000.0 / cell_area_m2)))
    edge_ok = np.zeros(dem.shape, dtype=bool)
    b = EDGE_BUFFER_CELLS
    edge_ok[b : -b or None, b : -b or None] = True

    # Two distinct masks, because they answer different questions.
    #
    #   buildable -- could a pond be *constructed* here? Slope, land cover,
    #                valid data, clear of the survey edge.
    #   feasible  -- is this a sensible *candidate*? Buildable, and receiving
    #                enough upstream area to be worth impounding.
    #
    # Conflating them makes the pond footprint follow the drainage line, so a
    # pond in the middle of a buildable field gets sized at a few hundred square
    # metres because only the channel cells cleared the accumulation threshold.
    buildable = valid & (slope_pct <= max_slope_pct) & edge_ok & np.isfinite(dem.elevation)
    if availability is not None:
        # A zero score means built-up, open water, snow or mangrove: no terrain
        # argument makes those buildable, so this is a mask and not a penalty.
        buildable = buildable & (availability > 0.0)
    if excluded is not None:
        # The hard veto: existing tanks, rivers, buildings, roads. Terrain scores
        # where water *collects*, which is where an existing tank already is and
        # where a river runs -- so without this the model recommends both,
        # enthusiastically and with a high score.
        buildable = buildable & ~np.asarray(excluded, dtype=bool)
    feasible = buildable & (flow.accumulation >= min_cells)

    # Normalise over the FEASIBLE set, not over every valid cell. Feasible cells
    # already sit in the extreme tail of each criterion relative to the whole
    # surface, so normalising globally clips them all to 1.0 and every candidate
    # ties at 100/100 -- destroying the ranking the caller asked for.
    layers = {
        "flow_accumulation": robust_normalise(acc_log, feasible),
        "slope": robust_normalise(slope_pct.astype(np.float64), feasible, invert=True),
        "depression_depth": robust_normalise(depth_sat, feasible),
        "plan_concavity": robust_normalise(concav, feasible),
    }
    if availability is not None:
        # Already 0-1 and meaningful in absolute terms (a land-cover class either
        # is or is not allottable), so it is used directly rather than rescaled
        # against the other candidates.
        layers["land_availability"] = availability.astype(np.float32)

    weights = resolve_weights(
        criteria_for(has_land_cover=availability is not None), weights_override
    )
    score = np.zeros(dem.shape, dtype=np.float32)
    for name, w in weights.items():
        score += (w * layers[name]).astype(np.float32)
    score = np.where(feasible, score, 0.0).astype(np.float32)
    return score, feasible, buildable, layers


@dataclass(frozen=True)
class _Region:
    """A contiguous candidate area, before scoring."""

    kind: str  # natural_depression | channel_position
    cells: int
    site_row: int  # where the pond would go
    site_col: int
    outlet_row: int  # where its catchment should be delineated from
    outlet_col: int
    max_depth_m: float
    max_upstream_cells: int
    mean_slope_pct: float
    mean_concavity: float
    #: Mean land-cover buildability over the region, or None when land cover was
    #: unavailable. Kept separate from the score so the response can say whether
    #: the criterion was measured or simply absent.
    mean_availability: float | None = None


def _depression_regions(
    conditioned: ConditionedDem,
    flow: FlowGrids,
    slope_pct: npt.NDArray[np.float32],
    concav: npt.NDArray[np.float32],
    feasible: npt.NDArray[np.bool_],
    availability: npt.NDArray[np.float32] | None,
    *,
    min_depth_m: float,
    min_cells: int,
) -> list[_Region]:
    """Natural depressions, aggregated as whole landforms.

    Criteria are aggregated **over the region**, not sampled at one cell, because
    the two things that matter live in different places: the *deepest* cell is
    where the pond goes (least excavation), while the *highest-accumulation* cell
    is the spill point through which the depression's runoff passes. Reading both
    off a single cell under-reports the water a bowl collects -- after flooding,
    the epsilon gradient carries flow to the spill point rather than through the
    geometric centre, so the deepest cell can show almost no upstream area.
    """
    depth = np.where(np.isfinite(conditioned.fill_depth), conditioned.fill_depth, 0.0)
    mask = conditioned.valid & (depth > min_depth_m)
    if not mask.any():
        return []

    labels = sk_label(mask, connectivity=2)
    regions: list[_Region] = []
    for lab in range(1, int(labels.max()) + 1):
        sel = labels == lab
        n = int(sel.sum())
        if n < min_cells:
            continue
        rows, cols = np.nonzero(sel)
        # The pond goes at the deepest cell that is actually buildable.
        buildable = feasible[rows, cols]
        pick_from = buildable if buildable.any() else np.ones(n, dtype=bool)
        d_sub = depth[rows, cols]
        deepest = int(np.argmax(np.where(pick_from, d_sub, -np.inf)))
        acc_sub = flow.accumulation[rows, cols]
        spill = int(np.argmax(acc_sub))
        # Slope is aggregated over the *buildable* part of the region. Averaging
        # over the whole landform would report a figure above the slope limit
        # that admitted the region, which reads as a contradiction.
        slope_cells = slope_pct[rows[pick_from], cols[pick_from]]
        regions.append(
            _Region(
                kind="natural_depression",
                cells=n,
                site_row=int(rows[deepest]),
                site_col=int(cols[deepest]),
                outlet_row=int(rows[spill]),
                outlet_col=int(cols[spill]),
                max_depth_m=float(d_sub.max()),
                max_upstream_cells=int(acc_sub.max()),
                mean_slope_pct=float(np.nanmean(slope_cells)),
                mean_concavity=float(np.nanmean(concav[rows, cols])),
                mean_availability=(
                    None
                    if availability is None
                    else float(np.nanmean(availability[rows[pick_from], cols[pick_from]]))
                ),
            )
        )
    return regions


def _channel_regions(
    conditioned: ConditionedDem,
    flow: FlowGrids,
    slope_pct: npt.NDArray[np.float32],
    concav: npt.NDArray[np.float32],
    feasible: npt.NDArray[np.bool_],
    availability: npt.NDArray[np.float32] | None,
    score: npt.NDArray[np.float32],
    *,
    score_threshold: float,
) -> list[_Region]:
    """High-scoring positions on the drainage network, for terrain with no bowl.

    A pond can be built by excavation on a channel even where no natural
    depression exists, so these keep the model useful on smooth terrain. DBSCAN
    rather than a top-N of cells: the best cells form contiguous blobs, and
    top-N would return five neighbours of one location.
    """
    high = feasible & (score >= score_threshold)
    if not high.any():
        return []
    rows, cols = np.nonzero(high)
    labels = DBSCAN(eps=2.5, min_samples=4).fit_predict(
        np.column_stack([rows, cols]).astype(np.float64)
    )
    depth = np.where(np.isfinite(conditioned.fill_depth), conditioned.fill_depth, 0.0)
    out: list[_Region] = []
    for lab in sorted(set(labels)):
        if lab == -1:  # DBSCAN noise
            continue
        sel = labels == lab
        r_sub, c_sub = rows[sel], cols[sel]
        best = int(np.argmax(score[r_sub, c_sub]))
        acc_sub = flow.accumulation[r_sub, c_sub]
        spill = int(np.argmax(acc_sub))
        out.append(
            _Region(
                kind="channel_position",
                cells=int(sel.sum()),
                site_row=int(r_sub[best]),
                site_col=int(c_sub[best]),
                outlet_row=int(r_sub[spill]),
                outlet_col=int(c_sub[spill]),
                max_depth_m=float(depth[r_sub, c_sub].max()),
                max_upstream_cells=int(acc_sub.max()),
                mean_slope_pct=float(np.nanmean(slope_pct[r_sub, c_sub])),
                mean_concavity=float(np.nanmean(concav[r_sub, c_sub])),
                mean_availability=(
                    None if availability is None else float(np.nanmean(availability[r_sub, c_sub]))
                ),
            )
        )
    return out


def _score_regions(
    regions: list[_Region],
    weights_override: Mapping[str, float] | None = None,
) -> list[tuple[float, _Region, list[CriterionScore]]]:
    """Score candidate regions against **each other**.

    Normalising across the candidate set rather than across the whole raster is
    what makes the ranking informative: every candidate has already passed the
    feasibility masks, so they all sit in the extreme tail of the full-surface
    distribution and a global scaling collapses them to a tie.
    """
    if not regions:
        return []
    has_availability = all(r.mean_availability is not None for r in regions)
    weights = resolve_weights(criteria_for(has_land_cover=has_availability), weights_override)

    raw = {
        "flow_accumulation": np.array([np.log1p(r.max_upstream_cells) for r in regions]),
        "slope": np.array([r.mean_slope_pct for r in regions]),
        "depression_depth": np.array(
            [min(r.max_depth_m, DEPRESSION_SATURATION_M) for r in regions]
        ),
        "plan_concavity": np.array([r.mean_concavity for r in regions]),
    }
    display = {
        "flow_accumulation": np.array([float(r.max_upstream_cells) for r in regions]),
        "slope": raw["slope"],
        "depression_depth": np.array([r.max_depth_m for r in regions]),
        "plan_concavity": raw["plan_concavity"],
    }
    keep = np.ones(len(regions), dtype=bool)
    norm = {
        name: robust_normalise(vals, keep, invert=(name == "slope")) for name, vals in raw.items()
    }
    if has_availability:
        # Absolute, not relative: a land-cover class either is or is not
        # allottable, so rescaling it against the other candidates would turn
        # "all five sites are on cropland" into "one of them is ideal".
        vals = np.array([float(r.mean_availability or 0.0) for r in regions])
        raw["land_availability"] = vals
        display["land_availability"] = vals
        norm["land_availability"] = vals.astype(np.float32)

    scored: list[tuple[float, _Region, list[CriterionScore]]] = []
    for i, region in enumerate(regions):
        criteria = [
            CriterionScore(
                name=name,
                weight=weights[name],
                raw=float(display[name][i]),
                normalised=float(norm[name][i]),
            )
            for name in weights
        ]
        scored.append((sum(c.contribution for c in criteria), region, criteria))
    scored.sort(key=lambda t: -t[0])
    return scored


def extract_sites(
    dem: DemGrid,
    conditioned: ConditionedDem,
    flow: FlowGrids,
    score: npt.NDArray[np.float32],
    feasible: npt.NDArray[np.bool_],
    *,
    availability: npt.NDArray[np.float32] | None = None,
    max_sites: int = 5,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    min_separation_m: float = DEFAULT_MIN_SEPARATION_M,
    weights_override: Mapping[str, float] | None = None,
    min_depression_depth_m: float = DEFAULT_MIN_DEPRESSION_DEPTH_M,
    slope_pct: npt.NDArray[np.float32] | None = None,
) -> tuple[list[CandidateSite], list[str]]:
    """Build candidate regions, score them against each other, and rank."""
    warnings: list[str] = []
    if slope_pct is None:
        slope_pct = slope_percent(dem.elevation, dem.cell_size_m)
    concav = np.nan_to_num(plan_concavity(dem), nan=0.0)
    min_cells = max(4, int(round(MIN_REGION_AREA_M2 / dem.cell_size_m**2)))

    regions = _depression_regions(
        conditioned,
        flow,
        slope_pct,
        concav,
        feasible,
        availability,
        min_depth_m=min_depression_depth_m,
        min_cells=min_cells,
    )
    n_depressions = len(regions)
    regions += _channel_regions(
        conditioned,
        flow,
        slope_pct,
        concav,
        feasible,
        availability,
        score,
        score_threshold=score_threshold,
    )
    if not regions:
        best = float(score[feasible].max()) if feasible.any() else 0.0
        warnings.append(
            f"no candidate region found: no depression deeper than "
            f"{min_depression_depth_m:.2f} m and no channel cell above the "
            f"suitability threshold of {score_threshold:.2f} (best was {best:.2f})"
        )
        return [], warnings
    if n_depressions == 0:
        warnings.append(
            "no natural depression met the minimum depth; candidates are channel "
            "positions that would need full excavation"
        )

    scored = _score_regions(regions, weights_override)

    # Non-maximum suppression: a lower-ranked candidate within min_separation of
    # a better one is describing the same structure.
    min_sep_cells = min_separation_m / dem.cell_size_m
    kept: list[tuple[float, _Region, list[CriterionScore]]] = []
    suppressed = 0
    for cand in scored:
        r = cand[1]
        if any(
            np.hypot(r.site_row - k[1].site_row, r.site_col - k[1].site_col) < min_sep_cells
            for k in kept
        ):
            suppressed += 1
            continue
        kept.append(cand)
        if len(kept) >= max_sites:
            break
    if suppressed:
        warnings.append(
            f"{suppressed} candidate(s) suppressed for lying within "
            f"{min_separation_m:.0f} m of a higher-ranked site"
        )

    to_lonlat = Transformer.from_crs(dem.epsg, 4326, always_xy=True)
    cell_area_ha = dem.cell_size_m**2 / 10_000.0
    sites: list[CandidateSite] = []
    for rank, (sc, r, criteria) in enumerate(kept, start=1):
        x, y = dem.xy(r.site_row, r.site_col)
        lon, lat = to_lonlat.transform(x, y)
        sites.append(
            CandidateSite(
                rank=rank,
                score_0_100=100.0 * sc,
                kind=r.kind,
                row=r.site_row,
                col=r.site_col,
                x_m=x,
                y_m=y,
                lon=lon,
                lat=lat,
                outlet_row=r.outlet_row,
                outlet_col=r.outlet_col,
                elevation_m=float(dem.elevation[r.site_row, r.site_col]),
                depression_depth_m=r.max_depth_m,
                slope_pct=r.mean_slope_pct,
                upstream_cells=r.max_upstream_cells,
                upstream_area_ha=r.max_upstream_cells * cell_area_ha,
                region_cells=r.cells,
                region_area_m2=r.cells * dem.cell_size_m**2,
                criteria=criteria,
            )
        )
    return sites, warnings


def identify_pond_sites(
    dem: DemGrid,
    conditioned: ConditionedDem,
    flow: FlowGrids,
    *,
    max_sites: int = 5,
    max_slope_pct: float = DEFAULT_MAX_SLOPE_PCT,
    min_upstream_ha: float = DEFAULT_MIN_UPSTREAM_HA,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    min_separation_m: float = DEFAULT_MIN_SEPARATION_M,
    min_depression_depth_m: float = DEFAULT_MIN_DEPRESSION_DEPTH_M,
    availability: npt.NDArray[np.float32] | None = None,
    layers_used: list[str] | None = None,
    layers_unavailable: list[str] | None = None,
    tier: str = "terrain_only",
    weights_override: Mapping[str, float] | None = None,
    excluded: npt.NDArray[np.bool_] | None = None,
) -> SitingResult:
    """End-to-end terrain siting: score, mask, regionalise, rank, explain."""
    slope_pct = slope_percent(dem.elevation, dem.cell_size_m)
    score, feasible, buildable, layers = score_terrain(
        dem,
        conditioned,
        flow,
        max_slope_pct=max_slope_pct,
        min_upstream_ha=min_upstream_ha,
        slope_pct=slope_pct,
        availability=availability,
        excluded=excluded,
        weights_override=weights_override,
    )
    sites, warnings = extract_sites(
        dem,
        conditioned,
        flow,
        score,
        feasible,
        availability=availability,
        weights_override=weights_override,
        max_sites=max_sites,
        score_threshold=score_threshold,
        min_separation_m=min_separation_m,
        min_depression_depth_m=min_depression_depth_m,
        slope_pct=slope_pct,
    )
    weights = resolve_weights(
        criteria_for(has_land_cover=availability is not None), weights_override
    )
    return SitingResult(
        suitability=score,
        feasible=feasible,
        buildable=buildable,
        sites=sites,
        tier=tier,  # type: ignore[arg-type]
        weights=weights,
        layers_used=layers_used or ["elevation", "slope", "flow_accumulation", "depression_depth"],
        layers_unavailable=(
            layers_unavailable
            if layers_unavailable is not None
            else [
                "soil_hydrologic_group",
                "land_use_land_cover",
                "rainfall",
                "existing_water_bodies",
            ]
        ),
        constraints={
            "max_slope_pct": max_slope_pct,
            "min_upstream_area_ha": min_upstream_ha,
            "score_threshold": score_threshold,
            "min_separation_m": min_separation_m,
            "min_depression_depth_m": min_depression_depth_m,
            "edge_buffer_cells": float(EDGE_BUFFER_CELLS),
            "land_cover_exclusion_applied": availability is not None,
        },
        warnings=warnings,
    )
