"""Contour-map endpoints (MC-10, MC-11).

    POST /api/v1/analyzeContour          one-shot: upload -> pond sites + catchments
    POST /api/v1/findCatchment           alias of the above
    POST /api/v1/terrain/contour-map     parse + interpolate only, returns a dem_id
    GET  /api/v1/terrain/contour-map/{dem_id}/contours
                                         echo the parsed contours as GeoJSON

The routes stay thin: they validate, delegate to `services.contour_analysis`, and
translate a `ContourParseError` into an RFC 7807 problem with the parser's own
specific reason. No domain logic lives here (HLD 2.1).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.errors import NotFoundProblem, UnanswerableProblem, ValidationProblem
from app.core.logging import get_logger
from app.providers.elevation.contour_kml import (
    MAX_UPLOAD_BYTES,
    ContourParseError,
    parse_contour_file,
)
from app.schemas.contour import ContourAnalysisResponse, ContourMapUploadResponse
from app.services import siting
from app.services.contour_analysis import (
    DEFAULT_SNAP_RADIUS_M,
    DEFAULT_STREAM_THRESHOLD_HA,
    ContourAnalysisOptions,
    analyze_contour_map,
)
from app.services.geometry import bbox_geojson, contours_to_geojson
from app.services.interpolate import MAX_CELL_M, MIN_CELL_M, contours_to_dem

router = APIRouter(tags=["contour"])
log = get_logger("contour")

#: Parsed uploads, keyed by dem_id, so the two-step flow can retrieve contours
#: without re-uploading. In-process and bounded: this is a local single-node
#: deployment (see the project's local-only decision), and M6 replaces it with
#: the Redis/PostGIS-backed store the async job architecture already specifies.
_PARSED_CACHE: dict[str, Any] = {}
_CACHE_LIMIT = 16

ACCEPTED_SUFFIXES = (".kml", ".kmz", ".xml")


#: Read granularity for uploads. Small enough that the size limit is enforced
#: after ~1 MB rather than after the whole body is in memory.
UPLOAD_CHUNK_BYTES = 1024 * 1024

#: Control characters are stripped from filenames before they reach a log line or
#: a response body: the name is attacker-controlled text, and a raw newline in it
#: forges log entries.
_FILENAME_SAFE = 96


def _safe_filename(raw: str | None) -> str:
    name = (raw or "upload").strip()
    cleaned = "".join(c for c in name if c.isprintable() and c not in "\r\n\t")
    # Only the basename matters: we never write the file, but a path in a log
    # line or an error body is noise at best and misleading at worst.
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    return cleaned[:_FILENAME_SAFE] or "upload"


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    """Read and pre-validate an uploaded file.

    Reads in chunks and aborts as soon as the cap is exceeded. `await file.read()`
    would buffer the entire body *before* the size check, so a 500 MB POST would
    be fully materialised in memory only to be rejected -- the check would exist
    while the denial-of-service it was meant to prevent still worked.
    """
    name = _safe_filename(file.filename)
    if not name.lower().endswith(ACCEPTED_SUFFIXES):
        raise ValidationProblem(
            f"{name!r} does not look like a contour map. Expected a "
            f"{', '.join(ACCEPTED_SUFFIXES)} file.",
            accepted_extensions=list(ACCEPTED_SUFFIXES),
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValidationProblem(
                f"{name!r} exceeds the {MAX_UPLOAD_BYTES / 1e6:.0f} MB limit; "
                "upload aborted without reading the rest.",
                limit_bytes=MAX_UPLOAD_BYTES,
            )
        chunks.append(chunk)

    if total == 0:
        raise ValidationProblem(f"{name!r} is empty.")
    return b"".join(chunks), name


def _as_unanswerable(exc: ContourParseError, filename: str) -> UnanswerableProblem:
    """A parse failure is a 422, not a 400.

    The request is well-formed -- a file was uploaded correctly -- but the file's
    *contents* make the question unanswerable. Keeping the two apart lets the
    caller show the parser's actual reason instead of "invalid input" (HLD 5.1).
    """
    return UnanswerableProblem(str(exc), filename=filename)


def _remember(parsed: Any, dem: Any, report: Any) -> str:
    dem_id = uuid.uuid4().hex[:16]
    if len(_PARSED_CACHE) >= _CACHE_LIMIT:
        _PARSED_CACHE.pop(next(iter(_PARSED_CACHE)))
    _PARSED_CACHE[dem_id] = {"parsed": parsed, "dem": dem, "report": report}
    return dem_id


#: Declared as explicit `Form(...)` fields rather than a Pydantic model bound with
#: `Form()`: alongside a `File()` parameter FastAPI nests the model as a single
#: `options` field instead of flattening it, which makes the natural call
#: (`curl -F max_sites=3`) fail with "options: Field required". Spelling the
#: fields out also gives each one its own documented input in Swagger.
def _options_form(
    cell_size_m: Annotated[
        float | None,
        Form(
            ge=MIN_CELL_M,
            le=MAX_CELL_M,
            description=(
                "Interpolation grid resolution in metres. Omit to derive it from the "
                "contour geometry: mean spacing (area / total line length), halved, "
                "then snapped to a legible value."
            ),
        ),
    ] = None,
    max_sites: Annotated[int, Form(ge=1, le=25, description="Maximum ranked sites to return.")] = 5,
    max_slope_pct: Annotated[
        float,
        Form(gt=0.0, le=100.0, description="Reject cells steeper than this."),
    ] = siting.DEFAULT_MAX_SLOPE_PCT,
    min_upstream_ha: Annotated[
        float,
        Form(
            ge=0.0,
            description=("A site must receive runoff from at least this much upstream area."),
        ),
    ] = siting.DEFAULT_MIN_UPSTREAM_HA,
    score_threshold: Annotated[
        float,
        Form(
            ge=0.0,
            le=1.0,
            description=(
                "Suitability floor for *channel* candidates. Natural depressions are "
                "deliberately not gated by it, so a real bowl is never hidden."
            ),
        ),
    ] = siting.DEFAULT_SCORE_THRESHOLD,
    min_separation_m: Annotated[
        float,
        Form(ge=0.0, description="Two sites closer than this describe one structure."),
    ] = siting.DEFAULT_MIN_SEPARATION_M,
    min_depression_depth_m: Annotated[
        float,
        Form(ge=0.0, description="A hollow shallower than this is survey noise."),
    ] = siting.DEFAULT_MIN_DEPRESSION_DEPTH_M,
    snap_radius_m: Annotated[
        float,
        Form(
            ge=0.0,
            description=(
                "How far a pour point may be nudged onto the drainage line. Without "
                "snapping, a point just off the channel gives a catchment wrong by "
                "orders of magnitude."
            ),
        ),
    ] = DEFAULT_SNAP_RADIUS_M,
    include_catchment_geometry: Annotated[
        bool, Form(description="Include each catchment's GeoJSON polygon.")
    ] = True,
    include_contours: Annotated[
        bool, Form(description="Echo the parsed contours as GeoJSON. Large.")
    ] = False,
    enrich: Annotated[
        bool,
        Form(
            description=(
                "Fetch soil, land cover and rainfall from the area's own location. "
                "Set false for a terrain-only answer with no network access."
            )
        ),
    ] = True,
    rainfall_years: Annotated[
        int, Form(ge=1, le=70, description="Years of rainfall record to use.")
    ] = 30,
    stream_threshold_ha: Annotated[
        float, Form(gt=0.0, description="Upstream area above which a cell is stream.")
    ] = DEFAULT_STREAM_THRESHOLD_HA,
) -> ContourAnalysisOptions:
    return ContourAnalysisOptions(
        cell_size_m=cell_size_m,
        max_sites=max_sites,
        max_slope_pct=max_slope_pct,
        min_upstream_ha=min_upstream_ha,
        score_threshold=score_threshold,
        min_separation_m=min_separation_m,
        min_depression_depth_m=min_depression_depth_m,
        snap_radius_m=snap_radius_m,
        include_catchment_geometry=include_catchment_geometry,
        include_contours=include_contours,
        stream_threshold_ha=stream_threshold_ha,
        enrich=enrich,
        rainfall_years=rainfall_years,
    )


async def _run(file: UploadFile, opts: ContourAnalysisOptions) -> Any:
    data, name = await _read_upload(file)
    try:
        # Off the event loop. The pipeline is several seconds of raster work plus
        # blocking provider calls; running it inline would stall every other
        # request -- including /health -- for the whole analysis.
        result = await run_in_threadpool(analyze_contour_map, data, name, opts)
    except ContourParseError as exc:
        log.warning("contour_parse_failed", filename=name, detail=str(exc))
        raise _as_unanswerable(exc, name) from exc

    body = result.as_dict()
    if opts.include_contours:
        body["contours"] = contours_to_geojson(result.parsed.lines)
    log.info(
        "contour_analysis_complete",
        analysis_id=result.analysis_id,
        filename=name,
        lines=result.parsed.lines_parsed,
        sites=len(result.sites),
        elapsed_s=round(result.elapsed_s, 2),
    )
    return body


_ANALYZE_DESCRIPTION = (
    "Upload a contour map (KML or KMZ) and receive, in one response: what was read "
    "from the file, the interpolated terrain, ranked candidate pond sites with a "
    "per-criterion explanation, and the catchment draining to each one.\n\n"
    "Elevation is located by trying, in order, the coordinate *z* ordinate, "
    "`ExtendedData` fields, the `Placemark` name, and the enclosing folder name; "
    "the strategy that succeeded is reported. Contour interval, extent, working UTM "
    "zone and grid resolution are all derived from the file -- nothing is assumed "
    "about any particular map.\n\n"
    "Send the file and any options together as `multipart/form-data`:\n\n"
    "```\n"
    "curl -X POST http://localhost:8000/api/v1/analyzeContour \\\n"
    "  -F 'file=@contours_1m.kml' -F 'max_sites=3'\n"
    "```"
)


@router.post(
    "/analyzeContour",
    response_model=ContourAnalysisResponse,
    summary="Analyse a contour map and return pond sites with their catchments",
    description=_ANALYZE_DESCRIPTION,
)
async def analyze_contour(
    file: Annotated[UploadFile, File(description="Contour map, KML or KMZ.")],
    opts: Annotated[ContourAnalysisOptions, Depends(_options_form)],
) -> Any:
    return await _run(file, opts)


@router.post(
    "/findCatchment",
    response_model=ContourAnalysisResponse,
    summary="Alias of /analyzeContour",
    description="Identical to `POST /analyzeContour`; for callers using this name.",
)
async def find_catchment(
    file: Annotated[UploadFile, File(description="Contour map, KML or KMZ.")],
    opts: Annotated[ContourAnalysisOptions, Depends(_options_form)],
) -> Any:
    return await _run(file, opts)


@router.post(
    "/terrain/contour-map",
    response_model=ContourMapUploadResponse,
    summary="Parse and interpolate a contour map, without siting",
    description=(
        "The first half of `/analyzeContour`: validates the file, interpolates a "
        "DEM, and returns a `dem_id`. Useful for inspecting what was read before "
        "committing to a full analysis."
    ),
)
async def upload_contour_map(
    file: Annotated[UploadFile, File(description="Contour map, KML or KMZ.")],
    cell_size_m: Annotated[
        float | None,
        Query(description="Grid resolution in metres. Omit to derive it from the file."),
    ] = None,
) -> Any:
    data, name = await _read_upload(file)

    def _parse() -> Any:
        parsed_local = parse_contour_file(data, name)
        dem_local, report_local = contours_to_dem(parsed_local, cell_size_m=cell_size_m)
        return parsed_local, dem_local, report_local

    try:
        parsed, dem, report = await run_in_threadpool(_parse)
    except ContourParseError as exc:
        log.warning("contour_parse_failed", filename=name, detail=str(exc))
        raise _as_unanswerable(exc, name) from exc

    dem_id = _remember(parsed, dem, report)
    return {
        "dem_id": dem_id,
        "contour_map": parsed.summary(),
        "interpolated_terrain": report.as_dict(),
        "area_of_interest": bbox_geojson(*parsed.bounds.as_tuple()),
        "warnings": parsed.warnings,
    }


@router.get(
    "/terrain/contour-map/{dem_id}/contours",
    summary="Echo the parsed contours as GeoJSON",
    description=(
        "Returns the contour lines exactly as the parser read them, so a caller "
        "can confirm the elevations were resolved from the right place before "
        "trusting the analysis."
    ),
)
async def get_contours(
    dem_id: str,
    simplify_deg: Annotated[
        float, Query(ge=0.0, le=0.01, description="Douglas-Peucker tolerance, degrees.")
    ] = 0.0,
    limit: Annotated[
        int | None, Query(ge=1, description="Return only the lowest N contour lines.")
    ] = None,
) -> Any:
    entry = _PARSED_CACHE.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            f"no parsed contour map with id {dem_id!r}. Upload one via "
            "POST /api/v1/terrain/contour-map first; parsed maps are held in memory "
            "and do not survive a restart.",
            dem_id=dem_id,
        )
    parsed = entry["parsed"]
    return {
        "dem_id": dem_id,
        "contour_interval_m": parsed.interval_m,
        "levels": len(parsed.levels),
        "elevation_range_m": list(parsed.elevation_range_m),
        "geojson": contours_to_geojson(parsed.lines, simplify_deg=simplify_deg, max_features=limit),
    }
