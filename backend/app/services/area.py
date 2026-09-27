"""A rectangle drawn on the map, as an area to analyse.

The second way into the pipeline, beside an uploaded contour map. The rectangle
is validated here, measured on the ellipsoid, and turned into a mask on the
analysis grid so siting can be held inside it.

Rectangles only, by decision: the UI draws a box by dragging, and a box is what a
caller can type into an API tester without building GeoJSON by hand.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.providers.elevation.base import Bounds, DemGrid
from app.services.geometry import bbox_geojson

#: Below this there is no room for a catchment worth the name: 0.1 km² is ten
#: hectares, about 110 cells of the 30 m terrain it will be analysed on.
MIN_AREA_KM2 = 0.1


class AreaError(ValueError):
    """The rectangle cannot be analysed as sent. Maps to HTTP 400."""


class AreaTooLargeError(AreaError):
    """The rectangle is larger than the configured cap. Maps to HTTP 413."""

    def __init__(self, area_km2: float, max_km2: float) -> None:
        super().__init__(
            f"the selected area is {area_km2:,.1f} km², over the {max_km2:g} km² limit. "
            "Draw a smaller rectangle."
        )
        self.area_km2 = area_km2
        self.max_km2 = max_km2


@dataclass(frozen=True)
class AnalysisArea:
    """A validated rectangle in WGS84 degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    area_km2: float

    @property
    def bounds(self) -> Bounds:
        return Bounds(self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def as_geojson(self) -> dict[str, Any]:
        return bbox_geojson(self.min_lon, self.min_lat, self.max_lon, self.max_lat)


def geodesic_area_km2(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> float:
    """Area of a lon/lat rectangle on the WGS84 ellipsoid, in km².

    Measured on the ellipsoid rather than from degrees: a degree of longitude
    shrinks with latitude, so the same box is a quarter smaller at 40° than at
    the equator, and the cap has to mean the same thing everywhere.
    """
    from pyproj import Geod

    lons = [min_lon, max_lon, max_lon, min_lon, min_lon]
    lats = [min_lat, min_lat, max_lat, max_lat, min_lat]
    area_m2, _perimeter = Geod(ellps="WGS84").polygon_area_perimeter(lons, lats)
    return abs(float(area_m2)) / 1_000_000.0


def validate_bbox(
    bbox: Sequence[Any],
    *,
    max_km2: float,
    min_km2: float = MIN_AREA_KM2,
) -> AnalysisArea:
    """`[min_lon, min_lat, max_lon, max_lat]` as an analysable area, or an error
    that says exactly what is wrong with it."""
    if isinstance(bbox, str | bytes) or len(bbox) != 4:
        raise AreaError(
            "bbox must be four numbers: [min_lon, min_lat, max_lon, max_lat] in degrees."
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise AreaError(f"bbox values must be numbers: {exc}") from exc
    if not all(math.isfinite(v) for v in (min_lon, min_lat, max_lon, max_lat)):
        raise AreaError("bbox values must be finite numbers.")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise AreaError("longitudes must lie between -180 and 180.")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise AreaError("latitudes must lie between -90 and 90.")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise AreaError(
            "bbox is inverted or has no size: it needs min_lon < max_lon and "
            "min_lat < max_lat, in the order [min_lon, min_lat, max_lon, max_lat]."
        )

    area = geodesic_area_km2(min_lon, min_lat, max_lon, max_lat)
    if area < min_km2:
        raise AreaError(
            f"the selected area is {area:.3f} km², below the {min_km2:g} km² minimum — "
            "too small to hold a catchment. Draw a larger rectangle."
        )
    if area > max_km2:
        raise AreaTooLargeError(area, max_km2)
    return AnalysisArea(min_lon, min_lat, max_lon, max_lat, round(area, 4))


def inside_mask(dem: DemGrid, area: AnalysisArea) -> npt.NDArray[np.bool_]:
    """Cells whose centre lies inside the rectangle.

    Tested in degrees, not metres: a lon/lat rectangle is not a rectangle in UTM
    (its sides curve and lean slightly), so projecting its corners and filling
    between them would misplace the edge by up to a cell on a large area.
    """
    from pyproj import Transformer

    rows, cols = dem.shape
    a, _b, c, _d, e, f = dem.transform
    xs = c + a * (np.arange(cols, dtype=np.float64) + 0.5)
    ys = f + e * (np.arange(rows, dtype=np.float64) + 0.5)
    grid_x, grid_y = np.meshgrid(xs, ys)
    lon, lat = Transformer.from_crs(dem.epsg, 4326, always_xy=True).transform(grid_x, grid_y)
    return np.asarray(
        (lon >= area.min_lon)
        & (lon <= area.max_lon)
        & (lat >= area.min_lat)
        & (lat <= area.max_lat),
        dtype=bool,
    )


def grid_bounds_4326(dem: DemGrid) -> Bounds:
    """The whole analysis grid in degrees — the drawn area plus its buffer.

    Enrichment layers are laid onto every cell of the grid, and catchments reach
    into the buffer, so land cover and OSM features have to cover the buffer too,
    not only the rectangle the user drew.
    """
    from pyproj import Transformer

    min_x, min_y, max_x, max_y = dem.bounds_m
    to_deg = Transformer.from_crs(dem.epsg, 4326, always_xy=True)
    xs = [min_x, max_x, max_x, min_x]
    ys = [min_y, min_y, max_y, max_y]
    lons, lats = to_deg.transform(xs, ys)
    return Bounds(float(min(lons)), float(min(lats)), float(max(lons)), float(max(lats)))


def terrain_failure(exc: BaseException) -> tuple[str, str] | None:
    """A failed terrain fetch in words a person can act on, or None for any other
    failure. Returns `(kind, detail)`, kind being `no_terrain` (open sea: draw
    elsewhere) or `unavailable` (Copernicus unreachable: try again). Shared by the
    synchronous route and the job runner, so both say the same thing."""
    from app.providers.base import ProviderUnavailableError
    from app.providers.elevation.copernicus_aws import NoTerrainError

    if isinstance(exc, NoTerrainError):
        return (
            "no_terrain",
            f"there is no terrain to analyse here: {exc.detail}. Draw the rectangle over land.",
        )
    if isinstance(exc, ProviderUnavailableError):
        return (
            "unavailable",
            "terrain for this area could not be fetched from Copernicus GLO-30 "
            f"({exc.detail}). Try again shortly, or upload a contour map instead.",
        )
    return None
