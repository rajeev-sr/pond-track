"""Turn an interpolated DEM into browser-servable raster layers (M2-3, M2-4).

Composes three things that already exist -- the DEM from `services.interpolate`,
Horn slope from `services.hydrology`, hillshade and COG writing from
`services.raster` -- into the set of layers a map needs, and records each one in
`dem_assets` so the same DEM is never rasterised twice.

The cache key is a hash of the **elevation grid itself** plus the parameters that
shaped it, not of the upload or a request id. Re-uploading the same contour file
therefore reuses the rasters rather than writing a second identical copy under a
new random name, and a stored analysis can be replayed against byte-identical
layers (HLD NFR-13).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import raster
from app.services.hydrology import slope_percent

log = logging.getLogger(__name__)

ALL_PRODUCTS: tuple[raster.Product, ...] = ("dem", "slope", "hillshade")

#: TiTiler's XYZ tile route, as served through nginx at `/tiles/`. The tile
#: matrix set is explicit because 0.19 requires it in the path.
TILE_ROUTE = "/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png"

#: How each layer should be coloured. `rescale` is filled per raster from its own
#: percentiles, because a fixed elevation range would render one flat colour on a
#: 30 m-relief plateau and clip a hill.
COLOURING: dict[raster.Product, dict[str, Any]] = {
    "dem": {
        "colormap_name": "terrain",
        "rescale_from": ("p2", "p98"),
        "legend": "Elevation, metres above sea level",
    },
    "slope": {
        "colormap_name": "magma",
        # Fixed 0-15 %: the interesting range for pond siting is 0-8 % (the
        # buildability threshold), and rescaling per-raster would make a flat
        # plateau look as varied as a hillside.
        "rescale": (0.0, 15.0),
        "legend": "Slope, percent (0-15 % shown; siting rejects above 8 %)",
    },
    "hillshade": {
        # No colormap: the band is already the grey value.
        "rescale": (0.0, 254.0),
        "legend": "Shaded relief, light from the north-west at 45 degrees",
    },
}


@dataclass(frozen=True)
class Layer:
    """One raster layer, ready for a map."""

    product: raster.Product
    asset: raster.RasterAsset
    tile_url_template: str
    legend: str
    reused: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "tile_url_template": self.tile_url_template,
            "legend": self.legend,
            "min_zoom": 0,
            "max_zoom": 20,
            "tile_size": 256,
            # True when the raster was already on disk from an earlier request
            # for the same DEM -- useful for telling a slow first call from a
            # slow pipeline.
            "reused": self.reused,
            "raster": self.asset.as_dict(),
        }


def build(
    session: Session | None,
    *,
    elevation: npt.NDArray[np.floating],
    transform: tuple[float, ...],
    epsg: int,
    cell_size_m: float,
    store: Path,
    products: tuple[raster.Product, ...] = ALL_PRODUCTS,
    hillshade_azimuth_deg: float = raster.DEFAULT_AZIMUTH_DEG,
    hillshade_altitude_deg: float = raster.DEFAULT_ALTITUDE_DEG,
    hillshade_z_factor: float = raster.DEFAULT_Z_FACTOR,
) -> list[Layer]:
    """Write the requested layers as COGs and return how to serve them.

    `session` may be None: the rasters are useful without a database, and the
    contour endpoints deliberately work with no database at all. When a session
    is given, each asset is recorded in `dem_assets`.
    """
    surface = np.where(np.isfinite(elevation), elevation, np.nan)
    grid_key = raster.cache_key(
        np.ascontiguousarray(surface, dtype=np.float32).tobytes(),
        tuple(round(v, 6) for v in tuple(transform)[:6]),
        epsg,
        round(cell_size_m, 4),
    )

    layers: list[Layer] = []
    for product in products:
        if product == "hillshade":
            key = raster.cache_key(
                grid_key,
                product,
                round(hillshade_azimuth_deg, 2),
                round(hillshade_altitude_deg, 2),
                round(hillshade_z_factor, 3),
            )
        else:
            key = raster.cache_key(grid_key, product)

        # Content-addressed layout: the first two hex characters fan the files
        # out across directories, so a store with tens of thousands of rasters
        # does not put them all in one directory.
        path = store / key[:2] / f"{key}-{product}.tif"

        if path.exists():
            asset = _describe_existing(path, product)
            reused = True
        else:
            asset = _write(
                product,
                path,
                surface=surface,
                transform=transform,
                epsg=epsg,
                cell_size_m=cell_size_m,
                azimuth_deg=hillshade_azimuth_deg,
                altitude_deg=hillshade_altitude_deg,
                z_factor=hillshade_z_factor,
            )
            reused = False

        if session is not None:
            _record(session, key, asset)

        layers.append(
            Layer(
                product=product,
                asset=asset,
                tile_url_template=_tile_url(asset),
                legend=str(COLOURING[product]["legend"]),
                reused=reused,
            )
        )
    return layers


def _write(
    product: raster.Product,
    path: Path,
    *,
    surface: npt.NDArray[np.floating],
    transform: tuple[float, ...],
    epsg: int,
    cell_size_m: float,
    azimuth_deg: float,
    altitude_deg: float,
    z_factor: float,
) -> raster.RasterAsset:
    band: npt.NDArray[Any]
    if product == "dem":
        band = np.where(np.isfinite(surface), surface, raster.NODATA_FLOAT).astype(np.float32)
        nodata: float | int = raster.NODATA_FLOAT
        resampling = "average"
    elif product == "slope":
        # The *original* surface, not a depression-filled one: buildability is a
        # property of the ground, and a filled hollow reads as 0 % slope exactly
        # where a pond would go.
        computed = slope_percent(surface, cell_size_m)
        band = np.where(np.isfinite(computed), computed, raster.NODATA_FLOAT).astype(np.float32)
        nodata = raster.NODATA_FLOAT
        resampling = "average"
    else:
        band = raster.hillshade(
            surface,
            cell_size_m,
            azimuth_deg=azimuth_deg,
            altitude_deg=altitude_deg,
            z_factor=z_factor,
        )
        nodata = raster.NODATA_HILLSHADE
        # Nearest for hillshade: averaging a shaded value with the 255 nodata
        # sentinel would paint a bright halo along every edge in the overviews.
        resampling = "nearest"

    return raster.write_cog(
        path,
        band,
        transform=transform,
        epsg=epsg,
        nodata=nodata,
        product=product,
        overview_resampling=resampling,
    )


def _describe_existing(path: Path, product: raster.Product) -> raster.RasterAsset:
    """Read back an already-written COG without recomputing the band."""
    import rasterio

    with rasterio.open(path) as handle:
        band = handle.read(1)
        nodata = handle.nodata
        bounds = raster.bounds_4326_of(handle)
        resolution = abs(handle.transform.a)
        width, height = handle.width, handle.height
        dtype = handle.dtypes[0]
        epsg = handle.crs.to_epsg()

    payload = path.read_bytes()
    return raster.RasterAsset(
        product=product,
        path=path,
        epsg=int(epsg or 0),
        resolution_m=float(resolution),
        width=int(width),
        height=int(height),
        dtype=str(dtype),
        nodata=nodata if nodata is not None else raster.NODATA_FLOAT,
        bounds_4326=bounds,
        size_bytes=len(payload),
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        stats=raster.band_stats(band, nodata if nodata is not None else raster.NODATA_FLOAT),
    )


def tiler_path(path: Path | str) -> str:
    """`path` as TiTiler sees it.

    A tile URL carries `?url=<the COG>` and TiTiler opens that path with its own
    filesystem, so the path has to be TiTiler's. Ours is the same string only
    when the API is a container too: run the API on the host and it writes
    `<repo>/data/cache/x.tif` while TiTiler serves the identical bytes from
    `/data/cache/x.tif`. Passing our path made every tile 500 with "No such file
    or directory" while the API itself reported success -- the layers were simply
    blank, which is the worst way for this to fail.

    So the `COG_STORE_PATH` prefix is rewritten to `TILER_STORE_PATH`. Equal
    values are a no-op, which is the container case. A path outside the store is
    returned untouched: a remote `/vsicurl` URL is already something TiTiler can
    open, and inventing a prefix for it would break it.
    """
    from app.config import get_settings

    settings = get_settings()
    ours = Path(settings.COG_STORE_PATH).resolve()
    theirs = str(settings.TILER_STORE_PATH).rstrip("/")
    if not theirs or str(ours) == theirs:
        return str(path)
    try:
        relative = Path(path).resolve().relative_to(ours)
    except ValueError:
        return str(path)
    return f"{theirs}/{relative.as_posix()}"


def _tile_url(asset: raster.RasterAsset) -> str:
    """A tile template the browser can use directly.

    The braces are left unexpanded for the map client to fill.
    """
    colouring = COLOURING[asset.product]
    params = [f"url={tiler_path(asset.path)}"]

    rescale = colouring.get("rescale")
    if rescale is None:
        low_key, high_key = colouring["rescale_from"]
        low = asset.stats.get(low_key)
        high = asset.stats.get(high_key)
        # A raster with no valid cells, or one perfectly flat surface, has no
        # range to stretch; sending `rescale=x,x` makes TiTiler divide by zero.
        if low is None or high is None or float(high) - float(low) < 1e-6:
            low, high = 0.0, 1.0
        rescale = (float(low), float(high))
    params.append(f"rescale={rescale[0]:g},{rescale[1]:g}")

    colormap = colouring.get("colormap_name")
    if colormap:
        params.append(f"colormap_name={colormap}")

    return f"{TILE_ROUTE}?{'&'.join(params)}"


def _record(session: Session, key: str, asset: raster.RasterAsset) -> None:
    """Upsert the asset into `dem_assets`, keyed on its content hash."""
    west, south, east, north = asset.bounds_4326
    session.execute(
        text(
            """
            INSERT INTO dem_assets
                (id, cache_key, product, source, bbox, epsg, resolution_m,
                 width_px, height_px, file_path, size_bytes, checksum_sha256, stats)
            VALUES (gen_random_uuid(), :cache_key, :product, :source,
                    ST_MakeEnvelope(:west, :south, :east, :north, 4326),
                    :epsg, :resolution_m, :width, :height, :file_path,
                    :size_bytes, :checksum, CAST(:stats AS jsonb))
            ON CONFLICT (cache_key) DO UPDATE SET
                file_path       = EXCLUDED.file_path,
                size_bytes      = EXCLUDED.size_bytes,
                checksum_sha256 = EXCLUDED.checksum_sha256,
                stats           = EXCLUDED.stats
            """
        ),
        {
            "cache_key": key,
            "product": asset.product,
            "source": "contour_map",
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "epsg": asset.epsg,
            "resolution_m": asset.resolution_m,
            "width": asset.width,
            "height": asset.height,
            "file_path": str(asset.path),
            "size_bytes": asset.size_bytes,
            "checksum": asset.checksum_sha256,
            "stats": json.dumps(asset.stats),
        },
    )
    session.commit()
