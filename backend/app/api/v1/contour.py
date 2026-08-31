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

import math
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.core.errors import NotFoundProblem, UnanswerableProblem, ValidationProblem
from app.core.logging import get_logger
from app.providers.elevation.contour_kml import (
    MAX_UPLOAD_BYTES,
    ContourParseError,
    parse_contour_file,
)
from app.schemas.contour import ContourAnalysisResponse, ContourMapUploadResponse
from app.services import conditioning as conditioning_service
from app.services import contours, dem_cache, derivatives, land, raster, siting, streams
from app.services import hydrology as hyd
from app.services.contour_analysis import (
    DEFAULT_SNAP_RADIUS_M,
    DEFAULT_STREAM_THRESHOLD_HA,
    ContourAnalysisOptions,
    analyze_contour_map,
)
from app.services.geometry import (
    bbox_geojson,
    contour_lines_to_geojson,
    contours_to_geojson,
    mask_to_geojson,
    point_geojson,
    reaches_to_geojson,
)
from app.services.interpolate import MAX_CELL_M, MIN_CELL_M, contours_to_dem

router = APIRouter(tags=["contour"])
log = get_logger("contour")

#: Parsed uploads live in `services.dem_cache` now, because the async job path
#: has to register them too -- a service cannot import this module without
#: inverting the layering, and while the registry lived here an analysis run as a
#: job came back with no `dem_id` at all.

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


