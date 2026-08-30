"""Contour lines -> DEM raster (MC-7, HLD 6.10.2).

Inverts 6.8: contours are normally the *output* of a DEM, and here they are the
input. The result is a `DemGrid` in a projected metric CRS -- byte-for-byte the
same currency a remote DEM tile produces -- so sink filling, D8 flow routing,
flow accumulation and catchment delineation all run downstream unchanged
(HLD ADR-7).

Nothing in this module is specific to any particular contour map: the grid
resolution, extent and working CRS are all derived from the input geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.ndimage import gaussian_filter

from app.core.crs import CRSGuard
from app.providers.elevation.base import DemGrid
from app.providers.elevation.contour_kml import ContourParseError, ParsedContours

#: Resolutions a surveyor would recognise. Snapping to these keeps the reported
#: cell size legible instead of "7.48 m", and ties break toward the finer value.
RESOLUTION_LADDER = (1.0, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0)

MIN_CELL_M = 1.0
MAX_CELL_M = 30.0
#: Guard against a pathological explicit `cell_size_m` producing a raster that
#: will not fit in memory or finish in time. Measured on the 8.5 km2 / 1355-line
#: sample, interpolation is close to linear in cell count:
#:
#:     10 m ->    86 k cells -> 0.5 s      3 m ->   949 k cells -> 1.9 s
#:      5 m ->   343 k cells -> 1.1 s      1 m -> 8_519 k cells -> 6.7 s
#:
#: so 20 M cells is roughly 15 s -- bounded, and far beyond any resolution a
#: contour survey can justify. The *derived* cell size never approaches this;
#: the cap exists only for a caller-supplied override.
MAX_GRID_CELLS = 20_000_000

#: De-terracing strength, in cells. TIN interpolation between closely spaced
#: contours yields stepped facets, and D8 on a stepped surface produces spurious
#: parallel flow (HLD CH-2). Light smoothing removes the steps without moving
#: the surface off the contour elevations.
DEFAULT_SMOOTH_SIGMA_CELLS = 0.75


@dataclass(frozen=True)
class InterpolationReport:
    """Everything the API needs to state how the surface was produced."""

    cell_size_m: float
    cell_size_derived: bool
    mean_contour_spacing_m: float
    total_contour_length_m: float
    grid_width: int
    grid_height: int
    points_used: int
    points_before_resample: int
    method: str
    smoothing_sigma_cells: float
    hull_coverage_pct: float
    elevation_min_m: float
    elevation_max_m: float
    relief_m: float

    def as_dict(self) -> dict[str, object]:
        return {
            "grid_resolution_m": self.cell_size_m,
            "grid_resolution_derived": self.cell_size_derived,
            "mean_contour_spacing_m": round(self.mean_contour_spacing_m, 2),
            "total_contour_length_m": round(self.total_contour_length_m, 1),
            "grid_size": [self.grid_width, self.grid_height],
            "grid_cells": self.grid_width * self.grid_height,
            "interpolation_method": self.method,
            "vertices_after_resample": self.points_used,
            "vertices_before_resample": self.points_before_resample,
            "smoothing_sigma_cells": self.smoothing_sigma_cells,
            "hull_coverage_pct": round(self.hull_coverage_pct, 1),
            "interpolated_elevation_min_m": round(self.elevation_min_m, 3),
            "interpolated_elevation_max_m": round(self.elevation_max_m, 3),
            "interpolated_relief_m": round(self.relief_m, 3),
        }


# ── geometry helpers ─────────────────────────────────────────────────────────
def polyline_length(pts: np.ndarray) -> float:
    """Total planar length of an (N, 2) polyline, in the units of `pts`."""
    if len(pts) < 2:
        return 0.0
    return float(np.hypot(*np.diff(pts, axis=0).T).sum())


def resample_polyline(pts: np.ndarray, spacing: float) -> np.ndarray:
    """Re-space a polyline's vertices to ~`spacing`, preserving both endpoints.

    Does double duty: it *densifies* long segments (so TIN triangles cannot span
    two contour levels and create false terraces) and *decimates* over-dense ones
    (the supplied sample already has ~4 m vertex spacing, far finer than any
    useful grid). Controlling point count this way keeps the Delaunay
    triangulation tractable on large surveys.
    """
    if len(pts) < 2 or spacing <= 0:
        return pts
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= spacing:
        return np.asarray([pts[0], pts[-1]])
    n = max(1, int(math.ceil(total / spacing)))
    targets = np.linspace(0.0, total, n + 1)
    x = np.interp(targets, cum, pts[:, 0])
    y = np.interp(targets, cum, pts[:, 1])
    return np.column_stack([x, y])


def _snap_to_ladder(value: float) -> float:
    """Nearest legible resolution; ties resolve to the finer value."""
    return min(RESOLUTION_LADDER, key=lambda c: (abs(c - value), c))


def derive_cell_size_m(area_m2: float, total_length_m: float) -> tuple[float, float]:
    """Derive a grid resolution from the contour geometry itself.

    For a family of contours spaced `d` apart covering area `A`, the total line
    length is `L ~= A / d`, so `d ~= A / L`. That mean spacing is the real limit
    on the information the survey contains -- interpolating much finer invents
    detail that is not there, and much coarser throws the survey away.

    A cell of `d / 2` is taken as the useful upper bound on resolvable detail
    (the same reasoning as a Nyquist limit), then snapped to a legible value.

    Returns `(cell_size_m, mean_spacing_m)`.
    """
    if total_length_m <= 0:
        raise ContourParseError("contour lines have zero total length")
    spacing = area_m2 / total_length_m
    cell = min(max(spacing / 2.0, MIN_CELL_M), MAX_CELL_M)
    return _snap_to_ladder(cell), spacing


def _nan_aware_gaussian(grid: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur that neither reads nor writes across the nodata boundary."""
    if sigma <= 0:
        return grid
    valid = np.isfinite(grid)
    filled = np.where(valid, grid, 0.0)
    num = gaussian_filter(filled, sigma=sigma, mode="nearest")
    den = gaussian_filter(valid.astype(np.float64), sigma=sigma, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 1e-12, num / den, np.nan)
    return np.where(valid, out, np.nan).astype(np.float32)


