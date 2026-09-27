"""End-to-end analysis (MC-11): a contour map, or a rectangle drawn on the map.

Two ways in, one pipeline:

    KML/KMZ   -> parse -> interpolate to a metric DEM --.
                                                         >-> condition (Priority-Flood)
    rectangle -> Copernicus GLO-30 for the box ---------'   -> D8 flow routing
              -> pond siting -> catchment per candidate -> JSON

Everything below the DEM is shared (HLD ADR-7): the source supplies a `DemGrid`
and nothing downstream knows or cares where it came from. That is why the drawn
area cost one adapter rather than a second pipeline.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from app.core.crs import is_within_india
from app.providers.elevation import copernicus_aws
from app.providers.elevation.base import Bounds, DemGrid
from app.providers.elevation.contour_kml import ParsedContours, parse_contour_file
from app.providers.rainfall import ensemble as rainfall_ensemble
from app.services import area as area_service
from app.services import contours as contour_generator
from app.services import explain, indian_runoff, siting, water_balance
from app.services import hydrology as hyd
from app.services import pond as pond_design
from app.services import runoff as runoff_service
from app.services.enrichment import Enrichment, fetch_enrichment
from app.services.geometry import (
    bbox_geojson,
    contour_lines_to_geojson,
    contours_to_geojson,
    mask_to_geojson,
    point_geojson,
)
from app.services.interpolate import InterpolationReport, contours_to_dem

#: How far a pour point may be nudged onto the drainage line. Expressed in metres
#: so it means the same thing at any grid resolution (HLD CH-12).
DEFAULT_SNAP_RADIUS_M = 150.0

#: Upstream area above which a cell is treated as part of the stream network.
DEFAULT_STREAM_THRESHOLD_HA = 5.0

#: Display contours for a drawn area are traced from the grid at the first of
#: these intervals giving no more than this many levels -- dense enough to read
#: the terrain, sparse enough not to bury the map.
DISPLAY_INTERVALS_M = (1.0, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0)
MAX_DISPLAY_LEVELS = 40


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
    #: A caller's own AHP weights, replacing the shipped vector. Restricted to
    #: the criteria actually available and renormalised, so the score stays
    #: comparable to a default run. `POST /suitability/weights/ahp` derives one.
    weights_override: dict[str, float] | None = None

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


SourceKind = Literal["uploaded_contour_map", "copernicus_glo30"]


@dataclass
class TerrainSource:
    """Where the DEM came from, and what the response should say about it.

    `bounds` is the area of interest a person chose -- the sheet's extent, or the
    rectangle they drew. `grid_bounds` is everything the grid covers, which for a
    drawn area includes a buffer so catchments starting outside the rectangle are
    measured whole; enrichment is laid onto all of it.
    """

    kind: SourceKind
    bounds: Bounds
    grid_bounds: Bounds
    info: dict[str, Any]
    parsed: ParsedContours | None = None
    interpolation: InterpolationReport | None = None
    area: area_service.AnalysisArea | None = None
    filename: str | None = None
    size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def input_block(self, options: ContourAnalysisOptions) -> dict[str, Any]:
        if self.area is not None:
            return {
                "bbox": self.area.as_list(),
                "area_km2": self.area.area_km2,
                "options": options.as_dict(),
            }
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "options": options.as_dict(),
        }


@dataclass
class ContourAnalysis:
    """The assembled result. `as_dict()` is the API response body."""

    analysis_id: str
    source: TerrainSource
    dem: DemGrid
    conditioned: hyd.ConditionedDem
    flow: hyd.FlowGrids
    siting_result: siting.SitingResult
    enrichment: Enrichment
    sites: list[dict[str, Any]]
    elapsed_s: float
    stage_timings: dict[str, float]
    options: ContourAnalysisOptions
    exclusions: Any | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    warnings: list[str] = field(default_factory=list)

    # Kept as properties so every caller written against the contour-only result
    # -- the endpoint, the job runner, the DEM registry -- reads the same names.
    @property
    def parsed(self) -> ParsedContours | None:
        return self.source.parsed

    @property
    def interpolation(self) -> InterpolationReport | None:
        return self.source.interpolation

    @property
    def source_filename(self) -> str | None:
        return self.source.filename

    @property
    def source_bytes(self) -> int:
        return self.source.size_bytes

    def as_dict(self) -> dict[str, Any]:
        src = self.source
        recommended = self.sites[0] if self.sites else None
        body: dict[str, Any] = {
            "analysis_id": self.analysis_id,
            "generated_at": self.generated_at,
            "elapsed_s": round(self.elapsed_s, 3),
            "stage_timings_s": {k: round(v, 3) for k, v in self.stage_timings.items()},
            "input": src.input_block(self.options),
            # The three results the brief asks for, at the top where a reader
            # looks first. Everything here is also in `recommended_site`; this is
            # the index, not a second computation.
            "summary": _summary(recommended),
            "terrain_source": src.info,
            "contour_map": None if src.parsed is None else src.parsed.summary(),
            "interpolated_terrain": {
                **self._grid_report(),
                "depressions_filled_cells": self.conditioned.filled_cells,
                "deepest_depression_m": round(self.conditioned.max_fill_depth_m, 3),
                "outlet_cells": self.conditioned.outlet_cells,
                "valid_cells": int(self.flow.valid.sum()),
                "max_upstream_cells": int(self.flow.accumulation.max()),
                "max_upstream_area_ha": round(
                    int(self.flow.accumulation.max()) * self.dem.cell_size_m**2 / 10_000.0, 3
                ),
            },
            "area_of_interest": bbox_geojson(*src.bounds.as_tuple()),
            "suitability": {
                "analysis_tier": self.enrichment.tier,
                "tier_meaning": self.enrichment.as_dict()["tier_meaning"],
                "layers_used": self.siting_result.layers_used,
                "layers_unavailable": self.siting_result.layers_unavailable,
                "criteria_weights": {k: round(v, 4) for k, v in self.siting_result.weights.items()},
                "constraints_applied": self.siting_result.constraints,
                "feasible_cells": int(self.siting_result.feasible.sum()),
                # Where a pond may *not* go, and how much of that veto was
                # actually available. A reader needs to know whether existing
                # tanks and rivers were ruled out or merely unchecked.
                "exclusions": (None if self.exclusions is None else self.exclusions.as_dict()),
            },
            "environment": self.enrichment.as_dict(),
            "recommended_site": recommended,
            "candidate_sites": self.sites,
            "warnings": self.warnings,
        }
        # A plain-language reading of the recommendation (FR-14), generated from
        # the values just computed. Deterministic templates, no language model:
        # the same analysis must always produce the same words, and every clause
        # has to trace to a named field.
        body["explanation"] = explain.explain_analysis(body)
        # Attached here rather than by the endpoint, so both the synchronous and
        # the job path get them from one place. A drawn area has no uploaded
        # lines to echo, so its contours are traced from the grid instead.
        if self.options.include_contours:
            body["contours"] = (
                contours_to_geojson(src.parsed.lines)
                if src.parsed is not None
                else _display_contours(self.dem)
            )
        return body

    def _grid_report(self) -> dict[str, Any]:
        """The grid's own facts: the interpolation report for a contour map, the
        fetched grid's provenance for a drawn area."""
        if self.source.interpolation is not None:
            return self.source.interpolation.as_dict()
        prov = self.dem.provenance
        rows, cols = self.dem.shape
        return {
            "grid_resolution_m": self.dem.cell_size_m,
            "grid_resolution_derived": False,
            "grid_size": [cols, rows],
            "grid_cells": rows * cols,
            "interpolation_method": (
                "none: Copernicus GLO-30 resampled bilinearly onto the working grid"
            ),
            "coverage_pct": prov.get("coverage_pct"),
            "interpolated_elevation_min_m": prov.get("elevation_min_m"),
            "interpolated_elevation_max_m": prov.get("elevation_max_m"),
            "interpolated_relief_m": prov.get("relief_m"),
        }


