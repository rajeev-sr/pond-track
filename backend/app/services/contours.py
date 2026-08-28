"""Contour lines from a DEM (M2-5, M2-6).

The mirror image of `services.interpolate`: that turns contour lines into a grid,
this turns a grid back into contour lines. Together they close a loop that is
worth being able to close -- regenerating 1 m contours from a DEM interpolated
out of 1 m contours should reproduce the input, and the golden test asserts it
does. A silent interpolation error shows up there and almost nowhere else.

Marching squares (`skimage.measure.find_contours`), not the `gdal_contour` CLI:
HLD Decision 7 took the rasterio path so there is no system GDAL binary to
depend on, and the raster is already in memory when this runs.

Two things a contour map needs beyond the geometry:

* **Simplification.** Marching squares emits a vertex per cell crossing, so a
  650x527 grid produces tens of thousands of vertices per level -- more than the
  browser can draw and far more than the terrain justifies. Douglas-Peucker with
  a tolerance tied to the cell size removes the staircase without moving the line
  anywhere a reader would notice.
* **Index contours.** Every fifth line is marked, which is what makes a contour
  map readable: the eye counts thick lines and interpolates between them, rather
  than counting forty identical ones.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from shapely.geometry import LineString

log = logging.getLogger(__name__)

#: Douglas-Peucker tolerance as a fraction of the cell size. At 0.35 the
#: simplified line stays inside a third of a cell of the original -- well below
#: the interpolation's own uncertainty, so nothing real is lost while the
#: per-cell staircase goes.
SIMPLIFY_FRACTION = 0.35

#: Every Nth contour is an index contour. Five is the cartographic convention on
#: Indian topo sheets and USGS quadrangles alike.
DEFAULT_INDEX_EVERY = 5

#: A line shorter than this is a fragment around a single noisy cell, not a
#: contour. Expressed in cells so it scales with resolution.
MIN_LINE_CELLS = 4.0

#: Guard against a caller asking for a 0.01 m interval over 300 m of relief.
MAX_LEVELS = 400


class ContourGenerationError(ValueError):
    """The requested contours cannot be generated."""


@dataclass(frozen=True)
class ContourLine:
    """One contour, in the raster's own projected CRS."""

    elevation_m: float
    #: True for every Nth level -- the ones a map draws thicker and labels.
    is_index: bool
    coordinates: list[tuple[float, float]]
    length_m: float


@dataclass(frozen=True)
class ContourSet:
    """Everything generated, plus how it was generated."""

    lines: list[ContourLine]
    interval_m: float
    index_every: int
    levels: list[float]
    elevation_min_m: float
    elevation_max_m: float
    vertices_before_simplify: int
    vertices_after_simplify: int
    simplify_tolerance_m: float
    epsg: int

    def report(self) -> dict[str, Any]:
        reduction = (
            0.0
            if not self.vertices_before_simplify
            else 100.0 * (1.0 - self.vertices_after_simplify / self.vertices_before_simplify)
        )
        return {
            "interval_m": self.interval_m,
            "index_every": self.index_every,
            "level_count": len(self.levels),
            "levels": [round(v, 3) for v in self.levels],
            "line_count": len(self.lines),
            "index_line_count": sum(1 for line in self.lines if line.is_index),
            "elevation_min_m": round(self.elevation_min_m, 3),
            "elevation_max_m": round(self.elevation_max_m, 3),
            "total_length_m": round(sum(line.length_m for line in self.lines), 1),
            "vertices_before_simplify": self.vertices_before_simplify,
            "vertices_after_simplify": self.vertices_after_simplify,
            "vertex_reduction_pct": round(reduction, 1),
            "simplify_tolerance_m": round(self.simplify_tolerance_m, 3),
            "working_crs_epsg": self.epsg,
        }


def levels_for(elevation_min: float, elevation_max: float, interval_m: float) -> list[float]:
    """The contour levels inside a range, on multiples of the interval.

    Snapped to multiples rather than started at the minimum, because a contour
    map's whole value is that its lines fall on round numbers: 267, 268, 269 --
    not 267.31, 268.31.
    """
    if interval_m <= 0:
        raise ContourGenerationError(f"interval must be positive, got {interval_m}")
    if not (math.isfinite(elevation_min) and math.isfinite(elevation_max)):
        raise ContourGenerationError("elevation range is not finite")
    if elevation_max <= elevation_min:
        raise ContourGenerationError(
            f"no relief to contour: the surface spans {elevation_min} to {elevation_max} m"
        )

    first = math.ceil(elevation_min / interval_m) * interval_m
    count = int(math.floor((elevation_max - first) / interval_m)) + 1
    if count <= 0:
        raise ContourGenerationError(
            f"an interval of {interval_m} m produces no levels across "
            f"{elevation_max - elevation_min:.2f} m of relief"
        )
    if count > MAX_LEVELS:
        raise ContourGenerationError(
            f"an interval of {interval_m} m over {elevation_max - elevation_min:.1f} m "
            f"of relief needs {count} levels; the limit is {MAX_LEVELS}. Use a "
            "coarser interval."
        )
    # Rounded because repeated addition of a float interval drifts: at 0.1 m the
    # 30th level lands on 3.0000000000000004 and renders as such in a label.
    return [round(first + step * interval_m, 6) for step in range(count)]


