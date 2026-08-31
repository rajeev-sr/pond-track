"""Request/response models for the contour-map endpoints (MC-1).

Typed at the boundary so FastAPI generates a self-describing OpenAPI schema --
`/docs` then documents the API without a separate hand-written spec (MC-23).

Deeply nested provenance blocks are typed as `dict[str, Any]` on purpose: their
shape is set by the services that produce them (parser, interpolator, hydrology),
and duplicating those structures here would create two definitions to keep in
step. The blocks that a caller *acts on* -- sites, catchment metrics -- are fully
typed.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.schemas._contour_example import ANALYZE_CONTOUR_EXAMPLE
from app.services import siting
from app.services.contour_analysis import (
    DEFAULT_SNAP_RADIUS_M,
    DEFAULT_STREAM_THRESHOLD_HA,
    ContourAnalysisOptions,
)
from app.services.interpolate import MAX_CELL_M, MIN_CELL_M


class ContourAnalysisRequest(BaseModel):
    """Tuning knobs for `POST /analyzeContour`. Every field is optional.

    Sent as individual multipart form fields alongside the file. Defaults are
    either derived from the uploaded map or documented constants -- nothing here
    is specific to any particular contour map.
    """

    cell_size_m: Annotated[
        float | None,
        Field(
            default=None,
            ge=MIN_CELL_M,
            le=MAX_CELL_M,
            description=(
                "Interpolation grid resolution in metres. Omit to derive it from the "
                "contour geometry: mean contour spacing (area / total line length) "
                "halved, then snapped to a legible value."
            ),
        ),
    ]
    max_sites: Annotated[
        int, Field(default=5, ge=1, le=25, description="Maximum ranked sites to return.")
    ]
    max_slope_pct: Annotated[
        float,
        Field(
            default=siting.DEFAULT_MAX_SLOPE_PCT,
            gt=0.0,
            le=100.0,
            description="Reject cells steeper than this; excavation cost rises sharply.",
        ),
    ]
    min_upstream_ha: Annotated[
        float,
        Field(
            default=siting.DEFAULT_MIN_UPSTREAM_HA,
            ge=0.0,
            description=(
                "A site must receive runoff from at least this much upstream area, "
                "otherwise it is a hollow no water reaches."
            ),
        ),
    ]
    score_threshold: Annotated[
        float,
        Field(
            default=siting.DEFAULT_SCORE_THRESHOLD,
            ge=0.0,
            le=1.0,
            description=(
                "Suitability floor for *channel* candidates. Natural depressions are "
                "deliberately not gated by it, so a real bowl is never hidden."
            ),
        ),
    ]
    min_separation_m: Annotated[
        float,
        Field(
            default=siting.DEFAULT_MIN_SEPARATION_M,
            ge=0.0,
            description="Two sites closer than this describe the same structure.",
        ),
    ]
    min_depression_depth_m: Annotated[
        float,
        Field(
            default=siting.DEFAULT_MIN_DEPRESSION_DEPTH_M,
            ge=0.0,
            description="A hollow shallower than this is survey noise, not a landform.",
        ),
    ]
    snap_radius_m: Annotated[
        float,
        Field(
            default=DEFAULT_SNAP_RADIUS_M,
            ge=0.0,
            description=(
                "How far a pour point may be nudged onto the drainage line. Without "
                "snapping, a point just off the channel yields a catchment wrong by "
                "orders of magnitude."
            ),
        ),
    ]
    include_catchment_geometry: Annotated[
        bool,
        Field(default=True, description="Include each catchment's GeoJSON polygon."),
    ]
    include_contours: Annotated[
        bool,
        Field(
            default=False,
            description="Echo the parsed contours as GeoJSON. Large; off by default.",
        ),
    ]
    stream_threshold_ha: Annotated[
        float,
        Field(
            default=DEFAULT_STREAM_THRESHOLD_HA,
            gt=0.0,
            description="Upstream area above which a cell counts as stream network.",
        ),
    ]
    enrich: Annotated[
        bool,
        Field(
            default=True,
            description=(
                "Fetch soil, land cover and rainfall from the area's own location. "
                "Set false for a terrain-only answer with no network access."
            ),
        ),
    ]
    rainfall_years: Annotated[
        int,
        Field(default=30, ge=1, le=70, description="Years of rainfall record to use."),
    ]

    def to_options(self) -> ContourAnalysisOptions:
        return ContourAnalysisOptions(**self.model_dump())


class CriterionBreakdown(BaseModel):
    criterion: str
    raw_value: float
    normalised: float = Field(description="Scaled to [0,1] across the candidate set.")
    weight: float = Field(description="AHP weight, renormalised over present criteria.")
    contribution: float = Field(description="weight x normalised")


class CatchmentMetrics(BaseModel):
    area_m2: float
    area_ha: float
    area_km2: float
    cell_count: int
    perimeter_m: float
    elevation_min_m: float
    elevation_max_m: float
    relief_m: float
    mean_slope_pct: float | None = None
    max_slope_pct: float | None = None
    longest_flow_path_m: float
    time_of_concentration_min: float | None = Field(
        default=None, description="Kirpich (1940), minutes."
    )
    form_factor: float | None = Field(
        default=None, description="Horton: area / length^2. Low = elongated."
    )
    compactness_coefficient: float | None = Field(
        default=None, description="Gravelius: 1.0 is a circle; higher is more ragged."
    )
    outlet_accumulation_cells: int
    touches_grid_edge: bool


class SiteCatchment(BaseModel):
    metrics: CatchmentMetrics
    pour_point: dict[str, Any] = Field(
        description="GeoJSON Point plus grid indices of the delineation outlet."
    )
    snapped: dict[str, Any]
    quality: dict[str, Any]
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON (Multi)Polygon of the catchment, EPSG:4326."
    )


class CandidateSiteOut(BaseModel):
    rank: int
    suitability_score: float = Field(description="0-100, relative to the other candidates.")
    site_kind: Literal["natural_depression", "channel_position"]
    location: dict[str, Any]
    terrain: dict[str, Any]
    region: dict[str, Any] = Field(description="Extent of the candidate landform.")
    catchment_pour_point: dict[str, int]
    criteria_breakdown: list[CriterionBreakdown]
    catchment: SiteCatchment
    runoff: dict[str, Any] | None = Field(
        default=None,
        description=(
            "SCS-CN runoff for this catchment: composite curve number with its "
            "land-cover breakdown, annual mean, and the 75 % dependable design "
            "figure. `available: false` with a stated reason when rainfall was not "
            "reachable."
        ),
    )
    pond: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Recommended depth, plan dimensions and capacity, the stage-storage "
            "curve from the terrain, the buildable footprint, and **which "
            "constraint bound the answer** (`binding_constraint`)."
        ),
    )


class ContourAnalysisResponse(BaseModel):
    """The full result of `POST /analyzeContour`."""

    # A real captured response, so `/docs` shows what the API actually returns
    # rather than a schema skeleton (MC-23).
    model_config = {"json_schema_extra": {"example": ANALYZE_CONTOUR_EXAMPLE}}

    analysis_id: str
    generated_at: str
    elapsed_s: float
    stage_timings_s: dict[str, float]
    dem_id: str = Field(
        description=(
            "Handle on the interpolated DEM behind this analysis. Pass it to "
            "`POST /terrain/derivatives` to get slope and hillshade tiles without "
            "uploading the file again. Held in memory, so it does not survive a "
            "restart."
        )
    )
    input: dict[str, Any] = Field(description="Filename, size, and the options applied.")
    contour_map: dict[str, Any] = Field(
        description=(
            "What was read from the file: elevation strategy used, line and vertex "
            "counts, derived contour interval, extent, and working CRS."
        )
    )
    interpolated_terrain: dict[str, Any] = Field(
        description="Grid resolution and how it was derived, plus conditioning results."
    )
    area_of_interest: dict[str, Any] = Field(description="GeoJSON bbox of the contours.")
    suitability: dict[str, Any] = Field(
        description="Analysis tier, layers used and unavailable, weights, constraints."
    )
    environment: dict[str, Any] = Field(
        description=(
            "Layers fetched from the AOI's own location: soil texture and "
            "Hydrologic Soil Group, land cover, and 30 years of rainfall with "
            "design statistics. Each is fetched independently, so a provider "
            "outage drops the analysis tier and is listed under "
            "`provider_failures` rather than failing the request."
        )
    )
    recommended_site: CandidateSiteOut | None
    candidate_sites: list[CandidateSiteOut]
    contours: dict[str, Any] | None = Field(
        default=None, description="Parsed contours as GeoJSON, when requested."
    )
    warnings: list[str]
    explanation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "A plain-language reading of the recommendation (FR-14): what was "
            "chosen, why it scored as it did, what limits the pond, and the "
            "caveats. Generated by deterministic templates over the computed "
            "values -- **no language model** -- so the same analysis always "
            "produces the same words and every clause traces to a named field."
        ),
    )


class ContourMapUploadResponse(BaseModel):
    """Result of `POST /terrain/contour-map`: parse and interpolate only."""

    dem_id: str
    contour_map: dict[str, Any]
    interpolated_terrain: dict[str, Any]
    area_of_interest: dict[str, Any]
    warnings: list[str]