async def _read_upload(
    file: UploadFile,
    *,
    accepted: tuple[str, ...] = ACCEPTED_SUFFIXES,
    describe_as: str = "a contour map",
) -> tuple[bytes, str]:
    """Read and pre-validate an uploaded file.

    Reads in chunks and aborts as soon as the cap is exceeded. `await file.read()`
    would buffer the entire body *before* the size check, so a 500 MB POST would
    be fully materialised in memory only to be rejected -- the check would exist
    while the denial-of-service it was meant to prevent still worked.

    The accepted extensions are a parameter because the cadastral upload takes a
    different set. They defaulted to the contour-map list, which silently
    rejected every `.geojson` and `.zip` sent to that endpoint with a message
    about contour maps.
    """
    name = _safe_filename(file.filename)
    if not name.lower().endswith(accepted):
        raise ValidationProblem(
            f"{name!r} does not look like {describe_as}. Expected a "
            f"{', '.join(accepted)} file.",
            accepted_extensions=list(accepted),
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
    return dem_cache.remember(parsed, dem, report)


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
    # Hand back a handle on the interpolated DEM so the caller can ask for
    # terrain tiles (POST /terrain/derivatives) without uploading the file a
    # second time. The analysis already holds the grid; re-parsing 6 MB of KML to
    # get back to it would be pure waste.
    body["dem_id"] = _remember(result.parsed, result.dem, result.interpolation)
    # Contours are attached by `ContourAnalysis.as_dict()` now, so both this
    # endpoint and the async job path get them from one place.
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


@router.post(
    "/terrain/derivatives",
    summary="Slope and hillshade as map tiles (M2-3, M2-4)",
    description=(
        "Writes the DEM behind a `dem_id` as Cloud-Optimized GeoTIFFs -- "
        "elevation, Horn slope, and shaded relief -- and returns XYZ tile "
        "templates the browser can use directly.\n\n"
        "HLD ADR-3 is why this exists: a 5 m DEM over 8.5 km2 is 342,550 cells, "
        "and shipping that as JSON freezes the tab. As a COG the browser fetches "
        "only the 256x256 tiles it can see.\n\n"
        "The rasters are content-addressed on the elevation grid itself, so "
        "asking twice for the same DEM reuses them -- `reused` says which."
    ),
)
async def terrain_derivatives(
    dem_id: Annotated[str, Form(description="From POST /terrain/contour-map.")],
    products: Annotated[
        str,
        Form(description="Comma-separated: dem, slope, hillshade. Default all three."),
    ] = "dem,slope,hillshade",
    hillshade_azimuth_deg: Annotated[
        float, Form(ge=0, le=360, description="Light direction, compass degrees.")
    ] = raster.DEFAULT_AZIMUTH_DEG,
    hillshade_altitude_deg: Annotated[
        float, Form(gt=0, le=90, description="Light elevation above the horizon.")
    ] = raster.DEFAULT_ALTITUDE_DEG,
    hillshade_z_factor: Annotated[
        float,
        Form(
            gt=0,
            le=20,
            description=(
                "Vertical exaggeration. Indian plateau relief of 30 m over 3 km is "
                "nearly invisible at 1.0; 3 to 5 reads well."
            ),
        ),
    ] = raster.DEFAULT_Z_FACTOR,
) -> Any:
    entry = dem_cache.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no parsed contour map with id {dem_id!r}. Upload one via "
                "POST /api/v1/terrain/contour-map first; parsed maps are held in "
                "memory and do not survive a restart."
            ),
            dem_id=dem_id,
        )

    requested = tuple(p.strip().lower() for p in products.split(",") if p.strip())
    unknown = [p for p in requested if p not in derivatives.ALL_PRODUCTS]
    if unknown:
        raise ValidationProblem(
            detail=(
                f"unknown product(s) {unknown}. Choose from " f"{list(derivatives.ALL_PRODUCTS)}."
            ),
            errors=[{"field": "products", "message": f"unknown: {unknown}"}],
        )
    if not requested:
        raise ValidationProblem(
            detail="no products requested; name at least one of dem, slope, hillshade.",
            errors=[{"field": "products", "message": "empty"}],
        )

    dem = entry["dem"]
    settings = get_settings()
    store = Path(settings.COG_STORE_PATH) / "cog"

    def _build() -> Any:
        # No database session: the rasters are useful without one, and the
        # contour endpoints deliberately work with no database at all.
        return derivatives.build(
            None,
            elevation=dem.elevation,
            transform=dem.transform,
            epsg=dem.epsg,
            cell_size_m=dem.cell_size_m,
            store=store,
            products=tuple(requested),  # type: ignore[arg-type]
            hillshade_azimuth_deg=hillshade_azimuth_deg,
            hillshade_altitude_deg=hillshade_altitude_deg,
            hillshade_z_factor=hillshade_z_factor,
        )

    try:
        layers = await run_in_threadpool(_build)
    except raster.RasterWriteError as exc:
        log.error("cog_write_failed", dem_id=dem_id, detail=str(exc))
        raise UnanswerableProblem(
            detail=f"the terrain could not be written as a tiled raster: {exc}"
        ) from exc

    first = layers[0].asset
    return {
        "dem_id": dem_id,
        "working_crs": f"EPSG:{first.epsg}",
        "resolution_m": first.resolution_m,
        "grid_size": [first.width, first.height],
        "bounds_4326": [round(v, 6) for v in first.bounds_4326],
        "hillshade": {
            "azimuth_deg": hillshade_azimuth_deg,
            "altitude_deg": hillshade_altitude_deg,
            "z_factor": hillshade_z_factor,
        },
        "layers": [layer.as_dict() for layer in layers],
        "note": (
            "Tile templates are relative to this origin and are served by TiTiler "
            "through /tiles/. They need the tiles service running: "
            "`docker compose up -d titiler`."
        ),
    }


