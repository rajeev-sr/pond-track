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


def _split_water(features: list[Any]) -> tuple[list[Any], list[Any]]:
    """`(standing, major)` — and everything minor is deliberately dropped.

    A stream or a field drain is where a check dam goes. Treating every mapped
    waterway as an obstruction would exclude exactly the positions the model is
    supposed to find.
    """
    standing: list[Any] = []
    major: list[Any] = []
    for feature in features:
        waterway = str(feature.tags.get("waterway", "")).lower()
        if waterway in MAJOR_WATERWAYS:
            major.append(feature)
        elif waterway in MINOR_WATERWAYS:
            continue
        else:
            # natural=water, landuse=reservoir/basin: a body, not a channel.
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
) -> ExclusionMask:
    """Everywhere a pond centre must not go, from whatever sources answered."""
    buffers = dict(SITING_BUFFER_M if buffers_m is None else buffers_m)
    shape = dem.shape
    mask = np.zeros(shape, dtype=bool)
    result = ExclusionMask(mask=mask)

    if osm is not None:
        from app.providers.vector.overpass import OsmContext
        from app.services.land import osm_exclusion_mask

        standing, major = _split_water(list(osm.water))
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

    if land_cover_codes is not None:
        water = np.isin(land_cover_codes, sorted(WATER_CODES))
        built = np.isin(land_cover_codes, sorted(BUILT_CODES))
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
