"""A stand-in for the Copernicus read, for tests of the drawn-area path.

Places synthetic terrain exactly where `copernicus_aws.fetch_dem` would: the same
buffer, the same UTM zone, the same grid origin and cell size. Only the heights
are invented -- a valley falling south with a hollow in it, so siting finds both
a channel and a depression to rank. Geometry is the part under test; realism of
the relief is not.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.core.crs import utm_epsg_for
from app.providers.elevation.base import Bounds, DemGrid

#: The sample sheet's extent: a real Indian village-scale rectangle, ~8.5 km².
DURG_BBOX = [81.2814, 21.2398, 81.3126, 21.2636]


class FakeFetch:
    """Callable with `fetch_dem`'s signature that counts how often it is used."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self, bounds: Bounds, *, cell_size_m: float | None = None, buffer_m: float = 500.0
    ) -> DemGrid:
        self.calls += 1
        return fake_dem(bounds, cell_size_m=cell_size_m, buffer_m=buffer_m)


def fake_dem(
    bounds: Bounds, *, cell_size_m: float | None = None, buffer_m: float = 500.0
) -> DemGrid:
    from pyproj import Transformer

    cell = float(cell_size_m or 30.0)
    clon, clat = bounds.centroid
    epsg = utm_epsg_for(clon, clat)
    deg_per_m_lat = 1.0 / 110_540.0
    deg_per_m_lon = 1.0 / (111_320.0 * max(0.05, math.cos(math.radians(clat))))
    b = Bounds(
        bounds.min_lon - buffer_m * deg_per_m_lon,
        bounds.min_lat - buffer_m * deg_per_m_lat,
        bounds.max_lon + buffer_m * deg_per_m_lon,
        bounds.max_lat + buffer_m * deg_per_m_lat,
    )
    xs, ys = Transformer.from_crs(4326, epsg, always_xy=True).transform(
        [b.min_lon, b.max_lon, b.min_lon, b.max_lon],
        [b.min_lat, b.min_lat, b.max_lat, b.max_lat],
    )
    min_x, max_x = float(min(xs)), float(max(xs))
    min_y, max_y = float(min(ys)), float(max(ys))
    n_cols = int(math.ceil((max_x - min_x) / cell)) + 1
    n_rows = int(math.ceil((max_y - min_y) / cell)) + 1
    transform = (cell, 0.0, min_x - cell / 2.0, 0.0, -cell, max_y + cell / 2.0)

    rr, cc = np.mgrid[0:n_rows, 0:n_cols].astype(np.float64)
    axis = n_cols / 2.0
    z = 280.0 + (n_rows - rr) * 0.30 + np.abs(cc - axis) * 0.20
    r = np.hypot(rr - n_rows * 0.55, cc - axis)
    z -= 4.0 * np.exp(-((r / max(3.0, n_rows * 0.08)) ** 2))
    elevation = z.astype(np.float32)

    return DemGrid(
        elevation=elevation,
        transform=transform,
        epsg=epsg,
        cell_size_m=cell,
        provenance=_provenance(bounds, b, cell, elevation, n_cols, n_rows, epsg, buffer_m),
    )


def _provenance(
    requested: Bounds,
    buffered: Bounds,
    cell: float,
    elevation: np.ndarray,
    n_cols: int,
    n_rows: int,
    epsg: int,
    buffer_m: float,
) -> dict[str, Any]:
    return {
        "elevation_source": "copernicus_dem_glo30",
        "tiles_used": ["fake_tile"],
        "tiles_failed": [],
        "requested_bounds_4326": list(requested.as_tuple()),
        "buffered_bounds_4326": list(buffered.as_tuple()),
        "buffer_m": buffer_m,
        "grid_resolution_m": cell,
        "grid_size": [n_cols, n_rows],
        "grid_cells": n_cols * n_rows,
        "working_crs_epsg": epsg,
        "elevation_min_m": round(float(elevation.min()), 2),
        "elevation_max_m": round(float(elevation.max()), 2),
        "relief_m": round(float(elevation.max() - elevation.min()), 2),
        "coverage_pct": 100.0,
    }
