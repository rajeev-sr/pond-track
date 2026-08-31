"""Location-derived enrichment for a contour upload (HLD §6.10.4, MC-19).

A contour map carries no soil, land-cover or rainfall attribute -- but it carries
its own *position*, and every remaining layer the pond calculation needs is
reachable from a coordinate with no credential. So a contour upload yields the
complete analysis, not a terrain-only subset.

Two properties matter here:

* **Each layer fails independently.** One provider being down degrades the answer
  by one tier; it never fails the analysis. The response names what was missing
  and why (HLD §3.7 `PARTIAL`).
* **The three fetches run concurrently.** They have no dependency on one another,
  and sequentially they are the single largest component of wall-clock time --
  ~7 s becomes ~4 s, which is the latency lever HLD §9.1 identifies.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt

from app.providers.base import ProviderUnavailableError
from app.providers.elevation.base import Bounds, DemGrid
from app.providers.landcover.worldcover import AVAILABILITY, LandCover, fetch_landcover
from app.providers.rainfall.base import RainfallStats
from app.providers.rainfall.ensemble import RainfallEnsemble, fetch_ensemble
from app.providers.soil.soilgrids import SoilProfile, fetch_soil_profile

AnalysisTier = Literal["full", "no_soil_lulc", "terrain_only"]

#: Land-cover classes a pond cannot be built on, whatever the terrain says.
EXCLUDED_COVER_CODES = (50, 70, 80, 95)  # built-up, snow, water, mangroves

#: Assumed when soil is unavailable but rainfall is not, so a runoff figure can
#: still be produced. C is the middle of the range and is stated as an
#: assumption in the response rather than presented as measured.
ASSUMED_HSG = "C"

#: Total wall-clock budget for the whole enrichment phase. Whatever has not
#: arrived by then is dropped and the tier degrades accordingly.
#:
#: This exists because SoilGrids is measurably unreliable: sampled latencies of
#: 0.9 s, 3.5 s, 3.7 s, a read timeout, and 1.05 s from the same host minutes
#: apart. With retries and backoff a single flaky provider stretched a 5 s
#: enrichment to 50 s and dominated the request. A deadline turns an unbounded
#: wait into a bounded one, and the tier ladder already knows how to answer with
#: a layer missing -- so degrading is strictly better than waiting.
DEFAULT_BUDGET_S = 20.0


@dataclass
class Enrichment:
    soil: SoilProfile | None = None
    land_cover: LandCover | None = None
    rainfall: RainfallStats | None = None
    #: Every rainfall source that answered, and the spread between them. Present
    #: whenever `rainfall` is: `rainfall` is one source's daily series (SCS-CN
    #: cannot run on a blend), and this is the uncertainty around it.
    rainfall_ensemble: RainfallEnsemble | None = None
    #: OpenStreetMap features for the window: existing tanks, rivers, buildings
    #: and roads. A *second, independent* source alongside land cover, so losing
    #: one does not leave the model free to recommend a pond in an existing tank
    #: -- which it otherwise does, enthusiastically, because a tank scores
    #: maximally on depression depth and flow accumulation.
    osm: Any | None = None
    failures: list[dict[str, str]] = field(default_factory=list)
    elapsed_s: float = 0.0
    budget_s: float = 0.0
    skipped: bool = False

    @property
    def tier(self) -> AnalysisTier:
        if self.soil and self.land_cover and self.rainfall:
            return "full"
        if self.rainfall:
            return "no_soil_lulc"
        return "terrain_only"

    @property
    def layers_used(self) -> list[str]:
        used = ["elevation", "slope", "flow_accumulation", "depression_depth"]
        if self.land_cover:
            used.append("land_use_land_cover")
        if self.soil:
            used.append("soil_hydrologic_group")
        if self.rainfall:
            used.extend(["rainfall", "reference_evapotranspiration"])
        return used

    @property
    def layers_unavailable(self) -> list[str]:
        missing = []
        if not self.land_cover:
            missing.append("land_use_land_cover")
        if not self.soil:
            missing.append("soil_hydrologic_group")
        if not self.rainfall:
            missing.append("rainfall")
        return missing

    def hydrologic_soil_group(self) -> tuple[str, bool]:
        """(group, was_measured). Falls back to a stated assumption."""
        if self.soil:
            return self.soil.hydrologic_soil_group, True
        return ASSUMED_HSG, False

    @property
    def water_exclusion(self) -> dict[str, Any]:
        """Which sources could rule out an existing water body, and what it means.

        This is reported rather than assumed because the failure is quiet and
        serious: with neither source, siting recommends existing tanks -- they
        maximise depression depth and flow accumulation, being places where water
        already collects. The reader needs to know which protection was in force.

        This is the reader-facing view, phrased for `explain.py`. The machine
        audit of what was actually vetoed -- rivers, buildings and roads as well
        as standing water -- is `siting_exclusions()`. Both are derived from the
        same two booleans below, so they never disagree; only the vocabulary
        differs ("none" here, "terrain-only" there, for the same state).
        """
        from_cover = self.land_cover is not None
        from_osm = self.osm is not None
        sources = [
            name
            for name, present in (("land cover", from_cover), ("OpenStreetMap", from_osm))
            if present
        ]
        if from_cover and from_osm:
            note = (
                "Existing water bodies were excluded using two independent "
                "sources, so a site cannot be an already-built tank or an open "
                "watercourse."
            )
        elif sources:
            note = (
                f"Existing water bodies were excluded using {sources[0]} only. "
                "That source is not exhaustive, so check the recommended site is "
                "not an existing tank before acting on it."
            )
        else:
            note = (
                "**Existing water bodies could not be excluded at all.** Neither "
                "land cover nor OpenStreetMap was available, and terrain alone "
                "cannot tell a good pond site from a pond that is already there "
                "-- both are depressions where water collects. Verify on imagery "
                "that the recommended site is dry ground."
            )
        return {
            "sources": sources,
            "confidence": "high" if len(sources) == 2 else ("partial" if sources else "none"),
            "note": note,
        }

    def availability_grid(self) -> npt.NDArray[np.float32] | None:
        """Per-cell buildability from land cover, 0-1, or None if unavailable.

        Land cover only. The wider veto -- existing tanks, rivers, buildings,
        roads -- is `siting_exclusions()`, which needs the flow grid and so
        cannot be computed here.
        """
        if self.land_cover is None:
            return None
        codes = self.land_cover.codes
        out = np.zeros(codes.shape, dtype=np.float32)
        for code, score in AVAILABILITY.items():
            out[codes == code] = score
        return out

    def siting_exclusions(self, dem: Any, flow: Any | None = None) -> Any:
        """Everywhere a pond centre must not go, from whatever sources answered.

        Terrain scores where water *collects*, which is exactly where an existing
        tank is and exactly where a river runs. Without this veto, three of five
        recommended sites on the sample sheet landed in permanent water.
        """
        from app.services import exclusions

        return exclusions.build(
            dem,
            osm=self.osm,
            land_cover_codes=None if self.land_cover is None else self.land_cover.codes,
            flow_accumulation=None if flow is None else flow.accumulation,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis_tier": self.tier,
            "tier_meaning": TIER_MEANING[self.tier],
            "layers_used": self.layers_used,
            "layers_unavailable": self.layers_unavailable,
            "provider_failures": self.failures,
            "water_exclusion": self.water_exclusion,
            "enrichment_elapsed_s": round(self.elapsed_s, 2),
            "enrichment_budget_s": self.budget_s,
            "enrichment_skipped": self.skipped,
            "soil": self.soil.as_dict() if self.soil else None,
            "land_cover": self.land_cover.as_dict() if self.land_cover else None,
            "rainfall": self.rainfall.as_dict() if self.rainfall else None,
            "rainfall_sources": (
                self.rainfall_ensemble.as_dict() if self.rainfall_ensemble else None
            ),
        }


TIER_MEANING: dict[str, str] = {
    "full": (
        "terrain + soil + land cover + rainfall: suitability, composite curve "
        "number, runoff volume and pond sizing are all measured"
    ),
    "no_soil_lulc": (
        "terrain + rainfall: runoff is estimated using an assumed soil group and "
        "land cover, both stated in the response"
    ),
    "terrain_only": (
        "terrain alone: pond location, catchment area and stage-storage capacity "
        "are reported; runoff needs rainfall, which was unavailable"
    ),
}


def fetch_enrichment(
    bounds: Bounds,
    dem: DemGrid,
    *,
    rainfall_years: int = 30,
    enabled: bool = True,
    budget_s: float = DEFAULT_BUDGET_S,
) -> Enrichment:
    """Fetch soil, land cover and rainfall concurrently, within a deadline.

    Never raises: a provider that fails, or does not answer inside the budget, is
    recorded and the tier drops. The three fetches are independent, so running
    them concurrently makes the phase cost the slowest single layer rather than
    the sum -- roughly 4 s instead of 7 s when all three are healthy.
    """
    import time

    if not enabled:
        return Enrichment(skipped=True)

    lon, lat = bounds.centroid
    result = Enrichment()
    t0 = time.perf_counter()

    # Both go through the disk cache, which is also what gives `DEMO_MODE` its
    # meaning: with it set, a cache miss raises rather than reaching the network,
    # so a demo is deterministic and works unplugged. A miss then degrades the
    # tier through the same path as a provider outage, which is already handled.
    from pathlib import Path

    from app.config import get_settings
    from app.services import provider_cache

    settings = get_settings()
    cache_store = Path(settings.COG_STORE_PATH)
    demo = bool(getattr(settings, "DEMO_MODE", False))

    def _soil() -> SoilProfile:
        return provider_cache.cached_soil(
            lon, lat, cache_store, demo_mode=demo, fetch=fetch_soil_profile
        )

    def _cover() -> LandCover:
        return provider_cache.cached_landcover(
            bounds.as_tuple(),
            dem.shape,
            dem.transform,
            dem.epsg,
            cache_store,
            demo_mode=demo,
            fetch=fetch_landcover,
        )

    def _rain() -> RainfallEnsemble:
        # Both sources at once. Either alone is enough to produce a runoff
        # estimate, so a rate-limited Open-Meteo no longer drops the whole
        # analysis to `terrain_only` -- NASA POWER answers instead, and the
        # response says which was used.
        return fetch_ensemble(lon, lat, years=rainfall_years)

    def _water() -> Any:
        """The OSM context for the window. Cheap: the window is disk-cached.

        The *mask* is built later, in `siting_exclusions()`, because it needs the
        flow grid for the terrain fallback and that is not known here.
        """
        from app.providers.vector import osm_cache
        from app.providers.vector.overpass import fetch_osm_context

        context, _cached = osm_cache.fetch_cached(
            bounds.as_tuple(), cache_store, fetch=fetch_osm_context
        )
        return context

    jobs = {
        "soil_hydrologic_group": _soil,
        "land_use_land_cover": _cover,
        "rainfall": _rain,
        "existing_water": _water,
    }
    # A timeout produces no exception to read the provider name off, and the
    # layer name is not an answer to "who was down?". Name the service.
    providers = {
        "soil_hydrologic_group": "ISRIC SoilGrids",
        "land_use_land_cover": "ESA WorldCover",
        "rainfall": "Open-Meteo",
        "existing_water": "OpenStreetMap / Overpass",
    }
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {name: pool.submit(fn) for name, fn in jobs.items()}
    try:
        wait(list(futures.values()), timeout=budget_s)
        for name, future in futures.items():
            if not future.done():
                result.failures.append(
                    {
                        "layer": name,
                        "provider": providers[name],
                        "reason": (
                            f"did not respond within the {budget_s:g} s enrichment "
                            "budget; the analysis continued without it"
                        ),
                    }
                )
                continue
            try:
                value = future.result()
            except ProviderUnavailableError as exc:
                result.failures.append(
                    {"layer": name, "reason": exc.detail, "provider": exc.provider}
                )
                continue
            except Exception as exc:
                result.failures.append(
                    {"layer": name, "reason": type(exc).__name__, "provider": providers[name]}
                )
                continue
            if name == "soil_hydrologic_group":
                result.soil = value  # type: ignore[assignment]
            elif name == "land_use_land_cover":
                result.land_cover = value  # type: ignore[assignment]
            elif name == "existing_water":
                result.osm = value
            else:
                # The futures dict is heterogeneous, so `value` is typed `object`;
                # the key is what says which provider it came from.
                ensemble = cast(RainfallEnsemble, value)
                result.rainfall_ensemble = ensemble
                # The primary is chosen by resolution, not by which replied
                # first: the daily series decides the runoff, so the choice has
                # to be deterministic.
                result.rainfall = ensemble.primary
    finally:
        # Do not block on stragglers: their own HTTP timeouts will end them.
        pool.shutdown(wait=False)

    result.elapsed_s = time.perf_counter() - t0
    result.budget_s = budget_s
    return result
