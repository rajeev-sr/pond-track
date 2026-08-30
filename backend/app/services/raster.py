"""Terrain derivatives, and writing them as Cloud-Optimized GeoTIFFs (M2-3, M2-4).

Two jobs that belong together:

* **Derive** hillshade from an elevation grid. Slope already exists in
  `services.hydrology` -- computed by Horn's method because the hydrology needs
  it -- and is reused rather than reimplemented, so a map layer and a siting
  criterion can never disagree about the steepness of the same cell.
* **Write** a raster the browser can read a tile at a time. HLD ADR-3 is the
  reason: a 25 km² DEM at 5 m is 1,000,000 cells, and serving that as JSON
  freezes the tab. As a COG, the browser fetches only the 256x256 tiles it can
  see.

GDAL's native `COG` driver does the layout work -- internal tiling, overviews,
the header ordering that makes a range request useful. Before 3.1 this needed
`rio-cogeo` and a two-pass write; GDAL here is 3.9, so the driver is used
directly and there is no extra dependency.

Everything is written in the **working UTM CRS**, not reprojected to Web
Mercator. TiTiler reprojects per tile, and warping the whole raster once up front
would resample the elevations that every downstream number is computed from.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

log = logging.getLogger(__name__)

Product = Literal["dem", "slope", "hillshade"]

#: Default illumination. 315° (north-west) at 45° is the cartographic convention
#: -- the human eye reads a shaded relief as convex only when the light comes
#: from the upper left, and reverses the terrain if it comes from below.
DEFAULT_AZIMUTH_DEG = 315.0
DEFAULT_ALTITUDE_DEG = 45.0

#: Vertical exaggeration. 1.0 is true scale; Indian plateau relief of 30 m over
#: 3 km is nearly invisible without it, so callers commonly raise this.
DEFAULT_Z_FACTOR = 1.0

#: 512 rather than the 256 GDAL defaults to: fewer, larger tiles suit a raster
#: read over HTTP, where per-request latency dominates transfer time.
COG_BLOCK_SIZE = 512

#: DEFLATE over ZSTD for the elevation and slope bands: both are supported
#: everywhere, and DEFLATE is what every GDAL build can read without a plugin.
COG_COMPRESS = "DEFLATE"

#: Elevation and slope are float32 with an explicit nodata; hillshade is uint8
#: where 0 is a legitimate value (fully shadowed), so it uses 255 for nodata and
#: the shading is compressed into 0-254.
NODATA_FLOAT = -9999.0
NODATA_HILLSHADE = 255


class RasterWriteError(RuntimeError):
    """The raster could not be written, or what was written is not a COG."""


@dataclass(frozen=True)
class RasterAsset:
    """A written COG and what it holds."""

    product: Product
    path: Path
    epsg: int
    resolution_m: float
    width: int
    height: int
    dtype: str
    nodata: float | int
    bounds_4326: tuple[float, float, float, float]
    size_bytes: int
    checksum_sha256: str
    stats: dict[str, float | int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "path": str(self.path),
            "epsg": self.epsg,
            "resolution_m": round(self.resolution_m, 3),
            "width_px": self.width,
            "height_px": self.height,
            "dtype": self.dtype,
            "nodata": self.nodata,
            "bounds_4326": [round(v, 6) for v in self.bounds_4326],
            "size_bytes": self.size_bytes,
            "checksum_sha256": self.checksum_sha256,
            "stats": self.stats,
        }


def hillshade(
    elevation: npt.NDArray[np.floating],
    cell_size_m: float,
    *,
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG,
    altitude_deg: float = DEFAULT_ALTITUDE_DEG,
    z_factor: float = DEFAULT_Z_FACTOR,
) -> npt.NDArray[np.uint8]:
    """Shaded relief, 0 (fully shadowed) to 254, with 255 for nodata.

    Computed as the dot product of the surface normal with the illumination
    direction, rather than through the slope-and-aspect trigonometry the GDAL
    documentation states::

        N = (-dz/dx, -dz/dy, 1)                      (surface normal, unnormalised)
        L = (cos(h)*sin(A), cos(h)*cos(A), sin(h))   (unit vector toward the light)
        H = 254 * clamp(N.L / |N|, 0, 1)

    The two are equivalent, but the slope/aspect form needs an `atan2` whose
    argument order and sign encode a convention, and getting it wrong rotates the
    illumination by 90 degrees -- which renders as a perfectly plausible hillshade
    of the wrong terrain. That is exactly what happened here: with light from
    315 degrees the brightest facet came out facing 225. The dot product has one
    convention left to state, and it is checkable by hand:

    * ``A`` is a **compass** azimuth, clockwise from north, naming the direction
      the light comes *from* -- so its east component is ``sin(A)`` and its north
      component is ``cos(A)``.
    * A north-up raster's **row index increases southward**, so the derivative
      taken over rows is d z/d(south); geographic ``dz/dy`` is its negation.

    Verified against planes of known aspect: with the default 315 degrees at
    45 degrees, a plane falling toward the north-west reads 224 and one falling
    toward the south-east reads 121, and flat ground reads
    ``254*sin(45 degrees) = 180``.

    Nodata propagates: a cell with no elevation is nodata in the output rather
    than shaded from a fabricated neighbour.
    """
    if cell_size_m <= 0:
        raise ValueError(f"cell size must be positive, got {cell_size_m}")
    if not 0.0 < altitude_deg <= 90.0:
        raise ValueError(f"altitude must be in (0, 90], got {altitude_deg}")

    z = np.asarray(elevation, dtype=np.float64)
    surface = np.where(np.isfinite(z), z, np.nan)
    padded = np.pad(surface, 1, mode="edge")

    # Horn's 3x3 weighted differences -- the same kernel `slope_percent` uses, so
    # a hillshade layer and a slope layer describe one surface.
    dz_dx = (
        (padded[:-2, 2:] + 2 * padded[1:-1, 2:] + padded[2:, 2:])
        - (padded[:-2, :-2] + 2 * padded[1:-1, :-2] + padded[2:, :-2])
    ) / (8.0 * cell_size_m)
    dz_drow = (
        (padded[2:, :-2] + 2 * padded[2:, 1:-1] + padded[2:, 2:])
        - (padded[:-2, :-2] + 2 * padded[:-2, 1:-1] + padded[:-2, 2:])
    ) / (8.0 * cell_size_m)
    dz_dy = -dz_drow  # rows run south; geographic y runs north

    gx = z_factor * dz_dx
    gy = z_factor * dz_dy

    altitude_rad = math.radians(altitude_deg)
    azimuth_rad = math.radians(azimuth_deg)
    light_east = math.cos(altitude_rad) * math.sin(azimuth_rad)
    light_north = math.cos(altitude_rad) * math.cos(azimuth_rad)
    light_up = math.sin(altitude_rad)

    with np.errstate(invalid="ignore"):
        numerator = -gx * light_east - gy * light_north + light_up
        illumination = numerator / np.sqrt(gx * gx + gy * gy + 1.0)

    # 254 not 255: 255 is reserved for nodata, and 0 is a real value (shadow).
    scaled = np.clip(illumination, 0.0, 1.0) * 254.0
    out = np.where(np.isfinite(scaled), scaled, NODATA_HILLSHADE)
    out[~np.isfinite(surface)] = NODATA_HILLSHADE
    result: npt.NDArray[np.uint8] = np.rint(out).astype(np.uint8)
    return result


def write_cog(
    destination: Path,
    array: npt.NDArray[Any],
    *,
    transform: Affine | tuple[float, ...],
    epsg: int,
    nodata: float | int,
    product: Product,
    overview_resampling: str = "average",
) -> RasterAsset:
    """Write `array` as a COG and return what was written.

    Written to a temporary neighbour and moved into place, so a reader (TiTiler
    shares this directory) can never observe a half-written file at the final
    path.

    `overview_resampling` should be `average` for continuous surfaces and
    `nearest` for anything categorical -- averaging class codes invents
    categories that do not exist.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    data = np.asarray(array)
    if data.ndim != 2:
        raise RasterWriteError(f"expected a 2-D array, got shape {data.shape}")
    height, width = data.shape
    if height < 2 or width < 2:
        raise RasterWriteError(f"raster too small to tile: {width}x{height}")

    affine = transform if isinstance(transform, Affine) else Affine(*tuple(transform)[:6])
    partial = destination.with_suffix(destination.suffix + ".part")

    profile = {
        "driver": "COG",
        "dtype": data.dtype.name,
        "width": width,
        "height": height,
        "count": 1,
        "crs": CRS.from_epsg(epsg),
        "transform": affine,
        "nodata": nodata,
        "BLOCKSIZE": COG_BLOCK_SIZE,
        "COMPRESS": COG_COMPRESS,
        "OVERVIEW_RESAMPLING": overview_resampling.upper(),
        # Without this GDAL computes statistics on every read of a fresh file.
        "STATISTICS": "YES",
    }

    try:
        with rasterio.open(partial, "w", **profile) as handle:
            handle.write(data, 1)
            handle.update_tags(product=product)
    except Exception as exc:  # rasterio raises a wide range here
        partial.unlink(missing_ok=True)
        raise RasterWriteError(f"could not write {product} COG: {exc}") from exc

    partial.replace(destination)

    with rasterio.open(destination) as handle:
        bounds_4326 = bounds_4326_of(handle)
        overview_levels = handle.overviews(1)

    if not overview_levels and max(height, width) > COG_BLOCK_SIZE:
        # A single-resolution tiled GeoTIFF is not a COG: zooming out would make
        # the tiler read every full-resolution block to render one tile.
        raise RasterWriteError(
            f"{destination.name} has no overviews; it would be served by reading "
            "the full-resolution grid at every zoom level"
        )

    payload = destination.read_bytes()
    return RasterAsset(
        product=product,
        path=destination,
        epsg=epsg,
        resolution_m=abs(affine.a),
        width=width,
        height=height,
        dtype=data.dtype.name,
        nodata=nodata,
        bounds_4326=bounds_4326,
        size_bytes=len(payload),
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        stats=band_stats(data, nodata),
    )


