"""Stream network extraction and Strahler ordering (M3-3, and M3-4's last metric).

A catchment outline says how much land drains to a point. It does not say *where*
the water goes on the way -- and for pond siting that is the more useful picture:
a site on a first-order headwater collects from a few hectares, the same site on a
third-order channel collects from hundreds and needs a spillway sized for it.

Three steps:

1. **Threshold.** A cell is a stream cell when the area draining through it
   exceeds a threshold. This is the only free parameter and it decides everything
   downstream, so it is expressed in hectares of contributing area rather than in
   cells -- the same threshold then means the same thing at 5 m and at 30 m.
2. **Vectorise.** Walk each stream cell downstream along the D8 grid, cutting a
   line at every confluence, so one line is one reach between junctions rather
   than an arbitrary polyline.
3. **Order.** Strahler: a reach with no stream feeding it is order 1; where two
   reaches of equal order *n* meet the result is *n+1*; where unequal orders meet
   the result is the greater. It is the standard measure of a channel's position
   in a network, and it is what makes "first-order headwater" a statement rather
   than an impression.

Drainage density -- total channel length over catchment area -- falls out of the
same network, and completes the morphometry in `hydrology.catchment_metrics`.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.services.hydrology import NEIGHBOURS, Catchment, FlowGrids

log = logging.getLogger(__name__)

#: Default contributing area at which a channel is considered to begin.
#:
#: 1 ha is deliberately small for Indian village terrain: a nala draining a few
#: hectares is exactly the feature a check dam or farm pond sits on, and a
#: threshold tuned for a mountain basin would erase every one of them.
DEFAULT_THRESHOLD_HA = 1.0

#: Refuse to vectorise beyond this many stream cells. At 5 m resolution a
#: 342,550-cell grid thresholded at 0.01 ha would put nearly every cell in the
#: network, producing a response no client can use.
MAX_STREAM_CELLS = 400_000


class StreamExtractionError(ValueError):
    """The stream network cannot be extracted as asked."""


@dataclass(frozen=True)
class Reach:
    """One channel segment between junctions, in the grid's projected CRS."""

    #: Strahler order: 1 is a headwater.
    order: int
    #: Cell indices from upstream to downstream, inclusive of both ends.
    cells: list[tuple[int, int]]
    coordinates: list[tuple[float, float]]
    length_m: float
    #: Contributing area at the downstream end -- what this reach carries.
    upstream_area_ha: float


@dataclass(frozen=True)
class StreamNetwork:
    reaches: list[Reach]
    threshold_ha: float
    threshold_cells: int
    stream_cell_count: int
    max_order: int
    total_length_m: float
    #: Total channel length per square kilometre of the area analysed. Horton's
    #: drainage density: low means water lingers on the surface, high means it
    #: leaves quickly.
    drainage_density_km_per_km2: float | None
    area_analysed_km2: float | None

    def report(self) -> dict[str, Any]:
        by_order: dict[int, dict[str, float]] = {}
        for reach in self.reaches:
            entry = by_order.setdefault(reach.order, {"count": 0, "length_m": 0.0})
            entry["count"] += 1
            entry["length_m"] += reach.length_m
        return {
            "threshold_ha": self.threshold_ha,
            "threshold_cells": self.threshold_cells,
            "stream_cell_count": self.stream_cell_count,
            "reach_count": len(self.reaches),
            "max_strahler_order": self.max_order,
            "total_length_m": round(self.total_length_m, 1),
            "total_length_km": round(self.total_length_m / 1000.0, 3),
            "drainage_density_km_per_km2": (
                None
                if self.drainage_density_km_per_km2 is None
                else round(self.drainage_density_km_per_km2, 3)
            ),
            "area_analysed_km2": (
                None if self.area_analysed_km2 is None else round(self.area_analysed_km2, 4)
            ),
            "by_order": {
                str(order): {
                    "reaches": int(stats["count"]),
                    "length_m": round(stats["length_m"], 1),
                }
                for order, stats in sorted(by_order.items())
            },
        }