@router.post(
    "/hydrology/catchment",
    summary="Delineate the catchment above any point (M3-9b, FR-4)",
    description=(
        "Given a `dem_id` and a coordinate, returns the land that drains to "
        "that point: the polygon, the morphometrics, and how far the point had "
        "to move to reach a channel.\n\n"
        "This is the interactive form of what `/analyzeContour` does at its "
        "ranked sites. Clicking three different points gives three visibly "
        "different catchments, which is how a reader checks the routing is "
        "doing something rather than taking it on trust.\n\n"
        "**Snapping matters more than it looks.** A click a few metres off the "
        "channel lands on a hillside cell whose catchment is the hillside -- a "
        "few hectares rather than a few hundred. The point is moved to the "
        "highest-accumulation cell within `snap_radius_m`, and the response says "
        "how far it moved so the caller can tell a nudge from a relocation."
    ),
)
async def hydrology_catchment(
    dem_id: Annotated[str, Form(description="From /analyzeContour or /terrain/contour-map.")],
    lon: Annotated[float, Form(ge=-180, le=180, description="Pour point longitude.")],
    lat: Annotated[float, Form(ge=-90, le=90, description="Pour point latitude.")],
    snap_radius_m: Annotated[
        float,
        Form(
            ge=0,
            le=2000,
            description=(
                "How far the point may be moved onto the drainage line. Zero "
                "delineates exactly where you clicked, which is usually not what "
                "you meant."
            ),
        ),
    ] = DEFAULT_SNAP_RADIUS_M,
    conditioning: Annotated[
        str,
        Form(
            description=(
                "How to make the surface routable: `fill` raises every depression "
                "to its spill level; `breach` carves outlets through thin barriers "
                "first, preserving the hollows a pond would occupy; `auto` breaches "
                "when more than 15 % of the surface has no usable gradient. The "
                "response reports which was used and how much of the terrain was "
                "altered."
            )
        ),
    ] = "auto",
) -> Any:
    entry = dem_cache.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no parsed contour map with id {dem_id!r}. Upload one via "
                "POST /api/v1/terrain/contour-map first; parsed maps are held in "
                "memory and do not survive a restart."
            ),
            dem_id=dem_id,
        )

    dem = entry["dem"]

    def _delineate() -> Any:
        from pyproj import Transformer

        to_grid = Transformer.from_crs(4326, dem.epsg, always_xy=True)
        x, y = to_grid.transform(lon, lat)
        # A coordinate far outside the working UTM zone projects to infinity, and
        # `rowcol` raises OverflowError rather than IndexError trying to floor it.
        # Checking for a finite result covers both, and every other way a
        # projection can fail to produce a usable number.
        if not (math.isfinite(x) and math.isfinite(y)):
            raise _PourPointOutsideError(lon, lat)
        try:
            row, col = dem.rowcol(float(x), float(y))
        except (IndexError, OverflowError, ValueError) as exc:
            raise _PourPointOutsideError(lon, lat) from exc

        conditioned, conditioning_report = conditioning_service.condition(dem, method=conditioning)
        flow = hyd.build_flow(dem, conditioned)
        catchment = hyd.delineate_catchment(
            dem,
            flow,
            row,
            col,
            snap_radius_cells=int(round(snap_radius_m / dem.cell_size_m)),
        )
        # Slope on the *original* surface: a filled hollow reads as 0 % exactly
        # where a pond would go, so the conditioned surface is the wrong one for
        # any question about the ground.
        slope = hyd.slope_percent(dem.elevation, dem.cell_size_m)
        metrics = hyd.catchment_metrics(dem, conditioned, flow, catchment, slope)
        geometry = mask_to_geojson(catchment.mask, dem)
        return catchment, metrics, geometry, conditioning_report

    try:
        catchment, metrics, geometry, conditioning_report = await run_in_threadpool(_delineate)
    except _PourPointOutsideError as exc:
        raise UnanswerableProblem(detail=str(exc)) from exc
    except ValueError as exc:
        raise ValidationProblem(
            detail=str(exc),
            errors=[{"field": "conditioning", "message": str(exc)}],
        ) from exc

    if geometry is None:
        raise UnanswerableProblem(
            detail=(
                "the catchment came out empty, which means the point sits on a "
                "cell with no valid elevation. Pick a point inside the surveyed "
                "area."
            )
        )

    outlet_lon, outlet_lat = _outlet_lonlat(dem, catchment)
    moved_m = round(catchment.snap_distance_m, 1)
    return {
        "dem_id": dem_id,
        "requested": point_geojson(lon, lat),
        "outlet": point_geojson(outlet_lon, outlet_lat),
        "snapped": {
            "was_snapped": catchment.snapped_from is not None,
            "moved_m": moved_m,
            # The effective radius, which is the requested one rounded to whole
            # cells -- the search works in cells, so 150 m at 5 m is exactly 30.
            "search_radius_m": round(
                int(round(snap_radius_m / dem.cell_size_m)) * dem.cell_size_m, 1
            ),
            # A move close to the radius means the search ran out of room rather
            # than finding the channel, so the answer deserves less confidence.
            "hit_the_search_limit": bool(
                snap_radius_m > 0 and moved_m >= snap_radius_m - dem.cell_size_m
            ),
        },
        "metrics": metrics,
        # How the surface was made routable, and how much of the terrain that
        # altered. A catchment delineated over a heavily filled surface deserves
        # less confidence than one over terrain that drained on its own, and this
        # is the only place that shows.
        "conditioning": conditioning_report,
        "geometry": geometry,
    }