def generate(
    elevation: npt.NDArray[np.floating],
    *,
    transform: tuple[float, ...],
    epsg: int,
    cell_size_m: float,
    interval_m: float,
    index_every: int = DEFAULT_INDEX_EVERY,
    simplify: bool = True,
) -> ContourSet:
    """Trace contours through `elevation` and return them in projected metres.

    The array is expected north-up, as every DEM in this system is: row 0 is the
    northern edge, so a row index maps to a *decreasing* y.
    """
    if index_every < 1:
        raise ContourGenerationError(f"index_every must be at least 1, got {index_every}")

    surface = np.asarray(elevation, dtype=np.float64)
    finite = np.isfinite(surface)
    if not finite.any():
        raise ContourGenerationError("the surface has no valid elevations")

    valid = surface[finite]
    levels = levels_for(float(valid.min()), float(valid.max()), interval_m)

    # `find_contours` has no nodata concept and would trace the boundary between
    # data and NaN as though it were terrain. Filling with a value below every
    # level keeps the tracing inside the data: a level is never crossed there, so
    # no contour is generated along the edge.
    below_everything = float(valid.min()) - abs(interval_m) * 10.0 - 1.0
    traceable = np.where(finite, surface, below_everything)

    tolerance = SIMPLIFY_FRACTION * cell_size_m if simplify else 0.0
    min_length_m = MIN_LINE_CELLS * cell_size_m

    lines_out: list[ContourLine] = []
    before = after = 0
    for index, level in enumerate(levels):
        for path in _trace(traceable, level):
            for run in _split_on_valid(path, finite):
                projected = [_cell_to_projected(transform, row, col) for row, col in run]
                before += len(projected)
                geometry = LineString(projected)
                if tolerance > 0:
                    geometry = geometry.simplify(tolerance, preserve_topology=False)
                if geometry.is_empty or geometry.length < min_length_m:
                    continue
                coordinates = [(float(x), float(y)) for x, y in geometry.coords]
                after += len(coordinates)
                lines_out.append(
                    ContourLine(
                        elevation_m=level,
                        # Counted from the first level rather than from zero, so
                        # the emphasis is regular whatever the elevation range.
                        is_index=(index % index_every == 0),
                        coordinates=coordinates,
                        length_m=float(geometry.length),
                    )
                )

    return ContourSet(
        lines=lines_out,
        interval_m=interval_m,
        index_every=index_every,
        levels=levels,
        elevation_min_m=float(valid.min()),
        elevation_max_m=float(valid.max()),
        vertices_before_simplify=before,
        vertices_after_simplify=after,
        simplify_tolerance_m=tolerance,
        epsg=epsg,
    )


def _split_on_valid(
    path: npt.NDArray[np.floating], finite: npt.NDArray[np.bool_]
) -> list[npt.NDArray[np.floating]]:
    """Cut a traced path into the runs that lie over surveyed ground.

    A traced vertex sits *between* cells, so all four cells it touches must hold
    data for it to count. Accepting a vertex whose rounded cell happens to be
    valid lets the line creep a cell into the hole, which shows as a fringe along
    the survey boundary.

    Runs of fewer than two vertices are dropped: a single point is not a line.
    """
    if finite.all():
        return [path]

    rows, cols = path[:, 0], path[:, 1]
    height, width = finite.shape

    row_lo = np.clip(np.floor(rows).astype(int), 0, height - 1)
    row_hi = np.clip(np.ceil(rows).astype(int), 0, height - 1)
    col_lo = np.clip(np.floor(cols).astype(int), 0, width - 1)
    col_hi = np.clip(np.ceil(cols).astype(int), 0, width - 1)

    keep = (
        finite[row_lo, col_lo]
        & finite[row_lo, col_hi]
        & finite[row_hi, col_lo]
        & finite[row_hi, col_hi]
    )
    if keep.all():
        return [path]

    runs: list[npt.NDArray[np.floating]] = []
    start: int | None = None
    for position, ok in enumerate(keep):
        if ok and start is None:
            start = position
        elif not ok and start is not None:
            if position - start >= 2:
                runs.append(path[start:position])
            start = None
    if start is not None and len(keep) - start >= 2:
        runs.append(path[start:])
    return runs


def _trace(surface: npt.NDArray[np.floating], level: float) -> list[np.ndarray]:
    """Marching squares at one level, as arrays of (row, col) in cell units."""
    from skimage import measure

    try:
        return [np.asarray(path) for path in measure.find_contours(surface, level)]
    except (ValueError, RuntimeError) as exc:  # pragma: no cover - defensive
        log.warning("contour_trace_failed", extra={"level": level, "error": str(exc)})
        return []


def _cell_to_projected(transform: tuple[float, ...], row: float, col: float) -> tuple[float, float]:
    """Map fractional (row, col) to projected coordinates via the affine transform.

    `find_contours` returns sub-cell positions -- that is the point of marching
    squares -- so the usual integer index arithmetic will not do. The half-cell
    offset puts a coordinate at the cell's *centre*, matching where the
    interpolation placed the elevation it read.
    """
    a, b, c, d, e, f = tuple(transform)[:6]
    x = c + a * (col + 0.5) + b * (row + 0.5)
    y = f + d * (col + 0.5) + e * (row + 0.5)
    return (x, y)
