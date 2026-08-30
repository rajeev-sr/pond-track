"""Available-land identification (HLD §6.4, FR-3 -- M5-2..M5-5).

Terrain says where water collects; this says where you are actually allowed to
dig. The two are independent, and conflating them is how a tool ends up
recommending the middle of a village.

The pipeline is the HLD's, in order: build an exclusion mask, build an inclusion
mask, intersect, clean the speckle morphologically, split into connected
parcels, then vectorise with the attributes a planner would ask for.

Two deliberate departures from `services/siting.py`, both because the questions
differ:

* **Slope.** This module defaults to 5 %, siting to 8 %. Siting asks "could a
  pond work here at all"; FR-3 asks "is this parcel worth surveying", and steep
  ground is ruled out by excavation cost long before it is ruled out by
  physics. The HLD fixes 5 % for this step specifically.
* **OSM is additive.** A missing building in OSM is not evidence of open
  ground, so OSM features only ever *remove* land here. They never rescue a
  cell the land-cover mask rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from app.core.logging import get_logger
from app.providers.elevation.base import DemGrid
from app.providers.landcover.worldcover import LEGEND
from app.providers.vector.overpass import OsmContext, OsmFeature
from app.services.geometry import mask_to_geojson

log = get_logger("services.land")

#: Exclusion buffers, in metres (HLD §6.4 Step 1). Tracks are not in the HLD's
#: list: it buffers "roads/railways" by 20 m, which applied to every field path
#: OSM knows about would exclude most of a village's farmland. A cart track is
#: an access route to a pond, not an obstruction, so it gets a token buffer.
BUFFER_M: dict[str, float] = {
    "building": 50.0,
    "road": 20.0,
    "track": 5.0,
    "water": 100.0,
    "landuse": 0.0,
}

#: Slope above which excavation cost rules the ground out (HLD §6.4 Step 1).
DEFAULT_MAX_SLOPE_PCT = 5.0

#: Smaller than this and it is not a pond site, it is a puddle (HLD Step 5).
DEFAULT_MIN_AREA_M2 = 400.0

#: Ellipse, 5x5, per HLD Step 4. Open first to drop speckle, then close to fill
#: the pinholes opening leaves behind.
MORPH_KERNEL_CELLS = 5

#: WorldCover codes no pond can be dug on, whatever the terrain says.
EXCLUDED_CODES = frozenset({10, 50, 70, 80, 95})

#: WorldCover codes that are candidate ground.
INCLUDED_CODES = frozenset({20, 30, 60})

#: Cropland is possible but usually privately held, so it is opt-in (HLD Step 2).
CROPLAND_CODE = 40


@dataclass(frozen=True)
class Parcel:
    """One connected patch of buildable land, with the attributes FR-3 asks for."""

    parcel_id: int
    area_m2: float
    area_ha: float
    centroid_lonlat: tuple[float, float]
    mean_slope_pct: float
    max_slope_pct: float
    dominant_land_cover: str
    hydrologic_soil_group: str | None
    distance_to_road_m: float | None
    distance_to_settlement_m: float | None
    mean_flow_accumulation_cells: float | None
    geometry: dict[str, Any] | None

    def as_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.parcel_id,
            "geometry": self.geometry,
            "properties": {
                "parcel_id": self.parcel_id,
                "area_m2": round(self.area_m2, 1),
                "area_ha": round(self.area_ha, 4),
                "mean_slope_pct": round(self.mean_slope_pct, 2),
                "max_slope_pct": round(self.max_slope_pct, 2),
                "dominant_land_cover": self.dominant_land_cover,
                "hydrologic_soil_group": self.hydrologic_soil_group,
                "distance_to_road_m": (
                    None if self.distance_to_road_m is None else round(self.distance_to_road_m, 1)
                ),
                "distance_to_settlement_m": (
                    None
                    if self.distance_to_settlement_m is None
                    else round(self.distance_to_settlement_m, 1)
                ),
                "mean_flow_accumulation_cells": (
                    None
                    if self.mean_flow_accumulation_cells is None
                    else round(self.mean_flow_accumulation_cells, 1)
                ),
                # FR-11 territory: without an uploaded cadastral layer there is
                # no honest answer, and guessing tenure would be worse than
                # admitting it.
                "ownership": None,
            },
        }


@dataclass(frozen=True)
class LandAvailability:
    """The mask, the parcels, and an audit of what removed what."""

    available: npt.NDArray[np.bool_]
    parcels: tuple[Parcel, ...]
    #: Cell counts, so a caller can see which rule did the work.
    removed_by: dict[str, int]
    total_available_m2: float
    considered_cells: int
    osm_used: bool
    cropland_allowed: bool
    max_slope_pct: float
    min_area_m2: float
    dropped_below_min_area: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "parcel_count": len(self.parcels),
            "total_available_ha": round(self.total_available_m2 / 10_000.0, 4),
            "criteria": {
                "max_slope_pct": self.max_slope_pct,
                "min_parcel_area_m2": self.min_area_m2,
                "cropland_allowed": self.cropland_allowed,
                "osm_exclusions_applied": self.osm_used,
            },
            "removed_by": self.removed_by,
            "parcels_dropped_below_min_area": self.dropped_below_min_area,
            "considered_cells": self.considered_cells,
        }

    def feature_collection(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": [p.as_feature() for p in self.parcels if p.geometry is not None],
        }


def _projected_geometries(features: list[OsmFeature], epsg: int) -> list[tuple[BaseGeometry, str]]:
    """OSM rings reprojected from lon/lat into the DEM's metric CRS.

    Buffering has to happen in metres, so this must precede it. An invalid ring
    (OSM areas self-intersect more often than one would hope) is repaired with a
    zero-width buffer, and anything still unusable is dropped rather than
    allowed to poison the whole rasterise call.
    """
    if not features:
        return []
    tf = Transformer.from_crs(4326, epsg, always_xy=True)
    out: list[tuple[BaseGeometry, str]] = []
    for feature in features:
        for ring in feature.rings:
            if len(ring) < 2:
                continue
            xs, ys = tf.transform([p[0] for p in ring], [p[1] for p in ring])
            coords = [(float(x), float(y)) for x, y in zip(xs, ys, strict=True)]
            coords = [c for c in coords if np.isfinite(c[0]) and np.isfinite(c[1])]
            if len(coords) < 2:
                continue
            geom: BaseGeometry
            if feature.is_area and len(coords) >= 4:
                try:
                    geom = Polygon(coords)
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                except Exception:  # a bad ring is data, not a bug
                    geom = LineString(coords)
            else:
                geom = LineString(coords)
            if geom.is_empty:
                continue
            out.append((geom, feature.kind))
    return out


def osm_exclusion_mask(
    context: OsmContext,
    dem: DemGrid,
    *,
    buffers_m: dict[str, float] | None = None,
) -> tuple[npt.NDArray[np.bool_], dict[str, int]]:
    """Rasterise buffered OSM features onto the analysis grid.

    Returns the union mask and a per-class cell count, so the response can say
    that the water buffer removed 4 ha rather than only that something did.
    """
    buffers = dict(BUFFER_M if buffers_m is None else buffers_m)
    a, b, c, d, e, f = dem.transform
    affine = Affine(a, b, c, d, e, f)
    rows, cols = dem.shape

    by_kind: dict[str, list[OsmFeature]] = {
        "building": context.buildings,
        "road": context.roads,
        "track": context.tracks,
        "water": context.water,
        "landuse": context.landuse,
    }

    union = np.zeros((rows, cols), dtype=bool)
    counts: dict[str, int] = {}
    for kind, features in by_kind.items():
        geoms = [g for g, _ in _projected_geometries(features, dem.epsg)]
        if not geoms:
            counts[kind] = 0
            continue
        distance = buffers.get(kind, 0.0)
        shapes: list[BaseGeometry] = []
        for geom in geoms:
            buffered = geom.buffer(distance) if distance > 0 else geom
            if not buffered.is_empty:
                shapes.append(buffered)
        if not shapes:
            counts[kind] = 0
            continue
        burned = rasterize(
            [(g, 1) for g in shapes],
            out_shape=(rows, cols),
            transform=affine,
            fill=0,
            all_touched=True,
            dtype="uint8",
        ).astype(bool)
        counts[kind] = int(burned.sum())
        union |= burned

    return union, counts


def lulc_masks(
    codes: npt.NDArray[np.uint8], *, allow_cropland: bool = False
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.bool_]]:
    """(included, excluded) land-cover masks per HLD §6.4 Steps 1-2."""
    included_codes = set(INCLUDED_CODES)
    if allow_cropland:
        included_codes.add(CROPLAND_CODE)
    included = np.isin(codes, sorted(included_codes))
    excluded = np.isin(codes, sorted(EXCLUDED_CODES))
    return included, excluded


def clean(
    mask: npt.NDArray[np.bool_], *, kernel_cells: int = MORPH_KERNEL_CELLS
) -> npt.NDArray[np.bool_]:
    """Morphological open then close (HLD §6.4 Step 4).

    Opening drops isolated cells that are noise rather than land; closing then
    fills the pinholes opening punched in otherwise solid patches. The order
    matters -- closing first would consolidate the speckle instead of removing
    it.
    """
    if kernel_cells < 2 or not mask.any():
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_cells, kernel_cells))
    src = mask.astype(np.uint8)
    opened = cv2.morphologyEx(src, cv2.MORPH_OPEN, kernel)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
    return closed.astype(bool)


def _distance_field_m(
    blocker: npt.NDArray[np.bool_], cell_size_m: float
) -> npt.NDArray[np.float64] | None:
    """Metres to the nearest True cell, or None when there are none."""
    if not blocker.any():
        return None
    # EDT measures distance to the nearest zero, so the mask is inverted.
    return np.asarray(distance_transform_edt(~blocker), dtype=np.float64) * cell_size_m


def extract_parcels(
    available: npt.NDArray[np.bool_],
    dem: DemGrid,
    *,
    min_area_m2: float = DEFAULT_MIN_AREA_M2,
    slope_pct: npt.NDArray[np.float32] | None = None,
    land_cover: npt.NDArray[np.uint8] | None = None,
    hydrologic_soil_group: str | None = None,
    flow_accumulation: npt.NDArray[np.float64] | None = None,
    road_mask: npt.NDArray[np.bool_] | None = None,
    settlement_mask: npt.NDArray[np.bool_] | None = None,
    simplify_cells: float = 0.5,
) -> tuple[tuple[Parcel, ...], int]:
    """Split the mask into parcels and attribute each (HLD §6.4 Steps 5-6).

    Returns `(parcels, dropped_below_min_area)`, ordered largest first.
    """
    if not available.any():
        return (), 0

    count, labels = cv2.connectedComponents(available.astype(np.uint8), connectivity=8)
    cell_area = dem.cell_size_m**2
    min_cells = max(1, int(round(min_area_m2 / cell_area)))

    to_road = _distance_field_m(road_mask, dem.cell_size_m) if road_mask is not None else None
    to_house = (
        _distance_field_m(settlement_mask, dem.cell_size_m) if settlement_mask is not None else None
    )
    tf = Transformer.from_crs(dem.epsg, 4326, always_xy=True)

    parcels: list[Parcel] = []
    dropped = 0
    # Label 0 is the background.
    for label in range(1, count):
        patch = labels == label
        cells = int(patch.sum())
        if cells < min_cells:
            dropped += 1
            continue

        rows, cols = np.nonzero(patch)
        cx, cy = dem.xy(int(round(rows.mean())), int(round(cols.mean())))
        lon, lat = tf.transform(cx, cy)

        if slope_pct is not None:
            values = slope_pct[patch]
            values = values[np.isfinite(values)]
            mean_slope = float(values.mean()) if values.size else float("nan")
            peak_slope = float(values.max()) if values.size else float("nan")
        else:
            mean_slope = peak_slope = float("nan")

        dominant = "unknown"
        if land_cover is not None:
            sel = land_cover[patch]
            sel = sel[sel > 0]
            if sel.size:
                code, _n = max(
                    (
                        (int(c), int(n))
                        for c, n in zip(*np.unique(sel, return_counts=True), strict=True)
                    ),
                    key=lambda kv: kv[1],
                )
                dominant = LEGEND.get(code, f"class_{code}")

        parcels.append(
            Parcel(
                parcel_id=len(parcels) + 1,
                area_m2=cells * cell_area,
                area_ha=cells * cell_area / 10_000.0,
                centroid_lonlat=(round(float(lon), 7), round(float(lat), 7)),
                mean_slope_pct=mean_slope,
                max_slope_pct=peak_slope,
                dominant_land_cover=dominant,
                hydrologic_soil_group=hydrologic_soil_group,
                distance_to_road_m=(None if to_road is None else float(to_road[patch].min())),
                distance_to_settlement_m=(
                    None if to_house is None else float(to_house[patch].min())
                ),
                mean_flow_accumulation_cells=(
                    None if flow_accumulation is None else float(flow_accumulation[patch].mean())
                ),
                geometry=mask_to_geojson(patch, dem, simplify_cells=simplify_cells),
            )
        )

    parcels.sort(key=lambda p: -p.area_m2)
    # Renumber so parcel 1 is the largest, which is the order a reader expects.
    ranked = tuple(Parcel(**{**p.__dict__, "parcel_id": i}) for i, p in enumerate(parcels, start=1))
    return ranked, dropped


def available_land(
    dem: DemGrid,
    *,
    slope_pct: npt.NDArray[np.float32],
    land_cover: npt.NDArray[np.uint8] | None = None,
    osm: OsmContext | None = None,
    hydrologic_soil_group: str | None = None,
    flow_accumulation: npt.NDArray[np.float64] | None = None,
    max_slope_pct: float = DEFAULT_MAX_SLOPE_PCT,
    min_area_m2: float = DEFAULT_MIN_AREA_M2,
    allow_cropland: bool = False,
    kernel_cells: int = MORPH_KERNEL_CELLS,
) -> LandAvailability:
    """The whole of HLD §6.4, end to end.

    Land cover is optional: without it the mask falls back to terrain plus OSM,
    which is weaker but still worth returning, and `removed_by` records that the
    land-cover rules contributed nothing.
    """
    valid = np.isfinite(dem.elevation)
    considered = int(valid.sum())
    removed_by: dict[str, int] = {}

    # Step 1 -- exclusions.
    steep = valid & (slope_pct > max_slope_pct)
    removed_by["slope"] = int(steep.sum())
    excluded = steep

    if land_cover is not None:
        included_lulc, excluded_lulc = lulc_masks(land_cover, allow_cropland=allow_cropland)
        removed_by["land_cover"] = int((valid & excluded_lulc).sum())
        excluded = excluded | excluded_lulc
    else:
        included_lulc = valid
        removed_by["land_cover"] = 0

    osm_used = osm is not None and osm.total > 0
    if osm is not None:
        osm_mask, osm_counts = osm_exclusion_mask(osm, dem)
        for kind, n in osm_counts.items():
            removed_by[f"osm_{kind}"] = n
        excluded = excluded | osm_mask
        settlement = _burn(osm.buildings, dem)
        roads_only = _burn(osm.roads + osm.tracks, dem)
    else:
        settlement = None
        roads_only = None

    # Steps 2-3 -- inclusion, then intersect.
    available = valid & included_lulc & ~excluded

    # Step 4 -- morphological cleaning.
    available = clean(available, kernel_cells=kernel_cells) & valid

    # Steps 5-6 -- parcels and attributes.
    parcels, dropped = extract_parcels(
        available,
        dem,
        min_area_m2=min_area_m2,
        slope_pct=slope_pct,
        land_cover=land_cover,
        hydrologic_soil_group=hydrologic_soil_group,
        flow_accumulation=flow_accumulation,
        road_mask=roads_only,
        settlement_mask=settlement,
    )

    total_m2 = sum(p.area_m2 for p in parcels)
    log.info(
        "land availability",
        parcels=len(parcels),
        total_ha=round(total_m2 / 10_000.0, 3),
        dropped=dropped,
        osm=osm_used,
    )
    return LandAvailability(
        available=available,
        parcels=parcels,
        removed_by=removed_by,
        total_available_m2=total_m2,
        considered_cells=considered,
        osm_used=osm_used,
        cropland_allowed=allow_cropland,
        max_slope_pct=max_slope_pct,
        min_area_m2=min_area_m2,
        dropped_below_min_area=dropped,
    )


def _burn(features: list[OsmFeature], dem: DemGrid) -> npt.NDArray[np.bool_] | None:
    """Rasterise features with no buffer, for the distance-to-X attributes."""
    geoms = [g for g, _ in _projected_geometries(features, dem.epsg)]
    if not geoms:
        return None
    a, b, c, d, e, f = dem.transform
    rows, cols = dem.shape
    burned = rasterize(
        [(g, 1) for g in geoms],
        out_shape=(rows, cols),
        transform=Affine(a, b, c, d, e, f),
        fill=0,
        all_touched=True,
        dtype="uint8",
    )
    return np.asarray(burned, dtype=bool)