@router.post(
    "/hydrology/streams",
    summary="Drainage network with Strahler order (M3-3)",
    description=(
        "Thresholds the flow accumulation of the DEM behind a `dem_id`, "
        "vectorises the resulting channels, and orders them by Strahler.\n\n"
        "A catchment outline says how much land drains to a point; this says "
        "*where the water goes on the way*. For siting that is often the more "
        "useful picture -- the same location on a first-order headwater collects "
        "from a few hectares and on a fourth-order channel from hundreds, and "
        "needs a spillway sized accordingly.\n\n"
        "A reach is a Strahler stream: it runs from where it attains its order "
        "to where it loses it, not from junction to junction. Cutting at every "
        "junction breaks Horton's law of stream numbers.\n\n"
        "Pass a pour point to restrict the network to that catchment, which is "
        "also what makes the drainage density meaningful -- density over an "
        "arbitrary rectangle is a property of the rectangle."
    ),
)
async def hydrology_streams(
    dem_id: Annotated[str, Form(description="From /analyzeContour or /terrain/contour-map.")],
    threshold_ha: Annotated[
        float,
        Form(
            gt=0,
            le=10_000,
            description=(
                "Contributing area at which a channel begins. In hectares rather "
                "than cells, so the same value means the same thing at 5 m and at "
                "30 m. 1 ha is deliberately small for village terrain: a nala "
                "draining a few hectares is exactly what a check dam sits on."
            ),
        ),
    ] = streams.DEFAULT_THRESHOLD_HA,
    lon: Annotated[
        float | None,
        Form(ge=-180, le=180, description="Pour point longitude, to restrict to one catchment."),
    ] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90, description="Pour point latitude.")] = None,
    snap_radius_m: Annotated[
        float,
        Form(
            ge=0,
            le=2000,
            description="How far the pour point may be nudged onto a channel.",
        ),
    ] = DEFAULT_SNAP_RADIUS_M,
) -> Any:
    entry = dem_cache.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no parsed contour map with id {dem_id!r}. Upload one via "
                "POST /api/v1/terrain/contour-map first; parsed maps are held in "
                "memory and do not survive a restart."
            ),
            dem_id=dem_id,
        )
    if (lon is None) != (lat is None):
        raise ValidationProblem(
            detail="give both lon and lat to restrict the network to a catchment, or neither.",
            errors=[{"field": "lon" if lon is None else "lat", "message": "required together"}],
        )

    dem = entry["dem"]

    def _extract() -> Any:
        conditioned = hyd.fill_depressions(dem)
        flow = hyd.build_flow(dem, conditioned)
        catchment = None
        if lon is not None and lat is not None:
            from pyproj import Transformer

            to_grid = Transformer.from_crs(4326, dem.epsg, always_xy=True)
            x, y = to_grid.transform(lon, lat)
            if not (math.isfinite(x) and math.isfinite(y)):
                raise _PourPointOutsideError(lon, lat)
            try:
                row, col = dem.rowcol(float(x), float(y))
            except (IndexError, OverflowError, ValueError) as exc:
                raise _PourPointOutsideError(lon, lat) from exc
            catchment = hyd.delineate_catchment(
                dem,
                flow,
                row,
                col,
                snap_radius_cells=int(round(snap_radius_m / dem.cell_size_m)),
            )
        network = streams.extract(
            flow,
            transform=dem.transform,
            cell_size_m=dem.cell_size_m,
            threshold_ha=threshold_ha,
            within=catchment,
        )
        return network, catchment

    try:
        network, catchment = await run_in_threadpool(_extract)
    except _PourPointOutsideError as exc:
        raise UnanswerableProblem(detail=str(exc)) from exc
    except streams.StreamExtractionError as exc:
        raise UnanswerableProblem(detail=str(exc)) from exc

    body: dict[str, Any] = {
        "dem_id": dem_id,
        "network": network.report(),
        "streams": reaches_to_geojson(network.reaches, dem.epsg),
    }
    if catchment is not None:
        body["catchment"] = {
            "area_ha": round(catchment.area_m2 / 10_000.0, 3),
            "area_km2": round(catchment.area_m2 / 1e6, 5),
            "outlet": point_geojson(*_outlet_lonlat(dem, catchment)),
            "snapped": {
                "was_snapped": catchment.snapped_from is not None,
                "distance_m": round(catchment.snap_distance_m, 1),
            },
        }
    return body


