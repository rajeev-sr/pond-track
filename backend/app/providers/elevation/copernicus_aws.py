"""Copernicus DEM GLO-30 from the AWS Open Data bucket (HLD §4.2 A1, M1-1).

The second `ElevationSource` alongside an uploaded contour map (ADR-7). Both
produce a metric `DemGrid`, so everything downstream -- conditioning, D8 routing,
catchment delineation, siting, runoff, pond design -- is identical regardless of
where the terrain came from. A protocol with a single implementation demonstrates
nothing; this is what makes the seam real.

Read straight from the public bucket as range-request COGs: no key, no quota, no
registration. Verified: 1°x1° tiles at 3600x3600, 1 arcsec (~30.9 m), float32,
internally tiled with overviews, so a windowed read of a 5x5 km area costs ~100 KB
rather than the whole tile.

Chosen over OpenTopography, which serves the same COP30 data behind an API key
that proved unobtainable. Same data, fewer preconditions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

from app.core.crs import CRSGuard, utm_epsg_for
from app.providers.base import Provenance, ProviderUnavailableError
from app.providers.elevation.base import Bounds, DemGrid

PROVIDER = "copernicus_dem_glo30"
BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"
PROVENANCE = Provenance(
    provider="Copernicus DEM (ESA) via AWS Open Data",
    dataset="COP-DEM GLO-30",
    resolution="1 arcsec (~30 m)",
    licence="Free, attribution required",
)

#: Native cell size. Interpolating a 30 m DEM to 5 m invents detail it does not
#: contain, so this is the default rather than something finer.
NATIVE_CELL_M = 30.0
#: Same guard as the contour path: bound the grid a caller can ask for.
MAX_GRID_CELLS = 20_000_000
#: Buffer added around a requested area so a catchment originating just outside
#: it is still complete (HLD CH-7).
DEFAULT_BUFFER_M = 500.0


@dataclass(frozen=True)
class CopernicusDemSource:
    """An `ElevationSource` backed by the Copernicus GLO-30 bucket."""

    bounds: Bounds
    buffer_m: float = DEFAULT_BUFFER_M
    name: str = PROVIDER

    def to_dem(self, cell_size_m: float | None = None) -> DemGrid:
        return fetch_dem(self.bounds, cell_size_m=cell_size_m, buffer_m=self.buffer_m)


def tile_name(lat: float, lon: float) -> str:
    """COP-DEM tiles are 1°x1°, named by their south-west corner."""
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    return (
        f"Copernicus_DSM_COG_10_{ns}{abs(int(math.floor(lat))):02d}_00_"
        f"{ew}{abs(int(math.floor(lon))):03d}_00_DEM"
    )


def tile_url(name: str) -> str:
    return f"{BUCKET}/{name}/{name}.tif"


def tiles_covering(bounds: Bounds) -> list[str]:
    """Every 1° tile the bounds touch, south-west to north-east."""
    names: list[str] = []
    lat = math.floor(bounds.min_lat)
    while lat <= bounds.max_lat:
        lon = math.floor(bounds.min_lon)
        while lon <= bounds.max_lon:
            names.append(tile_name(float(lat), float(lon)))
            lon += 1
        lat += 1
    return names


def fetch_dem(
    bounds: Bounds,
    *,
    cell_size_m: float | None = None,
    buffer_m: float = DEFAULT_BUFFER_M,
) -> DemGrid:
    """Read COP-DEM over `bounds` and reproject onto a metric grid.

    Bilinear resampling, unlike the nearest-neighbour used for land cover:
    elevation is a continuous field, so interpolating between cells is correct
    (whereas averaging class codes would invent categories).
    """
    cell = float(cell_size_m or NATIVE_CELL_M)
    if cell <= 0:
        raise ValueError(f"cell size must be positive, got {cell}")

    clon, clat = bounds.centroid
    epsg = utm_epsg_for(clon, clat)
    CRSGuard.require_projected(epsg, "DEM acquisition")

    # Work out the target grid in metres, buffered so an upstream area starting
    # just outside the request is still captured.
    deg_per_m_lat = 1.0 / 110_540.0
    deg_per_m_lon = 1.0 / (111_320.0 * max(0.05, math.cos(math.radians(clat))))
    b = Bounds(
        bounds.min_lon - buffer_m * deg_per_m_lon,
        bounds.min_lat - buffer_m * deg_per_m_lat,
        bounds.max_lon + buffer_m * deg_per_m_lon,
        bounds.max_lat + buffer_m * deg_per_m_lat,
    )

    from pyproj import Transformer

    to_utm = Transformer.from_crs(4326, epsg, always_xy=True)
    xs, ys = to_utm.transform(
        [b.min_lon, b.max_lon, b.min_lon, b.max_lon],
        [b.min_lat, b.min_lat, b.max_lat, b.max_lat],
    )
    min_x, max_x = float(min(xs)), float(max(xs))
    min_y, max_y = float(min(ys)), float(max(ys))

    n_cols = int(math.ceil((max_x - min_x) / cell)) + 1
    n_rows = int(math.ceil((max_y - min_y) / cell)) + 1
    if n_cols * n_rows > MAX_GRID_CELLS:
        raise ProviderUnavailableError(
            PROVIDER,
            f"a {cell:g} m grid over this area needs {n_cols * n_rows:,} cells, "
            f"over the {MAX_GRID_CELLS:,} limit",
        )

    transform = (cell, 0.0, min_x - cell / 2.0, 0.0, -cell, max_y + cell / 2.0)
    dst = np.full((n_rows, n_cols), np.nan, dtype=np.float32)

    names = tiles_covering(b)
    used: list[str] = []
    errors: list[str] = []
    for name in names:
        try:
            with rasterio.open(tile_url(name)) as src:
                window = from_bounds(*b.as_tuple(), src.transform)
                block = src.read(1, window=window, boundless=True, fill_value=np.nan)
                if block.size == 0 or not np.isfinite(block).any():
                    continue
                out = np.full((n_rows, n_cols), np.nan, dtype=np.float32)
                reproject(
                    source=block,
                    destination=out,
                    src_transform=src.window_transform(window),
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=f"EPSG:{epsg}",
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan,
                    dst_nodata=np.nan,
                )
                dst = np.where(np.isnan(dst), out, dst)
                used.append(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")

    if not used:
        raise ProviderUnavailableError(
            PROVIDER,
            f"no COP-DEM tile could be read for this area (tried {len(names)})"
            + (f": {'; '.join(errors)}" if errors else ""),
        )
    if not np.isfinite(dst).any():
        raise ProviderUnavailableError(PROVIDER, "tiles read but contained no elevation")

    finite = dst[np.isfinite(dst)]
    return DemGrid(
        elevation=dst,
        transform=transform,
        epsg=epsg,
        cell_size_m=cell,
        provenance={
            "elevation_source": "copernicus_dem_glo30",
            "tiles_used": used,
            "tiles_failed": errors,
            "requested_bounds_4326": list(bounds.as_tuple()),
            "buffered_bounds_4326": list(b.as_tuple()),
            "buffer_m": buffer_m,
            "grid_resolution_m": cell,
            "grid_size": [n_cols, n_rows],
            "grid_cells": n_cols * n_rows,
            "working_crs_epsg": epsg,
            "resampling": "bilinear",
            "elevation_min_m": round(float(finite.min()), 2),
            "elevation_max_m": round(float(finite.max()), 2),
            "relief_m": round(float(finite.max() - finite.min()), 2),
            "valid_cells": int(finite.size),
            "coverage_pct": round(100.0 * finite.size / dst.size, 1),
            "source": PROVENANCE.as_dict(),
        },
    )


def sample_elevation(lon: float, lat: float) -> float:
    """Elevation at one coordinate. Cheap sanity check, not for bulk use."""
    name = tile_name(lat, lon)
    try:
        with rasterio.open(tile_url(name)) as src:
            value = next(iter(src.sample([(lon, lat)])))[0]
    except Exception as exc:
        raise ProviderUnavailableError(PROVIDER, f"{name}: {type(exc).__name__}") from exc
    if not math.isfinite(float(value)):
        raise ProviderUnavailableError(PROVIDER, f"no elevation at {lat:.4f},{lon:.4f}")
    return float(value)


def as_grid(
    array: npt.NDArray[np.float32],
    transform: tuple[float, ...],
    epsg: int,
    cell: float,
    provenance: dict[str, Any] | None = None,
) -> DemGrid:
    """Wrap a raw array as a DemGrid. Used by tests and by offline fixtures."""
    return DemGrid(
        elevation=array,
        transform=transform,
        epsg=epsg,
        cell_size_m=cell,
        provenance=provenance or {},
    )