class StageReporter(Protocol):
    """What the pipeline needs in order to report progress.

    Deliberately the subset of `services.jobs.JobProgress` that matters here, so
    a job can be passed straight in while the pipeline keeps no knowledge of job
    state, Celery or Redis.
    """

    def start_step(self, name: str) -> None: ...
    def finish_step(self, name: str) -> None: ...
    def fail_step(self, name: str, reason: str) -> None: ...


def _stage_runner(reporter: StageReporter | None, timings: dict[str, float]) -> Any:
    """Time a stage and tell the reporter it started, finished or failed."""

    def stage(name: str, fn: Any) -> Any:
        t = time.perf_counter()
        if reporter is not None:
            reporter.start_step(name)
        try:
            out = fn()
        except Exception as exc:
            # The reporter is told before the exception propagates, so a failed
            # job records *which* stage died rather than only that it did.
            if reporter is not None:
                reporter.fail_step(name, f"{type(exc).__name__}: {exc}")
            raise
        timings[name] = time.perf_counter() - t
        if reporter is not None:
            reporter.finish_step(name)
        return out

    return stage


def analyze_contour_map(
    data: bytes,
    filename: str | None = None,
    options: ContourAnalysisOptions | None = None,
    reporter: StageReporter | None = None,
) -> ContourAnalysis:
    """Run the full pipeline on an uploaded contour map.

    Raises `ContourParseError` (with a specific reason) on unusable input; every
    other failure mode is reported as a warning on an otherwise valid result, so
    a partially-degraded analysis still answers the question.

    `reporter`, when given, is told which stage is starting and finishing. It is
    optional so the synchronous endpoint stays exactly as it was -- the async job
    path is the only caller that has anywhere to report to.
    """
    opts = options or ContourAnalysisOptions()
    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    stage = _stage_runner(reporter, timings)

    parsed = stage("parse", lambda: parse_contour_file(data, filename))
    dem, interp = stage(
        "interpolate", lambda: contours_to_dem(parsed, cell_size_m=opts.cell_size_m)
    )
    source = TerrainSource(
        kind="uploaded_contour_map",
        bounds=parsed.bounds,
        grid_bounds=parsed.bounds,
        info=_contour_source_info(parsed, dem, filename),
        parsed=parsed,
        interpolation=interp,
        filename=filename,
        size_bytes=len(data),
        warnings=list(parsed.warnings),
    )
    return _analyse(dem, source, opts, stage, timings, t_total)