@router.post(
    "/land/available",
    summary="Land parcels a pond could actually be dug on (M5-6, FR-3)",
    description=(
        "Runs the available-land pipeline over the DEM behind a `dem_id` and "
        "returns the surviving parcels as a GeoJSON FeatureCollection.\n\n"
        "Terrain says where water collects; this says where you are allowed to "
        "dig. The two are independent, and a tool that only models the first "
        "will happily recommend the middle of a village.\n\n"
        "Exclusions are buffered OSM features (buildings 50 m, roads 20 m, "
        "existing water 100 m -- so a new tank does not duplicate an existing "
        "one), land cover that rules the ground out outright, and slope above "
        "`max_slope_pct`. The default 5 % is stricter than the 8 % siting uses, "
        "because steep ground is ruled out by excavation cost long before it is "
        "ruled out by physics.\n\n"
        "OSM only ever *removes* land here. A building missing from OSM is not "
        "evidence of open ground, so a village with thin OSM coverage gets an "
        "optimistic answer rather than a wrong one -- `criteria."
        "osm_exclusions_applied` says whether any features were found at all.\n\n"
        "Both providers degrade rather than fail: if WorldCover or Overpass is "
        "unavailable the parcels are still returned from what did answer, and "
        "`unavailable` names what was lost."
    ),
)
async def land_available(
    dem_id: Annotated[str, Form(description="From /analyzeContour or /terrain/contour-map.")],
    max_slope_pct: Annotated[
        float,
        Form(
            gt=0,
            le=45,
            description=(
                "Slope above which excavation cost rules the ground out. The "
                "HLD fixes 5 % for this step."
            ),
        ),
    ] = land.DEFAULT_MAX_SLOPE_PCT,
    min_area_m2: Annotated[
        float,
        Form(
            gt=0,
            le=1_000_000,
            description="Below this a patch is a puddle, not a pond site.",
        ),
    ] = land.DEFAULT_MIN_AREA_M2,
    allow_cropland: Annotated[
        bool,
        Form(
            description=(
                "Include cropland. Off by default because it is usually "
                "privately held, which is a tenure question this cannot answer."
            ),
        ),
    ] = False,
    use_osm: Annotated[
        bool,
        Form(description="Fetch OSM buildings/roads/water to subtract. Adds a few seconds."),
    ] = True,
) -> Any:
    entry = dem_cache.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no parsed contour map with id {dem_id!r}. Upload one via "
                "POST /api/v1/terrain/contour-map first; parsed maps are held in "
                "memory and do not survive a restart."
            ),
            dem_id=dem_id,
        )

    dem = entry["dem"]
    bounds = entry["parsed"].bounds
    unavailable: list[dict[str, str]] = []

    def _compute() -> Any:
        from app.providers.base import ProviderUnavailableError
        from app.providers.landcover.worldcover import fetch_landcover
        from app.providers.vector import osm_cache
        from app.providers.vector.overpass import fetch_osm_context

        cover = None
        try:
            cover = fetch_landcover(bounds.as_tuple(), dem.shape, dem.transform, dem.epsg)
        except ProviderUnavailableError as exc:
            unavailable.append(
                {"layer": "land_cover", "provider": "ESA WorldCover", "reason": exc.detail}
            )

        osm = None
        osm_cached = False
        if use_osm:
            try:
                osm, osm_cached = osm_cache.fetch_cached(
                    bounds.as_tuple(),
                    Path(get_settings().COG_STORE_PATH),
                    fetch=fetch_osm_context,
                )
            except (ProviderUnavailableError, ValueError) as exc:
                unavailable.append(
                    {"layer": "osm_features", "provider": "Overpass", "reason": str(exc)}
                )

        # Buildability belongs on the ORIGINAL ground, not the conditioned
        # surface: filling a depression reports it as 0 % slope, and those are
        # exactly the cells being chosen between.
        slope = hyd.slope_percent(dem.elevation, dem.cell_size_m)
        return (
            land.available_land(
                dem,
                slope_pct=slope,
                land_cover=None if cover is None else cover.codes,
                osm=osm,
                max_slope_pct=max_slope_pct,
                min_area_m2=min_area_m2,
                allow_cropland=allow_cropland,
            ),
            cover,
            osm,
            osm_cached,
        )

    result, cover, osm, osm_cached = await run_in_threadpool(_compute)

    body: dict[str, Any] = {
        "dem_id": dem_id,
        "summary": result.as_dict(),
        "parcels": result.feature_collection(),
        "unavailable": unavailable,
        "sources": {
            "land_cover": None if cover is None else cover.as_dict()["source"],
            "osm": None if osm is None else {**osm.as_dict(), "from_cache": osm_cached},
        },
    }
    return body


