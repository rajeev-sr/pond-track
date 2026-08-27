"""Land use / land cover from ESA WorldCover 10 m (HLD §4.2 C2).

Read straight from the AWS Open Data bucket as range-request COGs -- the same
pattern as the DEM (§4.2 A1): no key, no quota, and a windowed read fetches only
the AOI. A 3 x 3 km area is ~100 KB rather than a 36000 x 36000 tile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

from app.providers.base import Provenance, ProviderUnavailableError

PROVIDER = "esa_worldcover"
BUCKET = "https://esa-worldcover.s3.amazonaws.com"
VERSION, YEAR = "v200", "2021"
TILE_DEGREES = 3
PROVENANCE = Provenance(
    provider="ESA WorldCover",
    dataset=f"ESA WorldCover {YEAR} {VERSION}",
    resolution="10 m",
    licence="CC-BY 4.0",
)

#: WorldCover class codes and names.
LEGEND: dict[int, str] = {
    10: "tree_cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built_up",
    60: "bare_sparse_vegetation",
    70: "snow_and_ice",
    80: "permanent_water",
    90: "herbaceous_wetland",
    95: "mangroves",
    100: "moss_and_lichen",
}

#: Whether land of each class could plausibly host an excavated pond. Used for
#: the land-availability criterion, and to keep a recommendation off a lake or a
#: village (HLD §6.4). Cropland is *possible* but usually privately held, so it
#: scores low rather than zero.
AVAILABILITY: dict[int, float] = {
    60: 1.00,  # bare / sparse -- the classic wasteland allotment
    30: 0.85,  # grassland / grazing common
    20: 0.70,  # shrubland
    40: 0.30,  # cropland: possible, but usually private
    100: 0.30,
    90: 0.10,  # wetland: ecologically sensitive
    10: 0.05,  # tree cover: clearing forest is not an option
    95: 0.00,
    50: 0.00,  # built-up
    80: 0.00,  # already water
    70: 0.00,
}


@dataclass(frozen=True)
class LandCover:
    """Land cover resampled onto the analysis grid."""

    codes: npt.NDArray[np.uint8]  # aligned with the DEM grid
    fractions: dict[str, float]  # class name -> fraction of valid cells
    dominant_class: str
    tiles_used: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dominant_class": self.dominant_class,
            "class_fractions_pct": {
                k: round(100.0 * v, 2)
                for k, v in sorted(self.fractions.items(), key=lambda kv: -kv[1])
            },
            "tiles_used": self.tiles_used,
            "source": PROVENANCE.as_dict(),
        }

    def fractions_within(self, mask: npt.NDArray[np.bool_]) -> dict[str, float]:
        """Class fractions inside a sub-area, e.g. a delineated catchment."""
        sel = self.codes[mask]
        sel = sel[sel > 0]
        if sel.size == 0:
            return {}
        codes, counts = np.unique(sel, return_counts=True)
        return {
            LEGEND.get(int(c), f"class_{int(c)}"): float(n) / float(sel.size)
            for c, n in zip(codes.tolist(), counts.tolist(), strict=True)
        }


def tile_name(lat: float, lon: float) -> str:
    """WorldCover tiles are 3 x 3 degrees, named by their south-west corner."""
    tl = int(np.floor(lat / TILE_DEGREES) * TILE_DEGREES)
    tn = int(np.floor(lon / TILE_DEGREES) * TILE_DEGREES)
    ns, ew = ("N" if tl >= 0 else "S"), ("E" if tn >= 0 else "W")
    return f"ESA_WorldCover_10m_{YEAR}_{VERSION}_{ns}{abs(tl):02d}{ew}{abs(tn):03d}_Map"


def tile_url(name: str) -> str:
    return f"{BUCKET}/{VERSION}/{YEAR}/map/{name}.tif"


def tiles_covering(bounds: tuple[float, float, float, float]) -> list[str]:
    min_lon, min_lat, max_lon, max_lat = bounds
    names: list[str] = []
    lat = np.floor(min_lat / TILE_DEGREES) * TILE_DEGREES
    while lat <= max_lat:
        lon = np.floor(min_lon / TILE_DEGREES) * TILE_DEGREES
        while lon <= max_lon:
            names.append(tile_name(float(lat), float(lon)))
            lon += TILE_DEGREES
        lat += TILE_DEGREES
    return names


def fetch_landcover(
    bounds: tuple[float, float, float, float],
    dst_shape: tuple[int, int],
    dst_transform: tuple[float, ...],
    dst_epsg: int,
) -> LandCover:
    """Read WorldCover over `bounds` and resample onto the analysis grid.

    Nearest-neighbour resampling: the values are *class codes*, so averaging them
    would invent categories that do not exist (the mean of cropland 40 and
    built-up 50 is not a land-cover class).
    """
    names = tiles_covering(bounds)
    dst = np.zeros(dst_shape, dtype=np.uint8)
    used: list[str] = []
    errors: list[str] = []

    for name in names:
        try:
            with rasterio.open(tile_url(name)) as src:
                window = from_bounds(*bounds, src.transform)
                block = src.read(1, window=window, boundless=True, fill_value=0)
                if block.size == 0 or not block.any():
                    continue
                out = np.zeros(dst_shape, dtype=np.uint8)
                reproject(
                    source=block,
                    destination=out,
                    src_transform=src.window_transform(window),
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=f"EPSG:{dst_epsg}",
                    resampling=Resampling.nearest,
                    src_nodata=0,
                    dst_nodata=0,
                )
                dst = np.where(dst == 0, out, dst)
                used.append(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")

    if not used:
        raise ProviderUnavailableError(
            PROVIDER,
            "no WorldCover tile could be read for this area"
            + (f" ({'; '.join(errors)})" if errors else ""),
        )

    valid = dst[dst > 0]
    if valid.size == 0:
        raise ProviderUnavailableError(PROVIDER, "tiles read but contained no data")
    codes, counts = np.unique(valid, return_counts=True)
    fractions = {
        LEGEND.get(int(c), f"class_{int(c)}"): float(n) / float(valid.size)
        for c, n in zip(codes.tolist(), counts.tolist(), strict=True)
    }
    dominant = max(fractions.items(), key=lambda kv: kv[1])[0]
    return LandCover(codes=dst, fractions=fractions, dominant_class=dominant, tiles_used=used)
