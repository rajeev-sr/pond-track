"""Pond depth, dimensions and storage capacity (HLD §6.7, MC-18).

Two independent routes, reported together because they answer different
questions:

* **Stage-storage from the DEM** -- flood the terrain from the site cell at
  rising water levels and measure the area and volume actually impounded. This
  captures the real bowl shape, so a natural depression yields far more storage
  per cubic metre excavated than flat ground, and the curve shows it.
* **Prismoidal excavation geometry** -- what a constructed pond of a given plan
  size and side slope would hold. This is the number a bill of quantities uses.

Depth is then chosen by a bounded search over the stage-storage curve subject to
constraints that are each documented, and the response states *which constraint
bound the answer* -- which is the actionable part (HLD §6.9 Step 7).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from app.providers.elevation.base import DemGrid
from app.services.hydrology import NEIGHBOURS

#: Minimum depth for a pond to survive an Indian dry season. Evaporation scales
#: with *surface area*, so a deep, narrow pond retains water far longer than a
#: shallow, wide one of the same capacity (HLD CH-19).
MIN_DEPTH_M = 2.5
#: Practical excavation reach, and the point past which side slopes get unstable
#: in ordinary soils without engineered support.
MAX_DEPTH_M = 4.5
#: Side slope, horizontal per 1 vertical. 1:1.5 suits cohesive/clay soils.
DEFAULT_SIDE_SLOPE = 1.5
#: Freeboard above full supply level.
FREEBOARD_M = 0.5
#: Dead storage reserved for silt, as a fraction of gross capacity.
SILT_FRACTION = 0.10
#: Do not impound more than this share of the catchment's annual yield --
#: over-harvesting starves downstream users and existing tanks (HLD CH-18).
MAX_YIELD_FRACTION = 0.30
#: Indicative excavation rate, INR per cubic metre (MGNREGA earthwork order of
#: magnitude). Overridable; reported so the figure is never mistaken for a quote.
DEFAULT_EXCAVATION_RATE_INR = 130.0
DEFAULT_EMBANKMENT_RATE_INR = 70.0

STAGE_STEP_M = 0.25

#: Largest footprint considered for a single village pond, 2 ha. Not a physical
#: limit -- it is a scoping assumption. MGNREGA farm ponds and Amrit Sarovar
#: structures run from roughly 0.04 to 2 ha; without a cap, a candidate sitting in
#: a large tract of buildable land would be sized as a reservoir rather than a
#: pond. Reported as a constraint so the assumption is visible.
MAX_POND_FOOTPRINT_M2 = 20_000.0


@dataclass(frozen=True)
class StagePoint:
    depth_m: float
    water_level_m: float
    area_m2: float
    volume_m3: float
    #: True when the flood-fill reached the area cap instead of a rim, i.e. the
    #: water was not contained by terrain and the volume is not an impoundment.
    unbounded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "depth_m": round(self.depth_m, 2),
            "water_level_m": round(self.water_level_m, 2),
            "flooded_area_m2": round(self.area_m2, 1),
            "storage_volume_m3": round(self.volume_m3, 1),
            "unbounded": self.unbounded,
        }


@dataclass(frozen=True)
class PondDesign:
    depth_m: float
    freeboard_m: float
    side_slope_h_per_v: float
    top_length_m: float
    top_width_m: float
    bottom_length_m: float
    bottom_width_m: float
    top_area_m2: float
    bottom_area_m2: float
    gross_capacity_m3: float
    dead_storage_m3: float
    live_storage_m3: float
    excavation_volume_m3: float
    embankment_volume_m3: float
    estimated_cost_inr: float
    terrain_capacity_m3: float
    stage_storage: list[StagePoint]
    binding_constraint: str
    constraints_evaluated: dict[str, Any]
    inflow_m3: float | None = None
    fill_ratio: float | None = None
    recommendations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommended": {
                "depth_m": round(self.depth_m, 2),
                "freeboard_m": self.freeboard_m,
                "side_slope": f"1V : {self.side_slope_h_per_v:g}H",
                "top_length_m": round(self.top_length_m, 1),
                "top_width_m": round(self.top_width_m, 1),
                "bottom_length_m": round(self.bottom_length_m, 1),
                "bottom_width_m": round(self.bottom_width_m, 1),
                "top_area_m2": round(self.top_area_m2, 1),
                "bottom_area_m2": round(self.bottom_area_m2, 1),
                "gross_capacity_m3": round(self.gross_capacity_m3, 1),
                "dead_storage_silt_m3": round(self.dead_storage_m3, 1),
                "live_storage_m3": round(self.live_storage_m3, 1),
                "excavation_volume_m3": round(self.excavation_volume_m3, 1),
                "embankment_volume_m3": round(self.embankment_volume_m3, 1),
                "estimated_cost_inr": round(self.estimated_cost_inr, 0),
            },
            "terrain_derived_capacity_m3": round(self.terrain_capacity_m3, 1),
            "binding_constraint": self.binding_constraint,
            "constraints_evaluated": self.constraints_evaluated,
            "hydrological_check": (
                None
                if self.inflow_m3 is None
                else {
                    "annual_inflow_m3": round(self.inflow_m3, 0),
                    "capacity_to_inflow_ratio": round(self.fill_ratio or 0.0, 5),
                    "interpretation": _interpret_fill(self.fill_ratio or 0.0),
                }
            ),
            "stage_storage_curve": [p.as_dict() for p in self.stage_storage],
            "recommendations": list(self.recommendations),
            "warnings": list(self.warnings),
            "cost_basis": {
                "excavation_inr_per_m3": DEFAULT_EXCAVATION_RATE_INR,
                "embankment_inr_per_m3": DEFAULT_EMBANKMENT_RATE_INR,
                "note": "Indicative order of magnitude, not a tender estimate.",
            },
        }


def _interpret_fill(ratio: float) -> str:
    if ratio <= 0:
        return "no inflow estimate available"
    if ratio < 0.05:
        return (
            f"the pond holds {100 * ratio:.1f} % of the catchment's annual yield, so it "
            "will fill readily and repeatedly; the limit on size is land and "
            "excavation, not water"
        )
    if ratio < MAX_YIELD_FRACTION:
        return "capacity is well matched to the catchment's yield"
    return (
        "capacity approaches or exceeds the sustainable share of the catchment's "
        "yield; enlarging further risks starving downstream users"
    )


def stage_storage_curve(
    dem: DemGrid,
    row: int,
    col: int,
    *,
    max_depth_m: float = MAX_DEPTH_M,
    step_m: float = STAGE_STEP_M,
    max_area_m2: float = MAX_POND_FOOTPRINT_M2,
) -> list[StagePoint]:
    """Area and volume impounded at rising water levels above the site cell.

    Flood-fills from the site, so only terrain *connected* to it at that level is
    counted -- a separate hollow across a ridge is not part of this pond, however
    low it sits.

    The fill is capped by area, which matters more than it looks. On flat or
    gently sloping ground there is no rim to contain the water: an uncapped fill
    spreads across the whole survey and reports a "capacity" of the entire plain
    flooded to 3 m. That is not an impoundment, and a design built on the number
    would be nonsense.

    **The curve stops at the level where the cap is first reached**, and that
    last point is marked `unbounded`. Continuing past it produced a curve that
    fell as depth rose -- storage of 79,473 m3 at 2.00 m against 45,336 m3 at
    4.25 m on the Durg sheet -- which is impossible for a stage-storage curve and
    was visible as a descending line the moment it was plotted. The cause is that
    a capped fill sums whichever `max_cells` cells the traversal happened to pop
    first, and that subset is not nested across levels: at a higher level the
    frontier expands faster, so the truncated set reaches further out over
    shallower water. Below the cap no truncation happens, the flooded sets *are*
    nested, and volume rises with depth by construction.

    So there is no honest number to report past containment, and the curve says
    so by ending rather than by carrying a value nobody should read.
    """
    z = dem.elevation
    rows, cols = z.shape
    base = float(z[row, col])
    if not math.isfinite(base):
        raise ValueError(f"site cell ({row}, {col}) has no elevation")

    cell_area = dem.cell_size_m**2
    max_cells = max(1, int(max_area_m2 / cell_area))
    curve: list[StagePoint] = [StagePoint(0.0, base, 0.0, 0.0)]
    depth = step_m
    while depth <= max_depth_m + 1e-9:
        level = base + depth
        seen = np.zeros(z.shape, dtype=bool)
        seen[row, col] = True
        queue: deque[tuple[int, int]] = deque([(row, col)])
        volume = 0.0
        cells = 0
        hit_cap = False
        while queue:
            if cells >= max_cells:
                hit_cap = True
                break
            r, c = queue.popleft()
            zc = float(z[r, c])
            volume += (level - zc) * cell_area
            cells += 1
            for dr, dc, _code, _diag in NEIGHBOURS:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols) or seen[nr, nc]:
                    continue
                zn = z[nr, nc]
                if math.isfinite(zn) and float(zn) < level:
                    seen[nr, nc] = True
                    queue.append((nr, nc))
        curve.append(StagePoint(depth, level, cells * cell_area, volume, unbounded=hit_cap))
        if hit_cap:
            # Terrain stopped containing the water here. Every deeper level would
            # be a truncated fill, and those are not comparable to each other.
            break
        depth += step_m
    return curve


def usable_footprint_m2(
    feasible: npt.NDArray[np.bool_],
    row: int,
    col: int,
    cell_size_m: float,
    *,
    cap_m2: float = MAX_POND_FOOTPRINT_M2,
) -> tuple[float, bool]:
    """Contiguous buildable land around a site, in square metres.

    The candidate *region* from the siting step is a scoring cluster -- a handful
    of the highest-scoring cells -- not a measure of how much land is there. Using
    it as the pond footprint sizes a channel-position pond at a few hundred square
    metres regardless of the field it sits in. What bounds a pond is the connected
    patch of land that passed the feasibility masks, which is what this measures.

    Returns `(area_m2, was_capped)`.
    """
    rows, cols = feasible.shape
    if not (0 <= row < rows and 0 <= col < cols):
        raise ValueError(f"site ({row}, {col}) is outside the grid")
    cell_area = cell_size_m**2
    max_cells = int(cap_m2 / cell_area)

    if not feasible[row, col]:
        # The site cell itself can fall just outside the mask after snapping; a
        # single cell is then the honest answer rather than zero.
        return cell_area, False

    seen = np.zeros(feasible.shape, dtype=bool)
    seen[row, col] = True
    queue: deque[tuple[int, int]] = deque([(row, col)])
    count = 0
    while queue and count < max_cells:
        r, c = queue.popleft()
        count += 1
        for dr, dc, _code, _diag in NEIGHBOURS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not seen[nr, nc] and feasible[nr, nc]:
                seen[nr, nc] = True
                queue.append((nr, nc))
    return count * cell_area, bool(queue)


def prismoidal_volume(
    top_length_m: float, top_width_m: float, depth_m: float, side_slope: float
) -> tuple[float, float, float]:
    """Excavated volume of a truncated pyramid, plus its bottom dimensions.

    V = (d/3)(A_top + A_bottom + sqrt(A_top * A_bottom))

    Raises if the geometry cannot close: with side slope `z` a pit of depth `d`
    loses `2*z*d` from each plan dimension, so a deep pond needs a wide top.
    """
    lb = top_length_m - 2.0 * side_slope * depth_m
    wb = top_width_m - 2.0 * side_slope * depth_m
    if lb <= 0 or wb <= 0:
        raise ValueError(
            f"a {depth_m:g} m pond at 1V:{side_slope:g}H needs a top wider than "
            f"{2 * side_slope * depth_m:g} m; got {top_length_m:g} x {top_width_m:g} m"
        )
    at = top_length_m * top_width_m
    ab = lb * wb
    volume = (depth_m / 3.0) * (at + ab + math.sqrt(at * ab))
    return volume, lb, wb


def design_pond(
    dem: DemGrid,
    row: int,
    col: int,
    *,
    available_area_m2: float,
    annual_runoff_m3: float | None = None,
    min_depth_m: float = MIN_DEPTH_M,
    max_depth_m: float = MAX_DEPTH_M,
    side_slope: float = DEFAULT_SIDE_SLOPE,
    water_table_depth_m: float | None = None,
    budget_inr: float | None = None,
    excavation_rate_inr: float = DEFAULT_EXCAVATION_RATE_INR,
) -> PondDesign:
    """Choose a depth and plan size, and say which constraint bound the choice."""
    # Cap the fill at the land actually available: beyond that, spreading water
    # is not storage.
    curve = stage_storage_curve(
        dem,
        row,
        col,
        max_depth_m=max_depth_m,
        max_area_m2=min(available_area_m2 * 2.0, MAX_POND_FOOTPRINT_M2),
    )

    # Depth ceiling: whichever limit bites first, recorded by name.
    limits: dict[str, float] = {"practical_excavation_depth": max_depth_m}
    if water_table_depth_m is not None:
        # Cutting into a shallow water table turns a storage pond into a seepage
        # pit (HLD §6.7). Needs CGWB observation-well data, which is operator-
        # supplied here -- see the response note when it is absent.
        limits["water_table_clearance"] = max(0.0, water_table_depth_m - 1.0)
    if annual_runoff_m3:
        limits["sustainable_yield_share"] = max_depth_m  # refined below

    depth_cap = min(limits.values())

    # Plan size: a square footprint within the region's usable area, capped so the
    # geometry can close at the chosen depth.
    side = math.sqrt(max(available_area_m2, 1.0))
    geometric_cap = (side / (2.0 * side_slope)) * 0.98 if side > 0 else 0.0
    if geometric_cap < depth_cap:
        limits["plan_area_geometry"] = geometric_cap
        depth_cap = geometric_cap

    warnings: list[str] = []
    if depth_cap < min_depth_m:
        warnings.append(
            f"the depth ceiling ({depth_cap:.2f} m) is below the {min_depth_m:g} m "
            "minimum needed to survive the dry season; the site is too small or too "
            "constrained for a viable pond at this footprint"
        )
        depth = max(depth_cap, 0.5)
    else:
        depth = depth_cap

    # Budget, if given, can cut the depth further.
    if budget_inr:
        while depth > min_depth_m:
            v, _lb, _wb = prismoidal_volume(side, side, depth, side_slope)
            if v * excavation_rate_inr <= budget_inr:
                break
            depth -= 0.25
        limits["budget"] = depth

    volume, lb, wb = prismoidal_volume(side, side, depth, side_slope)
    at, ab = side * side, lb * wb

    # Yield cap: never impound more than a sustainable share of annual runoff.
    fill_ratio: float | None = None
    if annual_runoff_m3:
        cap = MAX_YIELD_FRACTION * annual_runoff_m3
        if volume > cap:
            scale = (cap / volume) ** (1.0 / 3.0)
            depth *= scale
            side *= scale
            volume, lb, wb = prismoidal_volume(side, side, depth, side_slope)
            at, ab = side * side, lb * wb
            limits["sustainable_yield_share"] = depth
        fill_ratio = volume / annual_runoff_m3

    binding = min(limits.items(), key=lambda kv: kv[1])[0]
    terrain_capacity = float(
        np.interp(depth, [p.depth_m for p in curve], [p.volume_m3 for p in curve])
    )
    if any(p.unbounded for p in curve if p.depth_m <= depth + 1e-9):
        warnings.append(
            "the terrain does not contain water at this depth -- the flood-fill "
            "spread to the area cap rather than reaching a rim, so the "
            "terrain-derived capacity is not an impoundment. Use the excavated "
            "(prismoidal) figure, which is what a constructed pond would hold."
        )
    embankment = 0.25 * volume
    cost = volume * excavation_rate_inr + embankment * DEFAULT_EMBANKMENT_RATE_INR

    recommendations: list[str] = []
    if water_table_depth_m is None:
        recommendations.append(
            "Depth is not constrained by groundwater here because no water-table "
            "measurement was supplied. Check the pre-monsoon level (CGWB observation "
            "wells) before excavating: cutting into a shallow table converts a "
            "storage pond into a seepage pit."
        )
    if depth >= min_depth_m:
        recommendations.append(
            f"Provide {FREEBOARD_M:g} m freeboard above full supply level and a "
            "surplus weir sized for the design storm."
        )
        recommendations.append(
            f"Reserve {SILT_FRACTION:.0%} of capacity as dead storage for silt and "
            "provide a silt trap at the inlet."
        )

    return PondDesign(
        depth_m=depth,
        freeboard_m=FREEBOARD_M,
        side_slope_h_per_v=side_slope,
        top_length_m=side,
        top_width_m=side,
        bottom_length_m=lb,
        bottom_width_m=wb,
        top_area_m2=at,
        bottom_area_m2=ab,
        gross_capacity_m3=volume,
        dead_storage_m3=SILT_FRACTION * volume,
        live_storage_m3=(1.0 - SILT_FRACTION) * volume,
        excavation_volume_m3=volume,
        embankment_volume_m3=embankment,
        estimated_cost_inr=cost,
        terrain_capacity_m3=terrain_capacity,
        stage_storage=curve,
        binding_constraint=binding,
        constraints_evaluated={k: round(v, 3) for k, v in limits.items()},
        inflow_m3=annual_runoff_m3,
        fill_ratio=fill_ratio,
        recommendations=recommendations,
        warnings=warnings,
    )