@router.post(
    "/land/cadastral",
    summary="Upload a cadastral layer and check tenure at each site (M10-3, M10-4, FR-11)",
    description=(
        "Accepts a GeoJSON or a zipped shapefile of land parcels, reprojects it "
        "to WGS 84, and classifies each parcel's tenure. Give a `job_id` as well "
        "and every candidate site from that analysis is checked against the "
        "parcel it falls on.\n\n"
        "**This is the largest gap between 'recommended' and 'buildable'.** No "
        "open dataset carries village-level ownership, so a site that is "
        "physically ideal may be privately held. The model cannot close that gap "
        "on its own; this lets someone who has the layer close it.\n\n"
        "**The datum matters more than it looks.** Indian cadastral sheets are "
        "frequently on Everest 1830 / Kalianpur. PROJ's default choice of "
        "transformation for those is a *ballpark offset that moves nothing*, "
        "which would leave every parcel about 190 m from its true position while "
        "looking entirely correct. The operation is therefore chosen explicitly "
        "and reported with its stated accuracy, and a shapefile arriving without "
        "a `.prj` is refused rather than assumed to be WGS 84.\n\n"
        "The upload is parsed defensively: zip bombs, path traversal, symlinks "
        "and absurd entry counts are all rejected before anything is written."
    ),
    responses={400: {"description": "The layer could not be ingested; the reason says why."}},
)
async def upload_cadastral(
    file: Annotated[UploadFile, File(description="GeoJSON, or a zipped shapefile.")],
    job_id: Annotated[
        str | None,
        Form(description="An analysis job, to report tenure at each candidate site."),
    ] = None,
) -> Any:
    from app.services import cadastral

    data, filename = await _read_upload(
        file,
        accepted=(".geojson", ".json", ".zip"),
        describe_as="a cadastral layer",
    )
    try:
        layer = await run_in_threadpool(cadastral.load, data, filename)
    except cadastral.CadastralError as exc:
        raise ValidationProblem(
            detail=str(exc), errors=[{"field": "file", "message": str(exc)}]
        ) from exc

    body: dict[str, Any] = {
        "summary": layer.as_dict(),
        "parcels": layer.feature_collection(),
    }

    if job_id:
        from app.services.job_store import get_store

        record = get_store().get(job_id)
        if record is None:
            raise NotFoundProblem(detail=f"no analysis job with id {job_id!r}.", job_id=job_id)
        sites = (record.result or {}).get("candidate_sites") or []
        body["sites"] = _tenure_at_sites(sites, layer)

    return body