# ── the interpolation itself ─────────────────────────────────────────────────
def contours_to_dem(
    parsed: ParsedContours,
    *,
    cell_size_m: float | None = None,
    smooth_sigma_cells: float = DEFAULT_SMOOTH_SIGMA_CELLS,
    fill_gaps: bool = True,
) -> tuple[DemGrid, InterpolationReport]:
    """Interpolate parsed contours onto a regular metric grid.

    `cell_size_m=None` derives the resolution from the data (see
    `derive_cell_size_m`); an explicit value is honoured and reported as such.
    """
    epsg = parsed.utm_epsg
    CRSGuard.require_projected(epsg, "contour interpolation")

    # 1. Reproject every vertex to the working metric CRS, in bulk (ADR-5).
    tf = Transformer.from_crs(4326, epsg, always_xy=True)
    lines_m: list[tuple[float, np.ndarray]] = []
    for ln in parsed.lines:
        lon = np.fromiter((c[0] for c in ln.coords), dtype=np.float64, count=len(ln.coords))
        lat = np.fromiter((c[1] for c in ln.coords), dtype=np.float64, count=len(ln.coords))
        x, y = tf.transform(lon, lat)
        lines_m.append((ln.elevation_m, np.column_stack([x, y])))

    all_x = np.concatenate([p[:, 0] for _, p in lines_m])
    all_y = np.concatenate([p[:, 1] for _, p in lines_m])
    min_x, max_x = float(all_x.min()), float(all_x.max())
    min_y, max_y = float(all_y.min()), float(all_y.max())
    width_m, height_m = max_x - min_x, max_y - min_y
    if width_m <= 0 or height_m <= 0:
        raise ContourParseError("projected contour extent is degenerate")

    total_length = sum(polyline_length(p) for _, p in lines_m)

    # 2. Resolution: derived from the geometry unless the caller overrides it.
    derived_cell, mean_spacing = derive_cell_size_m(width_m * height_m, total_length)
    if cell_size_m is None:
        cell = derived_cell
        derived = True
    else:
        if not (MIN_CELL_M <= cell_size_m <= MAX_CELL_M):
            raise ContourParseError(
                f"cell_size_m must be between {MIN_CELL_M:g} and {MAX_CELL_M:g} m, "
                f"got {cell_size_m}"
            )
        cell = float(cell_size_m)
        derived = False

    n_cols = int(math.ceil(width_m / cell)) + 1
    n_rows = int(math.ceil(height_m / cell)) + 1
    if n_cols * n_rows > MAX_GRID_CELLS:
        raise ContourParseError(
            f"a {cell:g} m grid over this extent needs {n_cols * n_rows:,} cells, over the "
            f"{MAX_GRID_CELLS:,} limit; use a coarser cell_size_m"
        )

    # 3. Re-space vertices to ~one per cell: densifies sparse segments and
    #    decimates over-dense ones, so the triangulation stays tractable.
    pts_before = int(sum(len(p) for _, p in lines_m))
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    for elev, p in lines_m:
        rp = resample_polyline(p, cell)
        xs.append(rp[:, 0])
        ys.append(rp[:, 1])
        zs.append(np.full(len(rp), elev, dtype=np.float64))
    px = np.concatenate(xs)
    py = np.concatenate(ys)
    pz = np.concatenate(zs)

    if len(px) < 4:
        raise ContourParseError(
            f"only {len(px)} interpolation points after resampling; the contours are "
            "too short relative to the grid resolution"
        )

    # 4. Delaunay/TIN interpolation. Contour vertices are *exact* elevations on
    #    lines, not scattered samples, so a TIN honours them and interpolates
    #    linearly between adjacent contours -- how a person reads a contour map.
    #    IDW would produce a bullseye at every vertex; kriging's variogram
    #    assumptions are unjustifiable for lines of constant value (HLD 6.10.2).
    grid_x = min_x + np.arange(n_cols, dtype=np.float64) * cell
    grid_y = max_y - np.arange(n_rows, dtype=np.float64) * cell  # north-up
    gx, gy = np.meshgrid(grid_x, grid_y)

    interp = LinearNDInterpolator(np.column_stack([px, py]), pz)
    z = interp(gx, gy).astype(np.float32)  # NaN outside the convex hull

    # LinearNDInterpolator returning NaN beyond the hull *is* the hull clip --
    # the surface is never extrapolated past surveyed ground.
    inside_hull = np.isfinite(z)
    hull_pct = 100.0 * float(inside_hull.sum()) / inside_hull.size

    # 5. Fill only the interior slivers Qhull leaves along the hull edge.
    if fill_gaps and inside_hull.any() and not inside_hull.all():
        near = NearestNDInterpolator(np.column_stack([px, py]), pz)
        edge = ~inside_hull
        # One cell of reach: enough for hull-edge slivers, not enough to
        # extrapolate into genuinely unsurveyed ground.
        reach = gaussian_filter(inside_hull.astype(np.float64), sigma=1.0, mode="nearest") > 0.15
        target = edge & reach
        if target.any():
            z[target] = near(gx[target], gy[target]).astype(np.float32)

    if not np.isfinite(z).any():
        raise ContourParseError("interpolation produced no valid cells")

    # 6. De-terrace (HLD 6.10.2 step 6).
    z = _nan_aware_gaussian(z, smooth_sigma_cells * 1.0)

    finite = z[np.isfinite(z)]
    report = InterpolationReport(
        cell_size_m=cell,
        cell_size_derived=derived,
        mean_contour_spacing_m=mean_spacing,
        total_contour_length_m=total_length,
        grid_width=n_cols,
        grid_height=n_rows,
        points_used=int(len(px)),
        points_before_resample=pts_before,
        method="linear_tin_delaunay",
        smoothing_sigma_cells=smooth_sigma_cells,
        hull_coverage_pct=hull_pct,
        elevation_min_m=float(finite.min()),
        elevation_max_m=float(finite.max()),
        relief_m=float(finite.max() - finite.min()),
    )

    # Affine in rasterio/affine order: (a, b, c, d, e, f) where
    #   x = a*col + b*row + c   and   y = d*col + e*row + f
    transform = (cell, 0.0, min_x - cell / 2.0, 0.0, -cell, max_y + cell / 2.0)

    dem = DemGrid(
        elevation=z,
        transform=transform,
        epsg=epsg,
        cell_size_m=cell,
        provenance={**parsed.summary(), **report.as_dict()},
    )
    return dem, report