def cache_key(*parts: object) -> str:
    """A stable content key for a derived raster.

    Hashed rather than concatenated so the key fits `dem_assets.cache_key`
    (64 chars) whatever goes into it, and so a float that renders differently
    across platforms cannot produce two keys for one raster.
    """
    joined = "|".join(f"{part!r}" for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def bounds_4326_of(handle: Any) -> tuple[float, float, float, float]:
    """The raster's extent in lon/lat, whatever CRS it is stored in.

    Public because `services.derivatives` needs it when describing a COG that is
    already on disk, and reaching across a module boundary into a private helper
    is worse than naming it.
    """
    from rasterio.warp import transform_bounds

    west, south, east, north = transform_bounds(handle.crs, CRS.from_epsg(4326), *handle.bounds)
    return (float(west), float(south), float(east), float(north))


def band_stats(data: npt.NDArray[Any], nodata: float | int) -> dict[str, float | int]:
    """Min/max/mean over the valid cells only. Public for the same reason.

    Including nodata would report an elevation of -9999 m, and a tiler asked to
    rescale on those statistics would render the whole raster one flat colour.
    """
    values = data.astype(np.float64)
    valid = np.isfinite(values) & (values != nodata)
    count = int(valid.sum())
    if not count:
        return {"valid_cells": 0}
    kept = values[valid]
    return {
        "valid_cells": count,
        "nodata_cells": int(values.size - count),
        "min": round(float(kept.min()), 4),
        "max": round(float(kept.max()), 4),
        "mean": round(float(kept.mean()), 4),
        "p2": round(float(np.percentile(kept, 2)), 4),
        "p98": round(float(np.percentile(kept, 98)), 4),
    }
