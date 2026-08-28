"""Raster and vector geometry helpers for API responses.

Everything leaving the API is GeoJSON in EPSG:4326 (HLD ADR-5), while every
metric quantity is computed in the projected working CRS. These helpers are the
boundary between the two, so the conversion happens in one place instead of
being repeated at each endpoint.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
from pyproj import Transformer
from rasterio.features import shapes as raster_shapes
from rasterio.transform import Affine
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from app.providers.elevation.base import DemGrid

#: Douglas-Peucker tolerance as a multiple of the cell size. A catchment boundary
#: traced cell-by-cell is a staircase of thousands of vertices; simplifying at
#: half a cell removes the staircase without moving the boundary anywhere a
#: reader would notice.
SIMPLIFY_CELLS = 0.5


def _to_wgs84(epsg: int) -> Any:
    tf = Transformer.from_crs(epsg, 4326, always_xy=True)
    return lambda x, y: tf.transform(x, y)


def mask_to_geojson(
    mask: npt.NDArray[np.bool_],
    dem: DemGrid,
    *,
    simplify_cells: float = SIMPLIFY_CELLS,
) -> dict[str, Any] | None:
    """Vectorise a boolean raster mask to a GeoJSON geometry in EPSG:4326.

    Returns None for an empty mask. Disjoint parts are merged into a single
    MultiPolygon so callers always get one geometry per catchment.
    """
    if not mask.any():
        return None

    a, b, c, d, e, f = dem.transform
    affine = Affine(a, b, c, d, e, f)
    geoms = [
        shape(geom)
        for geom, value in raster_shapes(mask.astype(np.uint8), mask=mask, transform=affine)
        if value == 1
    ]
    if not geoms:
        return None

    merged = unary_union(geoms)
    tol = simplify_cells * dem.cell_size_m
    if tol > 0:
        # preserve_topology keeps the ring valid; without it a thin neck can
        # collapse and split the catchment into pieces.
        merged = merged.simplify(tol, preserve_topology=True)
    return dict(mapping(shapely_transform(_to_wgs84(dem.epsg), merged)))


def point_geojson(lon: float, lat: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}


def contours_to_geojson(
    lines: list[Any],
    *,
    simplify_deg: float = 0.0,
    max_features: int | None = None,
) -> dict[str, Any]:
    """Parsed contour lines -> a GeoJSON FeatureCollection, for overlay and for
    verifying that the parse read the file the way the caller expects.

    Coordinates are already EPSG:4326 as parsed, so no reprojection is needed.
    """
    features: list[dict[str, Any]] = []
    ordered = sorted(lines, key=lambda ln: ln.elevation_m)
    if max_features is not None:
        ordered = ordered[:max_features]
    for i, ln in enumerate(ordered):
        geom: Any = LineString(ln.coords)
        if simplify_deg > 0:
            geom = geom.simplify(simplify_deg, preserve_topology=False)
        features.append(
            {
                "type": "Feature",
                "id": i,
                "geometry": dict(mapping(geom)),
                "properties": {
                    "elevation_m": ln.elevation_m,
                    "vertices": ln.vertex_count,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def bbox_geojson(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }


def geometry_area_ha(geom: dict[str, Any], epsg: int) -> float:
    """Area of a WGS84 geometry, measured in a projected CRS.

    Exists so a response can state the area of the polygon it actually returns,
    which after simplification differs slightly from the raster cell count. Never
    computes area in degrees (HLD CH-10).
    """
    tf = Transformer.from_crs(4326, epsg, always_xy=True)
    projected = shapely_transform(lambda x, y: tf.transform(x, y), shape(geom))
    return float(projected.area) / 10_000.0


def contour_lines_to_geojson(lines: Any, epsg: int) -> dict[str, Any]:
    """Generated contour lines as a GeoJSON FeatureCollection in EPSG:4326.

    Each feature carries `elevation_m` and `is_index`, which is what a map needs
    to draw index contours thicker and label only those -- labelling all forty
    levels of a 1 m map produces an unreadable mat of text.
    """
    to_wgs84 = _to_wgs84(epsg)
    features = []
    for line in lines:
        xs, ys = zip(*line.coordinates, strict=True)
        # `_to_wgs84` returns a callable, not a pyproj Transformer.
        lons, lats = to_wgs84(xs, ys)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(float(lon), 7), round(float(lat), 7)]
                        for lon, lat in zip(lons, lats, strict=True)
                    ],
                },
                "properties": {
                    "elevation_m": round(float(line.elevation_m), 3),
                    "is_index": bool(line.is_index),
                    "length_m": round(float(line.length_m), 1),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def reaches_to_geojson(reaches: Any, epsg: int) -> dict[str, Any]:
    """Stream reaches as a GeoJSON FeatureCollection in EPSG:4326.

    Each feature carries `strahler_order`, so a map can widen the line with the
    order -- which is the whole point of computing it. A first-order headwater and
    a fourth-order trunk drawn identically tell the reader nothing about which one
    a pond can be built across.
    """
    to_wgs84 = _to_wgs84(epsg)
    features = []
    for reach in reaches:
        xs, ys = zip(*reach.coordinates, strict=True)
        lons, lats = to_wgs84(xs, ys)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(float(lon), 7), round(float(lat), 7)]
                        for lon, lat in zip(lons, lats, strict=True)
                    ],
                },
                "properties": {
                    "strahler_order": int(reach.order),
                    "length_m": round(float(reach.length_m), 1),
                    "upstream_area_ha": round(float(reach.upstream_area_ha), 3),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
