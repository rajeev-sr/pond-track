"""Bundled GeoJSON export of a finished analysis (M7-4).

    GET /api/v1/export/{job_id}?format=geojson

One file a reader can drop into QGIS and see the whole answer: the surveyed
extent, the contours, every candidate site, the catchment that drains to each,
and the indicative pond footprint. The API already returns all of it, but spread
across a nested result document and three follow-up endpoints -- which is the
right shape for a browser and the wrong shape for someone who wants the layers.

Every feature carries a `layer` property, because that is what QGIS and
`geopandas` group by. A FeatureCollection whose members are only distinguishable
by their geometry type forces the reader to reconstruct the grouping the API
already knew.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.core.errors import NotFoundProblem, UnanswerableProblem
from app.core.logging import get_logger
from app.services.job_store import get_store

log = get_logger("api.export")

router = APIRouter(prefix="/export", tags=["export"])

#: Metres per degree of latitude, for the indicative pond rectangle. Longitude is
#: scaled by cos(lat) at the site's own latitude -- a global constant visibly
#: stretches a 141 m box at 21 N.
M_PER_DEG_LAT = 111_320.0


def _feature(
    geometry: dict[str, Any] | None, layer: str, **properties: Any
) -> dict[str, Any] | None:
    if not geometry:
        return None
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {"layer": layer, **properties},
    }


def _pond_rectangle(site: dict[str, Any]) -> dict[str, Any] | None:
    """The recommended pond as a north-aligned rectangle of the right size.

    The design fixes plan dimensions, not an orientation -- nothing in the model
    chooses one -- so the footprint is honest about scale and position and
    silent about bearing. The property says so, because a polygon in a GIS file
    looks surveyed whether or not it is.
    """
    pond = site.get("pond") or {}
    design = pond.get("recommended") if pond.get("available") else None
    location = site.get("location") or {}
    if not design or "lon" not in location:
        return None

    lon, lat = float(location["lon"]), float(location["lat"])
    d_lat = float(design["top_width_m"]) / 2.0 / M_PER_DEG_LAT
    d_lon = (
        float(design["top_length_m"])
        / 2.0
        / (M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    )
    ring = [
        [lon - d_lon, lat - d_lat],
        [lon + d_lon, lat - d_lat],
        [lon + d_lon, lat + d_lat],
        [lon - d_lon, lat + d_lat],
        [lon - d_lon, lat - d_lat],
    ]
    return _feature(
        {"type": "Polygon", "coordinates": [ring]},
        "pond_footprint",
        rank=site.get("rank"),
        depth_m=design.get("depth_m"),
        top_length_m=design.get("top_length_m"),
        top_width_m=design.get("top_width_m"),
        gross_capacity_m3=design.get("gross_capacity_m3"),
        estimated_cost_inr=design.get("estimated_cost_inr"),
        binding_constraint=pond.get("binding_constraint"),
        orientation="indicative -- the model sizes the pond but does not choose a bearing",
    )


def build_collection(result: dict[str, Any]) -> dict[str, Any]:
    """Every geometry in an analysis result as one flat FeatureCollection."""
    features: list[dict[str, Any]] = []

    aoi = _feature(
        result.get("area_of_interest"),
        "survey_extent",
        note="the extent of the uploaded contour map; nothing outside it was analysed",
    )
    if aoi:
        features.append(aoi)

    # Contours arrive as their own FeatureCollection when they were requested.
    contours = result.get("contours") or {}
    for feature in contours.get("features", []) if isinstance(contours, dict) else []:
        properties = dict(feature.get("properties") or {})
        properties["layer"] = "contour"
        features.append({**feature, "properties": properties})

    for site in result.get("candidate_sites") or []:
        location = site.get("location") or {}
        if "lon" in location:
            point = _feature(
                {"type": "Point", "coordinates": [location["lon"], location["lat"]]},
                "candidate_site",
                rank=site.get("rank"),
                suitability_score=site.get("suitability_score"),
                site_kind=site.get("site_kind"),
            )
            if point:
                features.append(point)

        catchment = site.get("catchment") or {}
        metrics = catchment.get("metrics") or {}
        boundary = _feature(
            catchment.get("geometry"),
            "catchment",
            rank=site.get("rank"),
            area_ha=metrics.get("area_ha"),
            time_of_concentration_min=metrics.get("time_of_concentration_min"),
            # A catchment clipped by the survey edge is a lower bound, and a
            # reader measuring it in QGIS has no other way to know.
            touches_survey_edge=(catchment.get("quality") or {}).get("touches_survey_edge"),
        )
        if boundary:
            features.append(boundary)

        footprint = _pond_rectangle(site)
        if footprint:
            features.append(footprint)

    return {
        "type": "FeatureCollection",
        # Stated explicitly: a bare FeatureCollection is assumed to be 4326 by
        # convention, and this one is, but the file is meant to be read by a GIS
        # months later by someone who was not told.
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "properties": {
            "analysis_id": result.get("analysis_id"),
            "generated_at": result.get("generated_at"),
            "analysis_tier": (result.get("environment") or {}).get("analysis_tier"),
            "layers": sorted({f["properties"]["layer"] for f in features}),
            "feature_count": len(features),
        },
        "features": features,
    }


@router.get(
    "/{job_id}",
    summary="Every result layer as one GeoJSON file (M7-4)",
    description=(
        "Bundles the survey extent, contours, candidate sites, catchments and "
        "indicative pond footprints of a finished analysis into a single "
        "FeatureCollection, ready to open in QGIS.\n\n"
        "Each feature carries a `layer` property, which is what QGIS and "
        "`geopandas` group by; without it the reader has to reconstruct a "
        "grouping the API already knew. Served as a download with a filename, "
        "since the point is a file rather than a response body.\n\n"
        "Available for `partial` as well as `done`: a ranking computed without "
        "soil data is still worth exporting, and `properties.analysis_tier` "
        "records which it was.\n\n"
        "Contours are only present if the analysis was asked for them "
        "(`include_contours=true`); everything else is always there."
    ),
    responses={200: {"content": {"application/geo+json": {}}}},
)
async def export_analysis(
    job_id: str,
    format: Annotated[
        str, Query(description="Only `geojson` for now; the parameter fixes the contract.")
    ] = "geojson",
) -> Response:
    if format != "geojson":
        raise UnanswerableProblem(
            detail=f"format {format!r} is not supported; this endpoint emits geojson.",
            supported=["geojson"],
        )

    record = get_store().get(job_id)
    if record is None:
        raise NotFoundProblem(
            detail=(
                f"no analysis job with id {job_id!r}. Jobs are kept for 24 hours; "
                "run one via POST /api/v1/analysis."
            ),
            job_id=job_id,
        )
    state = record.progress.get("state")
    if state not in ("done", "partial") or not record.result:
        raise UnanswerableProblem(
            detail=(
                f"job {job_id!r} is {state!r}, so there is nothing to export. Poll "
                f"/api/v1/analysis/{job_id}/status until `is_terminal` is true."
            ),
            job_id=job_id,
            state=state,
        )

    collection = build_collection(record.result)
    log.info("analysis exported", job_id=job_id, features=collection["properties"]["feature_count"])
    return Response(
        content=json.dumps(collection),
        media_type="application/geo+json",
        headers={
            "Content-Disposition": f'attachment; filename="contour-analysis-{job_id[:8]}.geojson"'
        },
    )
