"""Slope and shaded relief as single images the map pins to the grid's corners.

The tiled rasters in `derivatives.py` need TiTiler, a separate service the lab
deployment does not run. Without it the two layers could still be switched on
in the legend, and drew nothing. One PNG per layer needs no tile server: the
grid is at most a few hundred cells a side, which is a small image, and MapLibre
places an image by its four corners.

The corners are the grid's own, taken from UTM to degrees. A UTM rectangle is
not a lon/lat rectangle, which is exactly why an image source takes four corners
rather than a bounding box; over a village the remaining error is far below a
cell.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Literal

import numpy as np

from app.providers.elevation.base import DemGrid
from app.services import raster
from app.services.hydrology import slope_percent

Product = Literal["hillshade", "slope"]
PRODUCTS: tuple[Product, ...] = ("hillshade", "slope")

#: Same fixed range as the tiled slope layer: 0-15 %, so a flat plateau does not
#: look as varied as a hillside, and 8 % (the siting threshold) sits mid-ramp.
SLOPE_RANGE_PCT = (0.0, 15.0)

#: Vertical exaggeration for the shaded relief. Indian plateau villages have a
#: few tens of metres of relief over kilometres, which at 1x renders as a flat
#: grey wash; 4x is what the workspace always asked the tiled layer for.
HILLSHADE_Z_FACTOR = 4.0

LEGEND: dict[Product, str] = {
    "hillshade": "Shaded relief, light from the north-west at 45 degrees, relief x4",
    "slope": "Slope, percent (0-15 % shown; siting rejects above 8 %)",
}


def corners_4326(dem: DemGrid) -> list[list[float]]:
    """The grid's outer corners in degrees: top-left, top-right, bottom-right,
    bottom-left -- the order MapLibre's image source expects."""
    from pyproj import Transformer

    min_x, min_y, max_x, max_y = dem.bounds_m
    to_deg = Transformer.from_crs(dem.epsg, 4326, always_xy=True)
    xs = [min_x, max_x, max_x, min_x]
    ys = [max_y, max_y, min_y, min_y]
    lons, lats = to_deg.transform(xs, ys)
    return [[round(float(lo), 7), round(float(la), 7)] for lo, la in zip(lons, lats, strict=True)]


def render(
    dem: DemGrid,
    product: Product,
    *,
    azimuth_deg: float = raster.DEFAULT_AZIMUTH_DEG,
    altitude_deg: float = raster.DEFAULT_ALTITUDE_DEG,
    z_factor: float = HILLSHADE_Z_FACTOR,
) -> bytes:
    """The layer as an RGBA PNG, transparent where the grid has no elevation."""
    from PIL import Image

    valid = np.isfinite(dem.elevation)
    rgba = np.zeros((*dem.shape, 4), dtype=np.uint8)
    if product == "hillshade":
        shade = raster.hillshade(
            dem.elevation,
            dem.cell_size_m,
            azimuth_deg=azimuth_deg,
            altitude_deg=altitude_deg,
            z_factor=z_factor,
        )
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = shade
        rgba[..., 3] = np.where(valid & (shade != 255), 255, 0)
    else:
        from matplotlib import colormaps

        lo, hi = SLOPE_RANGE_PCT
        slope = slope_percent(dem.elevation, dem.cell_size_m)
        scaled = np.clip((np.nan_to_num(slope, nan=0.0) - lo) / (hi - lo), 0.0, 1.0)
        colours = colormaps["magma"](scaled, bytes=True)
        rgba[..., :3] = colours[..., :3]
        rgba[..., 3] = np.where(valid & np.isfinite(slope), 255, 0)

    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def cached_render(
    dem_id: str, dem: DemGrid, product: Product, store: Path, **params: float
) -> bytes:
    """`render`, kept on disk by `dem_id` so a map pan never recomputes it.

    A `dem_id` names one grid for good, so its images never go stale; a write
    that fails costs only the next request a re-render.
    """
    tag = "-".join(f"{params[k]:g}" for k in sorted(params))
    path = store / "overlays" / f"{dem_id}-{product}{'-' + tag if tag else ''}.png"
    if path.is_file():
        return path.read_bytes()
    png = render(dem, product, **params)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(png)
        tmp.replace(path)
    except OSError:
        pass
    return png


def describe(dem_id: str, dem: DemGrid) -> list[dict[str, Any]]:
    """What the map needs for each layer: where the image is and where it goes."""
    corners = corners_4326(dem)
    return [
        {
            "product": product,
            "url": f"/api/v1/terrain/{dem_id}/overlay/{product}",
            "coordinates": corners,
            "legend": LEGEND[product],
            "resolution_m": dem.cell_size_m,
            "size_px": [dem.shape[1], dem.shape[0]],
        }
        for product in PRODUCTS
    ]