def analyze_area(
    bbox: Sequence[Any],
    options: ContourAnalysisOptions | None = None,
    reporter: StageReporter | None = None,
    *,
    max_km2: float | None = None,
    store: Path | None = None,
    fetch: Any = None,
) -> ContourAnalysis:
    """Run the full pipeline on a rectangle drawn on the map.

    `bbox` is `[min_lon, min_lat, max_lon, max_lat]`. It is validated before any
    stage runs -- a bad box costs no fetch -- and raises `area.AreaError` (or its
    `AreaTooLargeError`) saying exactly what is wrong. Terrain comes from
    Copernicus GLO-30 at its native 30 m, cached on disk; a Copernicus outage
    raises `ProviderUnavailableError` from the `terrain` stage.

    Sites are proposed only inside the rectangle. The grid extends
    `AOI_BUFFER_M` beyond it, so a catchment that starts outside the line the
    user drew is still measured whole.
    """
    from app.config import get_settings

    settings = get_settings()
    opts = options or ContourAnalysisOptions()
    cap = float(settings.MAX_AOI_KM2 if max_km2 is None else max_km2)
    box = area_service.validate_bbox(bbox, max_km2=cap)
    cache_dir = Path(settings.COG_STORE_PATH) if store is None else store

    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    stage = _stage_runner(reporter, timings)

    dem, was_cached = stage(
        "terrain",
        lambda: copernicus_aws.cached_fetch_dem(
            box.bounds, store=cache_dir, buffer_m=float(settings.AOI_BUFFER_M), fetch=fetch
        ),
    )
    inside = area_service.inside_mask(dem, box)
    if not inside.any():
        raise area_service.AreaError(
            "the selected rectangle does not cover a single cell of the terrain grid"
        )

    warnings: list[str] = []
    lon, lat = box.bounds.centroid
    if not is_within_india(lon, lat):
        warnings.append(
            "the selected area lies outside India; it is analysed all the same, but "
            "the Indian runoff cross-checks and the village register do not apply"
        )
    source = TerrainSource(
        kind="copernicus_glo30",
        bounds=box.bounds,
        grid_bounds=area_service.grid_bounds_4326(dem),
        info=_area_source_info(box, dem, was_cached, int(inside.sum())),
        area=box,
        warnings=warnings,
    )
    return _analyse(dem, source, opts, stage, timings, t_total, site_mask=inside)


