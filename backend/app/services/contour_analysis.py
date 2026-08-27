"""End-to-end contour-map analysis (MC-11).

Orchestrates the pipeline behind `POST /analyzeContour`:

    KML/KMZ -> parse -> interpolate to a metric DEM -> condition (Priority-Flood)
            -> D8 flow routing -> pond siting -> catchment per candidate -> JSON

Every stage below `parse` is shared with the remote-DEM path (HLD ADR-7): the
contour file supplies a `DemGrid` and nothing downstream knows or cares where it
came from. That is why adding a terrain input costs one adapter rather than a
second pipeline.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import numpy.typing as npt

from app.providers.elevation.base import DemGrid
from app.providers.elevation.contour_kml import ParsedContours, parse_contour_file
from app.providers.rainfall import ensemble as rainfall_ensemble
from app.services import hydrology as hyd
from app.services import indian_runoff, siting
from app.services import pond as pond_design
from app.services import runoff as runoff_service
from app.services.enrichment import Enrichment, fetch_enrichment
from app.services.geometry import bbox_geojson, mask_to_geojson, point_geojson
from app.services.interpolate import InterpolationReport, contours_to_dem

#: How far a pour point may be nudged onto the drainage line. Expressed in metres
#: so it means the same thing at any grid resolution (HLD CH-12).
DEFAULT_SNAP_RADIUS_M = 150.0

#: Upstream area above which a cell is treated as part of the stream network.
DEFAULT_STREAM_THRESHOLD_HA = 5.0


@dataclass
class ContourAnalysisOptions:
    """Everything a caller may tune. Defaults are derived or documented."""

    cell_size_m: float | None = None  # None -> derive from the contours
    max_sites: int = 5
    max_slope_pct: float = siting.DEFAULT_MAX_SLOPE_PCT
    min_upstream_ha: float = siting.DEFAULT_MIN_UPSTREAM_HA
    score_threshold: float = siting.DEFAULT_SCORE_THRESHOLD
    min_separation_m: float = siting.DEFAULT_MIN_SEPARATION_M
    min_depression_depth_m: float = siting.DEFAULT_MIN_DEPRESSION_DEPTH_M
    snap_radius_m: float = DEFAULT_SNAP_RADIUS_M
    include_catchment_geometry: bool = True
    include_contours: bool = False
    stream_threshold_ha: float = DEFAULT_STREAM_THRESHOLD_HA
    #: Fetch soil, land cover and rainfall from the AOI's own location. Turning
    #: it off gives a terrain-only answer with no network access at all.
    enrich: bool = True
    rainfall_years: int = 30
    #: Wall-clock budget for the enrichment phase; layers that miss it degrade
    #: the tier rather than delaying the response.
    enrichment_budget_s: float = 20.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cell_size_m": self.cell_size_m,
            "max_sites": self.max_sites,
            "max_slope_pct": self.max_slope_pct,
            "min_upstream_ha": self.min_upstream_ha,
            "score_threshold": self.score_threshold,
            "min_separation_m": self.min_separation_m,
            "min_depression_depth_m": self.min_depression_depth_m,
            "snap_radius_m": self.snap_radius_m,
            "include_catchment_geometry": self.include_catchment_geometry,
            "include_contours": self.include_contours,
            "stream_threshold_ha": self.stream_threshold_ha,
            "enrich": self.enrich,
            "rainfall_years": self.rainfall_years,
            "enrichment_budget_s": self.enrichment_budget_s,
        }


@dataclass
class ContourAnalysis:
    """The assembled result. `as_dict()` is the API response body."""

    analysis_id: str
    parsed: ParsedContours
    dem: DemGrid
    interpolation: InterpolationReport
    conditioned: hyd.ConditionedDem
    flow: hyd.FlowGrids
    siting_result: siting.SitingResult
    enrichment: Enrichment
    sites: list[dict[str, Any]]
    elapsed_s: float
    stage_timings: dict[str, float]
    options: ContourAnalysisOptions
    source_filename: str | None = None
    source_bytes: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        b = self.parsed.bounds
        recommended = self.sites[0] if self.sites else None
        return {
            "analysis_id": self.analysis_id,
            "generated_at": self.generated_at,
            "elapsed_s": round(self.elapsed_s, 3),
            "stage_timings_s": {k: round(v, 3) for k, v in self.stage_timings.items()},
            "input": {
                "filename": self.source_filename,
                "size_bytes": self.source_bytes,
                "options": self.options.as_dict(),
            },
            "contour_map": self.parsed.summary(),
            "interpolated_terrain": {
                **self.interpolation.as_dict(),
                "depressions_filled_cells": self.conditioned.filled_cells,
                "deepest_depression_m": round(self.conditioned.max_fill_depth_m, 3),
                "outlet_cells": self.conditioned.outlet_cells,
                "valid_cells": int(self.flow.valid.sum()),
                "max_upstream_cells": int(self.flow.accumulation.max()),
                "max_upstream_area_ha": round(
                    int(self.flow.accumulation.max()) * self.dem.cell_size_m**2 / 10_000.0, 3
                ),
            },
            "area_of_interest": bbox_geojson(*b.as_tuple()),
            "suitability": {
                "analysis_tier": self.enrichment.tier,
                "tier_meaning": self.enrichment.as_dict()["tier_meaning"],
                "layers_used": self.siting_result.layers_used,
                "layers_unavailable": self.siting_result.layers_unavailable,
                "criteria_weights": {k: round(v, 4) for k, v in self.siting_result.weights.items()},
                "constraints_applied": self.siting_result.constraints,
                "feasible_cells": int(self.siting_result.feasible.sum()),
            },
            "environment": self.enrichment.as_dict(),
            "recommended_site": recommended,
            "candidate_sites": self.sites,
            "warnings": self.warnings,
        }


def analyze_contour_map(
    data: bytes,
    filename: str | None = None,
    options: ContourAnalysisOptions | None = None,
) -> ContourAnalysis:
    """Run the full pipeline on an uploaded contour map.

    Raises `ContourParseError` (with a specific reason) on unusable input; every
    other failure mode is reported as a warning on an otherwise valid result, so
    a partially-degraded analysis still answers the question.
    """
    opts = options or ContourAnalysisOptions()
    t_total = time.perf_counter()
    timings: dict[str, float] = {}

    def stage(name: str, fn: Any) -> Any:
        t = time.perf_counter()
        out = fn()
        timings[name] = time.perf_counter() - t
        return out

    parsed = stage("parse", lambda: parse_contour_file(data, filename))
    dem_and_report = stage(
        "interpolate", lambda: contours_to_dem(parsed, cell_size_m=opts.cell_size_m)
    )
    dem, interp = dem_and_report
    conditioned = stage("condition", lambda: hyd.fill_depressions(dem))
    flow = stage("flow_routing", lambda: hyd.build_flow(dem, conditioned))

    # Enrichment from the AOI's own position (HLD §6.10.4). Each layer fails
    # independently: a provider outage drops the tier, never the analysis.
    enrichment = stage(
        "enrichment",
        lambda: fetch_enrichment(
            parsed.bounds,
            dem,
            rainfall_years=opts.rainfall_years,
            enabled=opts.enrich,
            budget_s=opts.enrichment_budget_s,
        ),
    )
    availability = enrichment.availability_grid()

    result = stage(
        "siting",
        lambda: siting.identify_pond_sites(
            dem,
            conditioned,
            flow,
            max_sites=opts.max_sites,
            max_slope_pct=opts.max_slope_pct,
            min_upstream_ha=opts.min_upstream_ha,
            score_threshold=opts.score_threshold,
            min_separation_m=opts.min_separation_m,
            min_depression_depth_m=opts.min_depression_depth_m,
            availability=availability,
            layers_used=enrichment.layers_used,
            layers_unavailable=enrichment.layers_unavailable,
            tier=enrichment.tier,
        ),
    )

    snap_cells = max(0, int(round(opts.snap_radius_m / dem.cell_size_m)))
    sites = stage(
        "catchments",
        lambda: [
            _site_payload(
                dem, conditioned, flow, site, snap_cells, opts, enrichment, result.buildable
            )
            for site in result.sites
        ],
    )

    warnings = [*parsed.warnings, *conditioned.warnings, *result.warnings]
    if enrichment.rainfall:
        warnings.extend(enrichment.rainfall.warnings)
    for failure in enrichment.failures:
        warnings.append(
            f"{failure['layer']} unavailable ({failure['reason']}); the analysis "
            f"continued at tier '{enrichment.tier}'"
        )
    if not sites:
        warnings.append(
            "no candidate pond site met the constraints; the analysis of the terrain "
            "itself is still reported above"
        )

    return ContourAnalysis(
        analysis_id=uuid.uuid4().hex[:16],
        parsed=parsed,
        dem=dem,
        interpolation=interp,
        conditioned=conditioned,
        flow=flow,
        siting_result=result,
        enrichment=enrichment,
        sites=sites,
        elapsed_s=time.perf_counter() - t_total,
        stage_timings=timings,
        options=opts,
        source_filename=filename,
        source_bytes=len(data),
        warnings=warnings,
    )


def _site_payload(
    dem: DemGrid,
    conditioned: hyd.ConditionedDem,
    flow: hyd.FlowGrids,
    site: siting.CandidateSite,
    snap_cells: int,
    opts: ContourAnalysisOptions,
    enrichment: Enrichment,
    buildable: npt.NDArray[np.bool_],
) -> dict[str, Any]:
    """One candidate site: its catchment, the runoff that reaches it, and a pond
    sized to hold a defensible share of that runoff."""
    catchment = hyd.delineate_catchment(
        dem, flow, site.outlet_row, site.outlet_col, snap_radius_cells=snap_cells
    )
    metrics = hyd.catchment_metrics(dem, conditioned, flow, catchment)
    slope = hyd.slope_percent(dem.elevation, dem.cell_size_m)

    payload: dict[str, Any] = dict(site.as_dict())
    outlet_lon, outlet_lat = _cell_lonlat(dem, *catchment.outlet_rowcol)
    catch: dict[str, Any] = {
        "metrics": metrics,
        "pour_point": {
            **point_geojson(outlet_lon, outlet_lat),
            "grid_row": catchment.outlet_rowcol[0],
            "grid_col": catchment.outlet_rowcol[1],
        },
        "snapped": {
            "was_snapped": catchment.snapped_from is not None,
            "distance_m": round(catchment.snap_distance_m, 1),
            "search_radius_m": opts.snap_radius_m,
        },
        "quality": {
            "touches_survey_edge": catchment.touches_grid_edge,
            "confidence": _confidence(catchment, metrics),
        },
    }
    if opts.include_catchment_geometry:
        catch["geometry"] = mask_to_geojson(catchment.mask, dem)
    payload["catchment"] = catch

    terrain: dict[str, Any] = dict(payload["terrain"])
    terrain["slope_pct_at_site"] = round(float(slope[site.row, site.col]), 2)
    payload["terrain"] = terrain

    payload["runoff"] = _runoff_payload(catchment, enrichment)
    payload["pond"] = _pond_payload(dem, site, payload["runoff"], buildable)
    return payload


def _runoff_payload(catchment: hyd.Catchment, enrichment: Enrichment) -> dict[str, Any] | None:
    """SCS-CN runoff for this catchment, or None with the reason stated.

    Land cover is taken *within the catchment* rather than over the whole survey:
    runoff depends on what the contributing area is covered with, not on the
    average of the map.
    """
    if enrichment.rainfall is None:
        return {
            "available": False,
            "reason": (
                "rainfall data was unavailable, so runoff cannot be estimated; the "
                "catchment area and the pond's stage-storage capacity above are "
                "unaffected"
            ),
        }

    hsg, measured = enrichment.hydrologic_soil_group()
    assumptions: list[str] = []
    if not measured:
        assumptions.append(
            f"soil data was unavailable; Hydrologic Soil Group assumed to be {hsg} "
            "(mid-range) -- verify before using the figure for design"
        )

    if enrichment.land_cover is not None:
        cover = enrichment.land_cover.fractions_within(catchment.mask)
        cover_source = "esa_worldcover (zonal, within the catchment)"
    else:
        cover = {runoff_service.FALLBACK_LAND_COVER: 1.0}
        cover_source = f"assumed {runoff_service.FALLBACK_LAND_COVER} (land cover unavailable)"
        assumptions.append(
            f"land cover was unavailable; the catchment is assumed to be entirely "
            f"{runoff_service.FALLBACK_LAND_COVER}"
        )
    if not cover:
        cover = {runoff_service.FALLBACK_LAND_COVER: 1.0}

    rain = enrichment.rainfall
    years = np.array([d.year for d in rain.dates])
    months = np.array([d.month for d in rain.dates])
    cn = runoff_service.composite_curve_number(cover, hsg, land_cover_source=cover_source)
    estimate = runoff_service.estimate_runoff(
        rain.daily_mm,
        years,
        months,
        cn,
        catchment.area_m2,
        monsoon_months=rain.monsoon_months,
    )
    body = estimate.as_dict()
    body["available"] = True
    body["assumptions"] = [*assumptions, *body["assumptions"]]

    # SCS-CN is a US model applied to a monsoon regime (HLD CH-15), so the figure
    # is cross-checked against formulae fitted on Indian gauged catchments. The
    # state is unknown for a contour upload, so the region falls to `general` and
    # the applicable set is whatever needs only rainfall; every method reports
    # itself either way, because "no cross-check was available" and "the
    # cross-check agreed" are different statements about the same number.
    monsoon_total = sum(
        rain.monthly_normals_mm[month - 1]
        for month in rain.monsoon_months
        if 1 <= month <= len(rain.monthly_normals_mm)
    )
    body["cross_check"] = indian_runoff.cross_check(
        scs_cn_runoff_mm=estimate.annual_mean_mm,
        annual_rainfall_mm=rain.mean_annual_mm,
        monsoon_rainfall_mm=monsoon_total,
        monthly_rainfall_mm=list(rain.monthly_normals_mm),
        # From whichever rainfall source carries a temperature series -- only
        # NASA POWER does. Taken from the ensemble rather than the primary,
        # because the primary is chosen for rainfall resolution and the finer
        # rainfall source is not the one with the temperature: reading it off
        # the primary would leave Khosla unavailable even when a temperature
        # had been fetched.
        monthly_temp_c=(
            rainfall_ensemble.temperature_from(enrichment.rainfall_ensemble)
            if enrichment.rainfall_ensemble
            else None
        ),
    ).as_dict()
    return body


def _pond_payload(
    dem: DemGrid,
    site: siting.CandidateSite,
    runoff: dict[str, Any] | None,
    buildable: npt.NDArray[np.bool_],
) -> dict[str, Any]:
    """Pond geometry and capacity for this site."""
    annual_m3: float | None = None
    if runoff and runoff.get("available"):
        annual_m3 = float(runoff["annual_mean"]["runoff_volume_m3"])

    footprint, capped = pond_design.usable_footprint_m2(
        buildable, site.row, site.col, dem.cell_size_m
    )
    try:
        design = pond_design.design_pond(
            dem,
            site.row,
            site.col,
            available_area_m2=footprint,
            annual_runoff_m3=annual_m3,
        )
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    body = design.as_dict()
    body["available"] = True
    body["footprint"] = {
        "usable_buildable_area_m2": round(footprint, 1),
        "usable_buildable_area_ha": round(footprint / 10_000.0, 4),
        "capped_at_max": capped,
        "max_considered_m2": pond_design.MAX_POND_FOOTPRINT_M2,
        "note": (
            "Contiguous land around the site that passed every feasibility mask, "
            "not the extent of the scoring cluster."
        ),
    }
    return body


def _cell_lonlat(dem: DemGrid, row: int, col: int) -> tuple[float, float]:
    from pyproj import Transformer

    x, y = dem.xy(row, col)
    lon, lat = Transformer.from_crs(dem.epsg, 4326, always_xy=True).transform(x, y)
    return float(lon), float(lat)


def _confidence(catchment: hyd.Catchment, metrics: dict[str, Any]) -> str:
    """Plain-language confidence, with the reason attached whenever it is not high.

    A catchment that runs off the surveyed area is understated, and flat terrain
    makes D8 flow directions weakly determined (HLD CH-7, CH-2). Both are stated
    in the response rather than buried.
    """
    if catchment.touches_grid_edge:
        return (
            "low: the catchment reaches the edge of the surveyed area, so its area is "
            "a lower bound"
        )
    relief = float(metrics.get("relief_m") or 0.0)
    if relief < 5.0:
        return (
            f"medium: only {relief:.1f} m of relief across the catchment, so D8 flow "
            "directions are weakly determined"
        )
    return "high"
