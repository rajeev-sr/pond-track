"""Hard exclusions for pond siting: where a pond must not go (edge-case guard).

Siting scores terrain. Terrain does not know about houses, roads, rivers, or the
tank that is already there -- and the scoring model actively *likes* those last
two, because flow accumulation and depression depth are its two strongest
signals and water maximises both. Measured on the sample sheet with land cover
removed, three of five recommended sites landed inside permanent water.

So the terrain score needs a veto, and this builds it. Five classes, each with a
buffer chosen for a reason rather than for symmetry:

* **Standing water** (tanks, lakes) -- 0 m. The exclusion is the water itself;
  the bank of an existing tank is a perfectly reasonable place for a new bund.
* **Major watercourses** (river, canal) -- buffered. This is the case a reader
  asks about first: *you cannot build a village pond in a river.* Damming a
  river is a different structure with different clearances, and the land beside
  one floods.
* **Minor channels** (stream, drain, ditch) -- **not excluded.** This is the
  distinction that matters. A check dam or nala bund belongs *on* a small
  channel; excluding them would reject the correct answer.
* **Buildings** -- buffered. A pond centred on a house is not a recommendation.
* **Roads** -- buffered. Tracks are not: a cart track is access to a pond site,
  not an obstruction.

Two independent sources feed this (OpenStreetMap and ESA WorldCover), plus a
terrain-only fallback, so no single provider outage removes the protection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from app.core.logging import get_logger

log = get_logger("services.exclusions")

#: Buffers in metres for the siting veto. Deliberately not `land.BUFFER_M`:
#: that answers "which parcels are worth surveying" and buffers water by 100 m
#: to avoid duplicating an existing tank. This answers "may the pond centre be
#: here", where the bank of a tank is legitimate ground.
SITING_BUFFER_M: dict[str, float] = {
    "standing_water": 0.0,
    "major_watercourse": 50.0,
    "building": 50.0,
    "road": 20.0,
}

#: `waterway` values that make a channel too big to impound with a village pond.
#: A `riverbank` is the mapped area of a river, so it belongs here too.
MAJOR_WATERWAYS = frozenset({"river", "canal", "riverbank"})

#: `waterway` values a check dam is *supposed* to sit on. Never excluded.
MINOR_WATERWAYS = frozenset({"stream", "drain", "ditch"})

#: `water` values that mean the same thing as `MAJOR_WATERWAYS`, for the *areal*
#: mapping of a river. This is the fix for a measured failure, so it is worth
#: stating why it exists. OSM maps a large river TWICE: a centreline tagged
#: `waterway=river`, and the wide body you actually see rendered, tagged
#: `natural=water` + `water=river` with **no `waterway` tag at all**. Keying only
#: off `waterway` therefore sent the body to the standing-water rule and its 0 m
#: buffer -- the rule written for a village tank, whose bank is legitimately good
#: ground. On the sample sheet the Shivnath's 563.6 ha body was classified that
#: way, leaving 64.7 ha of bank and floodplain recommendable, and the centreline
#: buffer could not cover it: the river runs a median 181 m wide, so 50 m from
#: the centre is still inside the water.
MAJOR_WATER_VALUES = frozenset({"river", "canal", "oxbow", "riverbank"})

#: `water` values that are a channel a check dam belongs on -- dropped, exactly
#: as the matching `MINOR_WATERWAYS` are.
MINOR_WATER_VALUES = frozenset({"stream", "drain", "ditch", "canal_ditch"})

#: A `natural=water` area carrying no `water=*` subtag is ambiguous: it may be a
#: tank or an unlabelled river reach. Shape decides, but only well clear of the
#: tanks it must not catch -- a body is promoted to a major watercourse only when
#: it is *both* long-and-thin and large. A misjudged tank costs one site beside
#: an existing tank; a missed river costs a pond in a river, so the thresholds
#: are set to be quiet rather than clever.
ELONGATION_FOR_CHANNEL = 5.0  # long axis / short axis of the enclosing rectangle
MIN_CHANNEL_AREA_M2 = 20_000.0  # 2 ha

#: Margin applied to land-cover water. Two pixels of a 10 m product: this is
#: classification slack at the land/water boundary, not a setback, which is why
#: it is nothing like the 50 m river buffer. Its real job is graceful
#: degradation -- when OSM is the source that failed, this is the only water
#: protection left, and undilated class pixels leave the waterline itself
#: recommendable.
LAND_COVER_WATER_BUFFER_M = 20.0

#: WorldCover codes that are water or otherwise unbuildable whatever terrain says.
WATER_CODES = frozenset({80, 90, 95})  # permanent water, wetland, mangroves
BUILT_CODES = frozenset({50})

#: Upstream area beyond which a cell is a substantial watercourse rather than a
#: pond site, used when no vector data is available at all. Generous on purpose:
#: the sample sheet's best site drains 180 ha and must not be rejected. This is a
#: backstop against damming a river with no OSM, not a tuning parameter.
DEFAULT_MAX_UPSTREAM_HA = 2000.0


@dataclass
class ExclusionMask:
    """The veto, and an audit of which rule produced it."""

    mask: npt.NDArray[np.bool_]
    removed_by: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        """How much of the veto was actually available."""
        if {"OpenStreetMap", "land cover"} <= set(self.sources):
            return "high"
        if self.sources and self.sources != ["terrain"]:
            return "partial"
        return "terrain-only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "excluded_cells": int(self.mask.sum()),
            "removed_by": {k: v for k, v in self.removed_by.items() if v},
            "sources": list(self.sources),
            "confidence": self.confidence,
            "notes": list(self.notes),
        }


def _dilate(mask: npt.NDArray[np.bool_], buffer_m: float, dem: Any) -> npt.NDArray[np.bool_]:
    """Grow a raster mask by `buffer_m`, measured on the DEM's own cell size.

    A circular structuring element, so the margin is a true distance rather than
    a square that reaches 1.41x further on the diagonals.
    """
    cell = float(getattr(dem, "cell_size_m", 0.0) or 0.0)
    if buffer_m <= 0 or cell <= 0 or not mask.any():
        return mask
    radius = int(np.ceil(buffer_m / cell))
    if radius < 1:
        return mask
    from scipy.ndimage import binary_dilation

    span = np.arange(-radius, radius + 1)
    yy, xx = np.meshgrid(span, span, indexing="ij")
    disc = (yy**2 + xx**2) <= radius**2
    return np.asarray(binary_dilation(mask, structure=disc), dtype=bool)


def _looks_like_a_channel(feature: Any, epsg: int) -> bool:
    """Shape test for a `natural=water` area with no `water=*` subtag.

    A river reach is long and thin; a tank is roughly equant. Both conditions
    must hold and both are set well clear of village tanks -- see
    `ELONGATION_FOR_CHANNEL`. Any geometry failure answers False, because this is
    a promotion rule: falling back to standing water is the status quo, never a
    new exclusion invented from a broken ring.
    """
    try:
        from app.services.land import _projected_geometries

        geoms = [g for g, _ in _projected_geometries([feature], epsg)]
        if not geoms:
            return False
        total = 0.0
        elongated = False
        for geom in geoms:
            box = geom.minimum_rotated_rectangle
            xs, ys = box.exterior.coords.xy
            pts = list(zip(xs, ys, strict=True))
            sides = [
                ((pts[i][0] - pts[i + 1][0]) ** 2 + (pts[i][1] - pts[i + 1][1]) ** 2) ** 0.5
                for i in range(len(pts) - 1)
            ]
            if len(sides) < 2:
                continue
            long_side, short_side = max(sides), min(sides)
            total += float(geom.area)
            if short_side > 0 and long_side / short_side >= ELONGATION_FOR_CHANNEL:
                elongated = True
        return elongated and total >= MIN_CHANNEL_AREA_M2
    except Exception:  # a bad ring is data, not a bug
        return False


def classify_water(feature: Any, epsg: int | None = None) -> str:
    """`"major"`, `"standing"` or `"minor"` for one OSM water feature.

    Read both tagging conventions, because OSM uses both for the same river: the
    centreline carries `waterway=river` and the areal body carries
    `natural=water` + `water=river`. Keying off one tag alone is what let a river
    through as standing water -- see `MAJOR_WATER_VALUES`.
    """
    tags = feature.tags if hasattr(feature, "tags") else {}
    waterway = str(tags.get("waterway", "")).lower()
    water = str(tags.get("water", "")).lower()

    if waterway in MAJOR_WATERWAYS or water in MAJOR_WATER_VALUES:
        return "major"
    if waterway in MINOR_WATERWAYS or water in MINOR_WATER_VALUES:
        return "minor"
    # Unlabelled `natural=water`: let the shape decide, conservatively.
    if not water and not waterway and epsg is not None and _looks_like_a_channel(feature, epsg):
        return "major"
    return "standing"


def _split_water(features: list[Any], epsg: int | None = None) -> tuple[list[Any], list[Any]]:
    """`(standing, major)` — and everything minor is deliberately dropped.

    A stream or a field drain is where a check dam goes. Treating every mapped
    waterway as an obstruction would exclude exactly the positions the model is
    supposed to find.
    """
    standing: list[Any] = []
    major: list[Any] = []
    for feature in features:
        kind = classify_water(feature, epsg)
        if kind == "major":
            major.append(feature)
        elif kind == "standing":
            standing.append(feature)
    return standing, major


def build(
    dem: Any,
    *,
    osm: Any | None = None,
    land_cover_codes: npt.NDArray[np.uint8] | None = None,
    flow_accumulation: npt.NDArray[np.floating] | None = None,
    max_upstream_ha: float = DEFAULT_MAX_UPSTREAM_HA,
    buffers_m: dict[str, float] | None = None,
    land_cover_water_buffer_m: float = LAND_COVER_WATER_BUFFER_M,
) -> ExclusionMask:
    """Everywhere a pond centre must not go, from whatever sources answered."""
    buffers = dict(SITING_BUFFER_M if buffers_m is None else buffers_m)
    shape = dem.shape
    mask = np.zeros(shape, dtype=bool)
    result = ExclusionMask(mask=mask)

    if osm is not None:
        from app.providers.vector.overpass import OsmContext
        from app.services.land import osm_exclusion_mask

        epsg = getattr(dem, "epsg", None)
        water_features = list(osm.water)
        standing, major = _split_water(water_features, epsg)
        # A shape-promoted body is a judgement the reader must be able to see,
        # so it is reported rather than folded silently into the river count.
        by_shape = sum(
            1
            for f in water_features
            if classify_water(f, epsg) == "major" and classify_water(f, None) != "major"
        )
        groups = (
            ("standing_water", OsmContext(water=standing)),
            ("major_watercourse", OsmContext(water=major)),
            ("building", OsmContext(buildings=list(osm.buildings))),
            ("road", OsmContext(roads=list(osm.roads))),
        )
        for name, context in groups:
            if context.total == 0:
                result.removed_by[name] = 0
                continue
            burned, _counts = osm_exclusion_mask(
                context,
                dem,
                buffers_m={
                    "water": buffers.get(name, 0.0),
                    "building": buffers.get(name, 0.0),
                    "road": buffers.get(name, 0.0),
                    "track": 0.0,
                    "landuse": 0.0,
                },
            )
            result.removed_by[name] = int(burned.sum())
            mask |= burned
        result.sources.append("OpenStreetMap")
        if result.removed_by.get("major_watercourse"):
            result.notes.append(
                f"{result.removed_by['major_watercourse']:,} cells excluded within "
                f"{buffers['major_watercourse']:g} m of a mapped river or canal: a "
                "village pond cannot impound one, and the land beside it floods."
            )
        if not getattr(osm, "water_relations", False):
            result.notes.append(
                "OpenStreetMap water multipolygon *relations* were not part of "
                "this window (older cache entry, or the supplementary query did "
                "not answer). A river mapped as a relation would be missing "
                "rather than mis-buffered, so re-fetch before relying on the "
                "river veto in unfamiliar terrain."
            )
        if by_shape:
            result.notes.append(
                f"{by_shape} unlabelled water area(s) were treated as a watercourse "
                "on shape alone (long, thin and larger than 2 ha). Verify on "
                "imagery if a site was rejected nearby."
            )

    if land_cover_codes is not None:
        water = np.isin(land_cover_codes, sorted(WATER_CODES))
        built = np.isin(land_cover_codes, sorted(BUILT_CODES))
        # Raw class pixels give zero margin, so the waterline itself stays
        # recommendable -- and when OSM is the layer that failed, this is the
        # only water protection left. The margin is *classification* slack, not
        # a hydrological setback: WorldCover is a 10 m product, so its
        # land/water boundary is uncertain by about a pixel and this allows two.
        # That is why it is far smaller than the 50 m river buffer, and why it
        # does not contradict the 0 m standing-water rule -- the bank of a tank
        # stays available beyond the margin.
        water = _dilate(water, land_cover_water_buffer_m, dem)
        result.removed_by["land_cover_water"] = int(water.sum())
        result.removed_by["land_cover_built_up"] = int(built.sum())
        mask |= water | built
        result.sources.append("land cover")

    if flow_accumulation is not None:
        cell_ha = (dem.cell_size_m**2) / 10_000.0
        upstream_ha = np.asarray(flow_accumulation, dtype=np.float64) * cell_ha
        too_big = upstream_ha > max_upstream_ha
        result.removed_by["major_channel_by_area"] = int(too_big.sum())
        if too_big.any():
            result.notes.append(
                f"{int(too_big.sum()):,} cells drain more than {max_upstream_ha:,.0f} ha "
                "and are treated as a substantial watercourse rather than a pond site."
            )
        mask |= too_big
        result.sources.append("terrain")

    if "OpenStreetMap" not in result.sources and "land cover" not in result.sources:
        result.notes.append(
            "Neither OpenStreetMap nor land cover was available, so existing "
            "tanks, buildings and roads could not be excluded. Terrain alone "
            "cannot tell a good pond site from a pond that is already there -- "
            "both are depressions where water collects. Verify on imagery."
        )

    result.mask = mask
    log.info(
        "siting exclusions built",
        excluded=int(mask.sum()),
        confidence=result.confidence,
        sources=result.sources,
    )
    return result
