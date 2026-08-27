"""Flat detection and depression *breaching* (M3-2).

`hydrology.fill_depressions` raises every depression to its spill level. That is
the standard preparation for D8 routing and it is correct, but for this
application it is also destructive in a specific way: **a pond goes in a
depression, and filling removes the depression.** The measurements already work
around it -- slope and depression depth are taken on the original surface -- but
the *routing* still runs over a raised plateau, so flow accumulation spreads
across a filled hollow instead of converging into it and leaving by one channel.

Breaching is the alternative: rather than raising the pit to the barrier, lower a
channel through the barrier down to ground the water can reach. The pit keeps its
depth, the surface stays routable, and the accumulation converges the way water
actually would.

Neither is universally right, which is why both exist here:

* **Filling** is robust and always succeeds. It is the right choice where the
  obstruction is a genuine landform -- a closed basin with no outlet within reach.
* **Breaching** preserves the terrain and is the right choice where the
  obstruction is thin: a bund, a road embankment, a survey artefact where two
  contour lines nearly touched. Those are common in interpolated village terrain
  and they are exactly what a filled surface hides.

The choice is made per depression and bounded, so a basin that would need a
kilometre of carving is filled rather than trenched across the map. What was
breached and what was filled is reported, because it changes how the flow map
should be read.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.providers.elevation.base import DemGrid
from app.services.hydrology import NEIGHBOURS, ConditionedDem, fill_depressions

log = logging.getLogger(__name__)

#: A cell counts as flat when the steepest drop to any of its eight neighbours is
#: below this, in metres per metre. 0.001 is 0.1 % -- a tenth of the gentlest
#: slope a contour survey can resolve at 1 m intervals over 15 m spacing, so
#: anything below it is interpolation noise rather than gradient.
FLAT_GRADIENT = 0.001

#: Above this flat fraction, D8 routing over the *filled* surface is reporting
#: more about the fill than the terrain, and breaching is preferred where it can
#: be done. Below it, filling is the simpler and safer choice.
FLAT_FRACTION_PREFER_BREACH = 0.15

#: How deep a channel may be carved. Beyond this the "barrier" is a landform, not
#: an artefact, and trenching through it invents topography.
DEFAULT_MAX_BREACH_DEPTH_M = 2.0

#: How far a breach path may run, in cells. A long path is a trench across the
#: map rather than a cut through a bund.
DEFAULT_MAX_BREACH_LENGTH_CELLS = 40

#: Each carved cell is set this far below the previous one, so the channel
#: descends strictly and D8 has an unambiguous direction along it.
CARVE_EPSILON_M = 1e-3


@dataclass(frozen=True)
class FlatnessReport:
    """How much of a surface has no usable gradient."""

    flat_cells: int
    valid_cells: int
    flat_fraction: float
    gradient_threshold: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "flat_cells": self.flat_cells,
            "valid_cells": self.valid_cells,
            "flat_fraction": round(self.flat_fraction, 4),
            "flat_pct": round(100.0 * self.flat_fraction, 2),
            "gradient_threshold": self.gradient_threshold,
            "interpretation": _flatness_note(self.flat_fraction),
        }


def _flatness_note(fraction: float) -> str:
    if fraction < 0.05:
        return "well-drained terrain; D8 routing is unambiguous"
    if fraction < FLAT_FRACTION_PREFER_BREACH:
        return "some flat ground; routing is reliable but check the catchment shapes"
    if fraction < 0.4:
        return (
            "substantially flat; filled depressions dominate the flow field and "
            "breaching is preferred where the barriers are thin"
        )
    return (
        "predominantly flat: D8 flow directions here are largely an artefact of "
        "the conditioning rather than of the terrain, and catchment boundaries "
        "should be treated as indicative"
    )


@dataclass(frozen=True)
class BreachReport:
    """What the breaching pass did."""

    depressions_found: int
    depressions_breached: int
    depressions_filled: int
    cells_carved: int
    max_carve_depth_m: float
    total_carve_volume_m3: float
    max_breach_depth_m: float
    max_breach_length_cells: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "depressions_found": self.depressions_found,
            "depressions_breached": self.depressions_breached,
            "depressions_filled": self.depressions_filled,
            "cells_carved": self.cells_carved,
            "max_carve_depth_m": round(self.max_carve_depth_m, 3),
            "total_carve_volume_m3": round(self.total_carve_volume_m3, 1),
            "limits": {
                "max_breach_depth_m": self.max_breach_depth_m,
                "max_breach_length_cells": self.max_breach_length_cells,
            },
        }


def flatness(dem: DemGrid, *, gradient_threshold: float = FLAT_GRADIENT) -> FlatnessReport:
    """What fraction of the surface has no usable gradient.

    Measured as the steepest descent to any of the eight neighbours, divided by
    the distance to it -- so a diagonal neighbour is correctly further away. A
    cell with no lower neighbour at all counts as flat, which is right: it is
    either a pit or a plateau, and D8 has nothing to work with either way.
    """
    z = dem.elevation.astype(np.float64)
    valid = np.isfinite(z)
    if not valid.any():
        raise ValueError("DEM has no valid cells")

    steepest = np.zeros_like(z)
    for dr, dc, _code, diagonal in NEIGHBOURS:
        here, there = _aligned(z.shape, dr, dc)
        distance = dem.cell_size_m * (math.sqrt(2.0) if diagonal else 1.0)
        drop = np.full_like(z, -np.inf)
        drop[here] = (z[here] - z[there]) / distance
        np.maximum(steepest, np.where(np.isfinite(drop), drop, 0.0), out=steepest)

    flat = valid & (steepest < gradient_threshold)
    valid_count = int(valid.sum())
    flat_count = int(flat.sum())
    return FlatnessReport(
        flat_cells=flat_count,
        valid_cells=valid_count,
        flat_fraction=flat_count / valid_count if valid_count else 0.0,
        gradient_threshold=gradient_threshold,
    )


def _aligned(
    shape: tuple[int, int], dr: int, dc: int
) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
    """Aligned (here, there) slices, as `hydrology.neighbour_slices` computes.

    Imported rather than duplicated would be better, but that helper returns the
    pair for comparing a cell with its neighbour and is already exactly this --
    so it is used directly.
    """
    from app.services.hydrology import neighbour_slices

    return neighbour_slices(shape, dr, dc)


def breach_then_fill(
    dem: DemGrid,
    *,
    max_breach_depth_m: float = DEFAULT_MAX_BREACH_DEPTH_M,
    max_breach_length_cells: int = DEFAULT_MAX_BREACH_LENGTH_CELLS,
) -> tuple[ConditionedDem, BreachReport]:
    """Carve outlets for the depressions that can take one; fill the rest.

    The algorithm, per depression, is a least-cost search outward from its pit:

    1. Dijkstra from the pit, where the cost of stepping onto a cell is how far
       that cell would have to be *lowered* to keep the path descending. A step
       downhill costs nothing.
    2. Stop at the first cell already lower than the pit and outside the
       depression -- that is somewhere the water can go.
    3. If the path's total carve stays inside the depth and length limits, carve
       it. Otherwise leave the depression alone and let the fill pass take it.

    This is the bounded form of Lindsay's least-cost breaching (2016). The bounds
    are what keep it honest: an unbounded search will always find *some* path, and
    trenching four metres across half a survey to drain a closed basin invents
    topography rather than revealing it.

    The fill pass runs afterwards regardless, so the returned surface is always
    fully routable -- breaching reduces how much filling is needed, it does not
    replace it.
    """
    if max_breach_depth_m <= 0:
        raise ValueError(f"max breach depth must be positive, got {max_breach_depth_m}")
    if max_breach_length_cells < 1:
        raise ValueError(
            f"max breach length must be at least 1 cell, got {max_breach_length_cells}"
        )

    original = dem.elevation.astype(np.float64)
    valid = np.isfinite(original)

    # The fill pass is the cheapest way to find where the depressions are and how
    # deep each one is; the breach then tries to undo the ones it can.
    probe = fill_depressions(dem)
    depression = valid & (probe.fill_depth > 0)
    if not depression.any():
        return probe, BreachReport(
            depressions_found=0,
            depressions_breached=0,
            depressions_filled=0,
            cells_carved=0,
            max_carve_depth_m=0.0,
            total_carve_volume_m3=0.0,
            max_breach_depth_m=max_breach_depth_m,
            max_breach_length_cells=max_breach_length_cells,
        )

    labels, count = _label(depression)
    surface = original.copy()
    carved_total = 0
    breached = 0
    max_carve = 0.0
    carve_volume = 0.0
    cell_area = dem.cell_size_m * dem.cell_size_m

    for label in range(1, count + 1):
        region = labels == label
        pit = _lowest_cell(surface, region)
        path = _least_cost_path(
            surface,
            valid,
            region,
            pit,
            max_depth_m=max_breach_depth_m,
            max_length=max_breach_length_cells,
        )
        if path is None:
            continue

        # Plan the carve before committing to it. The search bounds the *cost*
        # of the path -- how far each cell sits above the pit -- but the carved
        # channel also descends by an epsilon per cell to keep D8 unambiguous, so
        # the realised cut is the cost plus that accumulated descent. On a
        # 28-cell path that took a 2.000 m budget to a 2.028 m cut: small, and
        # still a limit the caller was told would hold. Checked here instead.
        level = float(surface[pit])
        planned: list[tuple[tuple[int, int], float, float]] = []
        deepest = 0.0
        for cell in path:
            level -= CARVE_EPSILON_M
            cut = float(surface[cell]) - level
            if cut > 0:
                planned.append((cell, level, cut))
                deepest = max(deepest, cut)

        if deepest > max_breach_depth_m:
            continue

        for cell, new_level, cut in planned:
            carve_volume += cut * cell_area
            surface[cell] = new_level
        if planned:
            max_carve = max(max_carve, deepest)
            breached += 1
            carved_total += len(planned)

    # Whatever the breaching could not resolve still has to be routable.
    carved_dem = DemGrid(
        elevation=surface.astype(dem.elevation.dtype),
        transform=dem.transform,
        epsg=dem.epsg,
        cell_size_m=dem.cell_size_m,
    )
    conditioned = fill_depressions(carved_dem)
    remaining = int(((conditioned.fill_depth > 0) & valid).sum())
    log.info(
        "breach_then_fill",
        extra={
            "depressions": count,
            "breached": breached,
            "cells_carved": carved_total,
            "cells_still_filled": remaining,
        },
    )
    return conditioned, BreachReport(
        depressions_found=count,
        depressions_breached=breached,
        depressions_filled=count - breached,
        cells_carved=carved_total,
        max_carve_depth_m=max_carve,
        total_carve_volume_m3=carve_volume,
        max_breach_depth_m=max_breach_depth_m,
        max_breach_length_cells=max_breach_length_cells,
    )


def _label(mask: npt.NDArray[np.bool_]) -> tuple[npt.NDArray[np.int32], int]:
    """Connected components of a boolean mask, 8-connected."""
    from scipy import ndimage

    structure = np.ones((3, 3), dtype=bool)
    labels, count = ndimage.label(mask, structure=structure)
    return labels.astype(np.int32), int(count)


def _lowest_cell(
    surface: npt.NDArray[np.floating], region: npt.NDArray[np.bool_]
) -> tuple[int, int]:
    """The deepest cell of a region -- its pit."""
    masked = np.where(region, surface, np.inf)
    index = int(np.argmin(masked))
    return (index // surface.shape[1], index % surface.shape[1])


def _least_cost_path(
    surface: npt.NDArray[np.floating],
    valid: npt.NDArray[np.bool_],
    region: npt.NDArray[np.bool_],
    pit: tuple[int, int],
    *,
    max_depth_m: float,
    max_length: int,
) -> list[tuple[int, int]] | None:
    """Cheapest descending path from `pit` to somewhere the water can leave.

    Cost is the depth of cut a cell needs to stay below the running path level.
    Returns the cells to carve, excluding the pit itself, or None if no route
    exists inside the limits.
    """
    rows, cols = surface.shape
    pit_level = float(surface[pit])

    best: dict[tuple[int, int], float] = {pit: 0.0}
    previous: dict[tuple[int, int], tuple[int, int]] = {}
    # (cost so far, path length, cell) -- length breaks cost ties towards the
    # shorter cut, which is the one that looks less like a trench.
    queue: list[tuple[float, int, tuple[int, int]]] = [(0.0, 0, pit)]

    while queue:
        cost, length, cell = heapq.heappop(queue)
        if cost > best.get(cell, math.inf):
            continue
        if length >= max_length:
            continue

        for dr, dc, _code, _diagonal in NEIGHBOURS:
            nr, nc = cell[0] + dr, cell[1] + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            neighbour = (nr, nc)
            if not valid[neighbour]:
                # Nodata is where water leaves a survey, so reaching it is a
                # valid exit -- but there is nothing to carve into, so the path
                # ends at the cell before it.
                return _rebuild(previous, cell, pit)
            step_cost = max(0.0, float(surface[neighbour]) - pit_level)
            total = cost + step_cost
            if total > max_depth_m:
                continue
            if total >= best.get(neighbour, math.inf):
                continue

            best[neighbour] = total
            previous[neighbour] = cell

            # An exit: outside the depression and genuinely below the pit.
            if not region[neighbour] and surface[neighbour] < pit_level:
                return _rebuild(previous, neighbour, pit)

            heapq.heappush(queue, (total, length + 1, neighbour))

    return None


def _rebuild(
    previous: dict[tuple[int, int], tuple[int, int]],
    end: tuple[int, int],
    start: tuple[int, int],
) -> list[tuple[int, int]]:
    """The path from `start` to `end`, excluding `start`."""
    path: list[tuple[int, int]] = []
    cursor = end
    while cursor != start:
        path.append(cursor)
        cursor = previous.get(cursor, start)
    path.reverse()
    return path


def condition(
    dem: DemGrid,
    *,
    method: str = "auto",
    max_breach_depth_m: float = DEFAULT_MAX_BREACH_DEPTH_M,
    max_breach_length_cells: int = DEFAULT_MAX_BREACH_LENGTH_CELLS,
) -> tuple[ConditionedDem, dict[str, Any]]:
    """Prepare a DEM for routing, choosing how based on how flat it is.

    `method` is `fill`, `breach`, or `auto`. Auto breaches when more than
    `FLAT_FRACTION_PREFER_BREACH` of the surface has no usable gradient, because
    that is where the filled surface stops describing the terrain -- below it the
    simpler pass is also the safer one.

    Returns the conditioned DEM and a report of what was done, which belongs in
    the response: a catchment delineated over a heavily filled surface deserves
    less confidence than one over terrain that drained on its own.
    """
    flat = flatness(dem)
    if method not in ("auto", "fill", "breach"):
        raise ValueError(f"method must be auto, fill or breach; got {method!r}")

    chosen = method
    if method == "auto":
        chosen = "breach" if flat.flat_fraction > FLAT_FRACTION_PREFER_BREACH else "fill"

    if chosen == "fill":
        conditioned = fill_depressions(dem)
        report: dict[str, Any] = {"method": "fill", "method_chosen_by": method}
    else:
        conditioned, breach = breach_then_fill(
            dem,
            max_breach_depth_m=max_breach_depth_m,
            max_breach_length_cells=max_breach_length_cells,
        )
        report = {"method": "breach_then_fill", "method_chosen_by": method, **breach.as_dict()}

    report["flatness"] = flat.as_dict()
    report["cells_still_filled"] = conditioned.filled_cells
    report["max_fill_depth_m"] = round(conditioned.max_fill_depth_m, 3)
    return conditioned, report