def threshold_cells_for(threshold_ha: float, cell_size_m: float) -> int:
    """Contributing cells equivalent to a contributing area.

    Expressed this way so a threshold means the same thing at any resolution: at
    5 m one hectare is 400 cells, at 30 m it is 11.
    """
    if threshold_ha <= 0:
        raise StreamExtractionError(f"threshold must be positive, got {threshold_ha} ha")
    if cell_size_m <= 0:
        raise StreamExtractionError(f"cell size must be positive, got {cell_size_m}")
    cells = threshold_ha * 10_000.0 / (cell_size_m * cell_size_m)
    # At least one: a threshold finer than a single cell still means "every cell
    # that carries any flow", and rounding it to zero would mean the opposite.
    return max(1, int(round(cells)))


def extract(
    flow: FlowGrids,
    *,
    transform: tuple[float, ...],
    cell_size_m: float,
    threshold_ha: float = DEFAULT_THRESHOLD_HA,
    within: Catchment | None = None,
) -> StreamNetwork:
    """Threshold, vectorise and order the drainage network.

    `within` restricts the network to one catchment, which is what makes the
    drainage density meaningful -- density over an arbitrary rectangle is a
    property of the rectangle.
    """
    threshold = threshold_cells_for(threshold_ha, cell_size_m)

    is_stream = flow.valid & (flow.accumulation >= threshold)
    if within is not None:
        is_stream &= within.mask

    count = int(is_stream.sum())
    if count > MAX_STREAM_CELLS:
        raise StreamExtractionError(
            f"a threshold of {threshold_ha} ha puts {count:,} cells in the network; "
            f"the limit is {MAX_STREAM_CELLS:,}. Use a larger threshold."
        )
    if count == 0:
        return StreamNetwork(
            reaches=[],
            threshold_ha=threshold_ha,
            threshold_cells=threshold,
            stream_cell_count=0,
            max_order=0,
            total_length_m=0.0,
            drainage_density_km_per_km2=0.0 if within is not None else None,
            area_analysed_km2=_area_km2(within),
        )

    downstream = _downstream_map(flow, is_stream)
    upstream = _invert(downstream)
    order = _strahler(is_stream, downstream, upstream)
    reaches = _to_reaches(is_stream, downstream, upstream, order, flow, transform, cell_size_m)

    total_length = sum(reach.length_m for reach in reaches)
    area_km2 = _area_km2(within)
    density = None if not area_km2 else (total_length / 1000.0) / area_km2

    return StreamNetwork(
        reaches=reaches,
        threshold_ha=threshold_ha,
        threshold_cells=threshold,
        stream_cell_count=count,
        max_order=max((reach.order for reach in reaches), default=0),
        total_length_m=total_length,
        drainage_density_km_per_km2=density,
        area_analysed_km2=area_km2,
    )


def _area_km2(catchment: Catchment | None) -> float | None:
    return None if catchment is None else catchment.area_m2 / 1e6


def _downstream_map(
    flow: FlowGrids, is_stream: npt.NDArray[np.bool_]
) -> dict[tuple[int, int], tuple[int, int]]:
    """For each stream cell, the stream cell it flows into.

    A cell whose D8 neighbour is outside the network is a network outlet and gets
    no entry -- it is where the reach ends.
    """
    code_to_offset = {code: (dr, dc) for dr, dc, code, _ in NEIGHBOURS}
    rows, cols = is_stream.shape
    out: dict[tuple[int, int], tuple[int, int]] = {}
    for row, col in zip(*np.nonzero(is_stream), strict=True):
        offset = code_to_offset.get(int(flow.direction[row, col]))
        if offset is None:  # code 0: an outlet or nodata
            continue
        nr, nc = row + offset[0], col + offset[1]
        if 0 <= nr < rows and 0 <= nc < cols and is_stream[nr, nc]:
            out[(int(row), int(col))] = (int(nr), int(nc))
    return out