def _tenure_at_sites(sites: list[Any], layer: Any) -> list[dict[str, Any]]:
    """Which parcel each candidate site falls on, and whether it is allottable.

    A point-in-polygon test, not a nearest-parcel search: a site 50 m outside
    every parcel is *not* on public land, and saying "unknown" is the honest
    answer rather than attaching it to whichever polygon happens to be closest.
    """
    from shapely.geometry import Point, shape

    prepared = [(shape(p.geometry), p) for p in layer.parcels]
    out: list[dict[str, Any]] = []
    for site in sites:
        location = site.get("location") or {}
        if "lon" not in location:
            continue
        point = Point(float(location["lon"]), float(location["lat"]))
        match = next((parcel for geom, parcel in prepared if geom.contains(point)), None)
        out.append(
            {
                "rank": site.get("rank"),
                "suitability_score": site.get("suitability_score"),
                "parcel_id": None if match is None else match.parcel_id,
                "ownership": None if match is None else match.ownership,
                "tenure": (
                    "unknown -- the site falls outside every parcel in the layer"
                    if match is None
                    else ("allottable" if match.is_public else "privately held")
                ),
                "parcel_area_ha": None if match is None else round(match.area_ha, 3),
            }
        )
    return out


class _PourPointOutsideError(Exception):
    """The requested pour point is not inside the surveyed area."""

    def __init__(self, lon: float, lat: float) -> None:
        super().__init__(
            f"({lat}, {lon}) is outside the surveyed area. The contour map covers "
            "only its own extent; pick a point inside it."
        )


def _outlet_lonlat(dem: Any, catchment: Any) -> tuple[float, float]:
    from pyproj import Transformer

    to_wgs84 = Transformer.from_crs(dem.epsg, 4326, always_xy=True)
    lon, lat = to_wgs84.transform(*catchment.outlet_xy)
    return float(lon), float(lat)


@router.post(
    "/terrain/contours",
    summary="Generate contour lines from a DEM at any interval (M2-5, M2-6)",
    description=(
        "Traces contours through the DEM behind a `dem_id` and returns them as "
        "GeoJSON, simplified and with every Nth line marked as an index "
        "contour.\n\n"
        "The mirror image of the upload path: that turns contour lines into a "
        "grid, this turns the grid back into lines -- at whatever interval is "
        "asked for, not only the one the file happened to use. Regenerating the "
        "input interval is also a check on the interpolation, and the golden "
        "test does exactly that.\n\n"
        "Marching squares rather than the `gdal_contour` CLI (HLD Decision 7), "
        "so there is no system GDAL binary to depend on."
    ),
)
async def terrain_contours(
    dem_id: Annotated[str, Form(description="From /analyzeContour or /terrain/contour-map.")],
    interval_m: Annotated[
        float,
        Form(gt=0, le=500, description="Vertical spacing between contours, in metres."),
    ] = 1.0,
    index_every: Annotated[
        int,
        Form(
            ge=1,
            le=50,
            description=(
                "Mark every Nth line as an index contour. Five is the convention "
                "on Indian topo sheets; the eye counts thick lines and "
                "interpolates between them."
            ),
        ),
    ] = contours.DEFAULT_INDEX_EVERY,
    simplify: Annotated[
        bool,
        Form(
            description=(
                "Douglas-Peucker at a third of a cell. Marching squares emits a "
                "vertex per cell crossing -- tens of thousands per level -- which "
                "no browser will draw."
            )
        ),
    ] = True,
) -> Any:
    entry = dem_cache.get(dem_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no parsed contour map with id {dem_id!r}. Upload one via "
                "POST /api/v1/terrain/contour-map first; parsed maps are held in "
                "memory and do not survive a restart."
            ),
            dem_id=dem_id,
        )

    dem = entry["dem"]

    def _generate() -> Any:
        return contours.generate(
            dem.elevation,
            transform=dem.transform,
            epsg=dem.epsg,
            cell_size_m=dem.cell_size_m,
            interval_m=interval_m,
            index_every=index_every,
            simplify=simplify,
        )

    try:
        generated = await run_in_threadpool(_generate)
    except contours.ContourGenerationError as exc:
        # 422, not 400: the request is well-formed and the answer is that this
        # surface cannot carry these contours.
        raise UnanswerableProblem(detail=str(exc)) from exc

    return {
        "dem_id": dem_id,
        "generation": generated.report(),
        "contours": contour_lines_to_geojson(generated.lines, generated.epsg),
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
    entry = dem_cache.get(dem_id)
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