def _analyse(
    dem: DemGrid,
    source: TerrainSource,
    opts: ContourAnalysisOptions,
    stage: Any,
    timings: dict[str, float],
    t_total: float,
    site_mask: npt.NDArray[np.bool_] | None = None,
) -> ContourAnalysis:
    """Everything below the DEM, shared by both ways in."""
    conditioned = stage("condition", lambda: hyd.fill_depressions(dem))
    flow = stage("flow_routing", lambda: hyd.build_flow(dem, conditioned))

    # Enrichment from the AOI's own position (HLD §6.10.4). Each layer fails
    # independently: a provider outage drops the tier, never the analysis.
    enrichment = stage(
        "enrichment",
        lambda: fetch_enrichment(
            source.grid_bounds,
            dem,
            rainfall_years=opts.rainfall_years,
            enabled=opts.enrich,
            budget_s=opts.enrichment_budget_s,
        ),
    )
    availability = enrichment.availability_grid()
    # The hard veto on where a pond may go: existing tanks, rivers, buildings,
    # roads. Built here because it needs the flow grid for its terrain fallback.
    exclusions = enrichment.siting_exclusions(dem, flow)
    # A drawn area holds siting inside the rectangle. The buffer around it is
    # there for the catchments, not for the sites: a pond outside the land the
    # person chose would answer a question they did not ask. Kept out of the
    # exclusion audit, which reports hazards, not the edge of the selection.
    excluded = exclusions.mask if site_mask is None else (exclusions.mask | ~site_mask)

    result = stage(
        "siting",
        lambda: siting.identify_pond_sites(
            dem,
            conditioned,
            flow,
            max_sites=opts.max_sites,
            max_slope_pct=opts.max_slope_pct,
            min_upstream_ha=opts.min_upstream_ha,
            weights_override=opts.weights_override,
            score_threshold=opts.score_threshold,
            min_separation_m=opts.min_separation_m,
            min_depression_depth_m=opts.min_depression_depth_m,
            availability=availability,
            excluded=excluded,
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

    warnings = [*source.warnings, *conditioned.warnings, *result.warnings]
    if enrichment.rainfall:
        warnings.extend(enrichment.rainfall.warnings)
    for failure in enrichment.failures:
        warnings.append(
            f"{failure['layer']} unavailable ({failure['reason']}); the analysis "
            f"continued at tier '{enrichment.tier}'"
        )
    if not sites:
        where = " inside the selected area" if site_mask is not None else ""
        warnings.append(
            f"no candidate pond site met the constraints{where}; the analysis of the "
            "terrain itself is still reported above"
        )

    return ContourAnalysis(
        analysis_id=uuid.uuid4().hex[:16],
        source=source,
        dem=dem,
        conditioned=conditioned,
        flow=flow,
        siting_result=result,
        enrichment=enrichment,
        sites=sites,
        elapsed_s=time.perf_counter() - t_total,
        stage_timings=timings,
        exclusions=exclusions,
        options=opts,
        warnings=warnings,
    )


def _contour_source_info(
    parsed: ParsedContours, dem: DemGrid, filename: str | None
) -> dict[str, Any]:
    return {
        "kind": "uploaded_contour_map",
        "dataset": "Uploaded contour map (KML/KMZ)",
        "filename": filename,
        "resolution_m": dem.cell_size_m,
        "working_crs_epsg": dem.epsg,
        "bounds_4326": list(parsed.bounds.as_tuple()),
        "contour_interval_m": parsed.interval_m,
        "relief_m": round(float(dem.relief_m), 2),
    }


def _area_source_info(
    box: area_service.AnalysisArea, dem: DemGrid, was_cached: bool, cells_inside: int
) -> dict[str, Any]:
    prov = dem.provenance
    rows, cols = dem.shape
    buffer_m = prov.get("buffer_m")
    return {
        "kind": "copernicus_glo30",
        "dataset": "Copernicus DEM GLO-30",
        "provider": copernicus_aws.PROVENANCE.provider,
        "licence": copernicus_aws.PROVENANCE.licence,
        "resolution_m": dem.cell_size_m,
        "working_crs_epsg": dem.epsg,
        "bounds_4326": box.as_list(),
        "area_km2": box.area_km2,
        "analysed_bounds_4326": prov.get("buffered_bounds_4326"),
        "buffer_m": buffer_m,
        "grid_size": [cols, rows],
        "cells_inside_area": cells_inside,
        "tiles_used": prov.get("tiles_used", []),
        "cached": was_cached,
        "elevation_min_m": prov.get("elevation_min_m"),
        "elevation_max_m": prov.get("elevation_max_m"),
        "relief_m": prov.get("relief_m"),
        "coverage_pct": prov.get("coverage_pct"),
        "note": (
            f"Terrain is the Copernicus global {dem.cell_size_m:g} m elevation model, "
            "coarser than a surveyed contour map: a 141 m pond spans about five cells. "
            "Sites are proposed only inside the drawn rectangle; the grid extends "
            f"{buffer_m if buffer_m is not None else 'a buffer'} m beyond it so "
            "catchments that begin outside are measured whole."
        ),
    }


def _summary(recommended: dict[str, Any] | None) -> dict[str, Any]:
    """The three results the brief asks for: pond location, catchment area and
    the water volume that can be collected -- for the recommended site, at the
    top of the response where a reader looks first."""
    if recommended is None:
        return {
            "available": False,
            "reason": "no candidate pond site met the constraints",
            "pond_location": None,
            "catchment_area_ha": None,
            "expected_water_volume_m3": None,
        }

    loc = recommended.get("location") or {}
    metrics = (recommended.get("catchment") or {}).get("metrics") or {}
    water = recommended.get("expected_water") or _expected_water(recommended)
    return {
        "available": True,
        "site_rank": recommended.get("rank"),
        "suitability_score": recommended.get("suitability_score"),
        "pond_location": {"lat": loc.get("lat"), "lon": loc.get("lon")},
        "catchment_area_ha": metrics.get("area_ha"),
        "catchment_area_km2": metrics.get("area_km2"),
        "expected_water_volume_m3": water["volume_m3"],
        "expected_water_volume_basis": water["basis"],
        "expected_water_volume_limited_by": water["limited_by"],
        "pond_capacity_m3": water["pond_capacity_m3"],
        "annual_inflow_m3": water["annual_inflow_m3"],
    }


def _expected_water(site: dict[str, Any]) -> dict[str, Any]:
    """The water one site's pond can collect in a normal year, with its basis.

    The pond's live storage capped by the catchment's 75 % dependable inflow.
    Either alone would overstate it: storage the catchment cannot fill in a
    normal year is not collectable, and inflow beyond what the pond holds runs
    over the spillway. Per site only -- catchments nest, so a total across sites
    would count the same water twice.
    """
    pond = site.get("pond") or {}
    design = pond.get("recommended") if pond.get("available") else None
    runoff = site.get("runoff") or {}

    live = None if design is None else design.get("live_storage_m3")
    gross = None if design is None else design.get("gross_capacity_m3")
    mean = dependable = None
    if runoff.get("available"):
        mean = (runoff.get("annual_mean") or {}).get("runoff_volume_m3")
        dependable = (runoff.get("design_75_percent_dependable") or {}).get("runoff_volume_m3")

    volume: float | None
    if live is not None and dependable is not None:
        volume = min(float(live), float(dependable))
        limited_by: str | None = "storage" if float(live) <= float(dependable) else "inflow"
        basis = (
            "the pond's live storage, capped by the catchment's 75 % dependable annual "
            "inflow: the water it can collect in three years of four"
        )
    elif live is not None:
        volume, limited_by = float(live), "storage"
        basis = (
            "the pond's live storage; rainfall was unavailable, so the inflow that "
            "fills it could not be checked"
        )
    else:
        volume, limited_by = None, None
        basis = str(pond.get("reason") or "no pond could be sized at this site")

    return {
        "volume_m3": None if volume is None else round(volume, 1),
        "limited_by": limited_by,
        "basis": basis,
        "pond_capacity_m3": {"gross": gross, "live": live},
        "annual_inflow_m3": {"mean": mean, "dependable_75_percent": dependable},
    }


def _display_interval_m(relief_m: float) -> float:
    for step in DISPLAY_INTERVALS_M:
        if relief_m / step <= MAX_DISPLAY_LEVELS:
            return step
    return DISPLAY_INTERVALS_M[-1]


def _display_contours(dem: DemGrid) -> dict[str, Any] | None:
    """Contours traced from the grid, for an area with no uploaded lines to echo."""
    try:
        generated = contour_generator.generate(
            dem.elevation,
            transform=dem.transform,
            epsg=dem.epsg,
            cell_size_m=dem.cell_size_m,
            interval_m=_display_interval_m(max(float(dem.relief_m), 1.0)),
        )
    except contour_generator.ContourGenerationError:
        return None
    return contour_lines_to_geojson(generated.lines, generated.epsg)


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
    payload["pond"] = _pond_payload(
        dem,
        site,
        payload["runoff"],
        buildable,
        enrichment=enrichment,
        catchment_area_m2=catchment.area_m2,
    )
    # Per site, so the map can label every marker with what it would collect,
    # not only the recommended one in `summary`.
    payload["expected_water"] = _expected_water(payload)
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
    enrichment: Enrichment | None = None,
    catchment_area_m2: float | None = None,
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
    body["water_balance"] = _water_balance_payload(design, runoff, enrichment, catchment_area_m2)
    return body


def _water_balance_payload(
    design: Any,
    runoff: dict[str, Any] | None,
    enrichment: Enrichment | None,
    catchment_area_m2: float | None,
) -> dict[str, Any]:
    """Month-by-month storage for this pond (FR-13).

    Capacity says how much it holds; this says whether there is water in April,
    which is the question a village actually asks. Reported as unavailable rather
    than approximated when an input is missing -- a balance without evaporation
    would overstate how long the pond lasts, which is the wrong direction to be
    wrong in.
    """
    if not runoff or not runoff.get("available") or catchment_area_m2 is None:
        return {"available": False, "reason": "needs a runoff estimate for the catchment"}

    monthly_runoff = runoff.get("monthly_mean_runoff_mm")
    et0 = None
    soil_group = None
    if enrichment is not None and enrichment.rainfall is not None:
        et0 = enrichment.rainfall.et0_monthly_mm
    if runoff.get("curve_number"):
        soil_group = runoff["curve_number"].get("hydrologic_soil_group")

    if not monthly_runoff or not et0:
        missing = "reference evapotranspiration" if monthly_runoff else "monthly runoff"
        return {
            "available": False,
            # Not "a degraded tier": this happened on full-tier runs whenever the
            # rainfall source that answered carried no ET0. Say what is missing.
            "reason": (f"needs {missing}, which the rainfall data on this run did not provide"),
        }

    try:
        balance = water_balance.simulate(
            monthly_runoff_mm=list(monthly_runoff),
            catchment_area_m2=catchment_area_m2,
            monthly_et0_mm=list(et0),
            bottom_length_m=design.bottom_length_m,
            bottom_width_m=design.bottom_width_m,
            depth_m=design.depth_m,
            side_slope=design.side_slope_h_per_v,
            capacity_m3=design.gross_capacity_m3,
            soil_group=soil_group,
        )
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, **balance.as_dict()}


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