def _invert(
    downstream: dict[tuple[int, int], tuple[int, int]],
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    upstream: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for source, target in downstream.items():
        upstream.setdefault(target, []).append(source)
    return upstream


def _strahler(
    is_stream: npt.NDArray[np.bool_],
    downstream: dict[tuple[int, int], tuple[int, int]],
    upstream: dict[tuple[int, int], list[tuple[int, int]]],
) -> dict[tuple[int, int], int]:
    """Strahler order per stream cell, computed from the headwaters down.

    Processed in topological order rather than recursively: a 340,000-cell grid
    would exceed Python's recursion limit long before it ran out of memory, and
    the D8 network after depression filling is a forest, so Kahn's algorithm
    applies directly.

    The rule at a junction: equal orders promote, unequal orders do not. Two
    first-order streams meeting make a second-order; a first joining a third
    leaves it third, which is the whole point of the measure -- it tracks
    branching structure, not how many tributaries happen to arrive.
    """
    order: dict[tuple[int, int], int] = {}
    pending = {
        cell: len(upstream.get(cell, ()))
        for cell in ((int(r), int(c)) for r, c in zip(*np.nonzero(is_stream), strict=True))
    }
    queue = deque(cell for cell, count in pending.items() if count == 0)

    while queue:
        cell = queue.popleft()
        feeders = [order[up] for up in upstream.get(cell, ()) if up in order]
        if not feeders:
            order[cell] = 1
        else:
            highest = max(feeders)
            order[cell] = highest + 1 if feeders.count(highest) > 1 else highest

        target = downstream.get(cell)
        if target is not None:
            pending[target] -= 1
            if pending[target] == 0:
                queue.append(target)

    if len(order) < len(pending):
        # Would mean a cycle in the D8 grid, which Priority-Flood + epsilon
        # conditioning is specifically there to prevent.
        log.warning(
            "strahler_incomplete",
            extra={"ordered": len(order), "stream_cells": len(pending)},
        )
    return order


def _to_reaches(
    is_stream: npt.NDArray[np.bool_],
    downstream: dict[tuple[int, int], tuple[int, int]],
    upstream: dict[tuple[int, int], list[tuple[int, int]]],
    order: dict[tuple[int, int], int],
    flow: FlowGrids,
    transform: tuple[float, ...],
    cell_size_m: float,
) -> list[Reach]:
    """Trace one line per Strahler stream: from where an order begins to where it ends.

    A reach runs from the cell that *attains* its order down to the cell where the
    order changes -- not from junction to junction. The distinction matters for
    more than tidiness: cutting at every junction chops a trunk into one segment
    per tributary, so the reach counts stop obeying Horton's law of stream
    numbers. Measured on the sample catchment, it produced 16 order-4 "reaches"
    against 10 of order 3, a bifurcation ratio of 0.62 -- an impossibility that is
    a definition error rather than a terrain feature.

    The boundary cell is shared: a reach ends *at* the cell where the order rises,
    and the next reach starts there, so the lines join up on a map instead of
    leaving a one-cell gap at every confluence.
    """
    diagonal = cell_size_m * math.sqrt(2.0)
    is_diagonal = {(dr, dc): diag for dr, dc, _, diag in NEIGHBOURS}

    def starts_a_reach(cell: tuple[int, int]) -> bool:
        feeders = upstream.get(cell, ())
        if not feeders:
            return True  # a headwater
        # A cell begins a reach when it holds an order none of its feeders had.
        mine = order.get(cell, 1)
        return all(order.get(up, 1) != mine for up in feeders)

    reaches: list[Reach] = []
    for row, col in zip(*np.nonzero(is_stream), strict=True):
        head = (int(row), int(col))
        if not starts_a_reach(head):
            continue

        mine = order.get(head, 1)
        cells = [head]
        length = 0.0
        cursor = head
        while True:
            target = downstream.get(cursor)
            if target is None:
                break
            step = (target[0] - cursor[0], target[1] - cursor[1])
            length += diagonal if is_diagonal.get(step, False) else cell_size_m
            cells.append(target)
            cursor = target
            # The order rises here, so this cell ends the stream -- and begins
            # the next one.
            if order.get(target, mine) != mine:
                break

        if len(cells) < 2:
            continue
        reaches.append(
            Reach(
                order=order.get(head, 1),
                cells=cells,
                coordinates=[_cell_centre(transform, r, c) for r, c in cells],
                length_m=length,
                upstream_area_ha=float(flow.accumulation[cells[-1]])
                * cell_size_m
                * cell_size_m
                / 10_000.0,
            )
        )
    return reaches


def _cell_centre(transform: tuple[float, ...], row: int, col: int) -> tuple[float, float]:
    a, b, c, d, e, f = tuple(transform)[:6]
    return (c + a * (col + 0.5) + b * (row + 0.5), f + d * (col + 0.5) + e * (row + 0.5))
