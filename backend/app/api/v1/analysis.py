"""Asynchronous analysis jobs (M6-1, M6-3, M6-4, M6-5).

    POST   /api/v1/analysis            202 + job_id
    GET    /api/v1/analysis/{id}/status
    GET    /api/v1/analysis/{id}/result
    DELETE /api/v1/analysis/{id}

The synchronous `POST /analyzeContour` stays exactly as it is. It is the right
shape for a 24-second call from a script, and replacing it with a job id would
make the simple case worse. This is the addition for a browser, which needs to
paint something during those 24 seconds.

Thin, as HLD 2.1 requires: the state machine is `services.jobs`, the lifecycle
is `services.job_runner`, the storage is `services.job_store`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, Response, UploadFile, status

from app.api.v1.contour import (
    _options_form,
    _read_upload,
    _safe_filename,
    area_options,
    area_problem,
)
from app.api.v1.slots import refuse_when_queue_full
from app.config import get_settings
from app.core.errors import NotFoundProblem, UnanswerableProblem
from app.core.logging import get_logger
from app.schemas.contour import AnalyzeAreaRequest
from app.services import area as area_service
from app.services import idempotency
from app.services.contour_analysis import ContourAnalysisOptions
from app.services.job_store import get_store
from app.services.jobs import TERMINAL_STATES

log = get_logger("api.analysis")

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _options_dict(opts: ContourAnalysisOptions) -> dict[str, Any]:
    return {k: v for k, v in vars(opts).items() if not k.startswith("_") and not callable(v)}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an analysis as a background job (M6-1)",
    description=(
        "Accepts a contour map and returns immediately with a `job_id` and the "
        "URL to poll, per HLD 5.1's rule that long operations answer `202`.\n\n"
        "A cold analysis of the sample sheet takes about 24 seconds, 20 of them "
        "in the provider fetch. A browser has to show something during that, and "
        "`GET .../status` reports the step by name with a percentage weighted by "
        "each step's *measured* share of the runtime -- so the bar tracks elapsed "
        "time rather than step count.\n\n"
        "The work runs on a Celery worker when one is reachable, and in this "
        "process otherwise. That fallback is deliberate: this deployment is "
        "local-only, and accepting a job into a queue nothing is draining would "
        "leave the client polling `queued` for ever. `executor` in the response "
        "says which path was taken.\n\n"
        "The synchronous `POST /analyzeContour` is unchanged and remains the "
        "better call from a script."
    ),
)
async def start_analysis(
    background: BackgroundTasks,
    response: Response,
    opts: Annotated[ContourAnalysisOptions, Depends(_options_form)],
    contour_map: Annotated[
        UploadFile | None, File(description="Contour map, KML or KMZ. Preferred field name.")
    ] = None,
    file: Annotated[
        UploadFile | None, File(description="Accepted alias for `contour_map`.")
    ] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    from app.api.v1.contour import _one_upload

    repeated = idempotency.recall(idempotency_key)
    if repeated is not None:
        # A retried start whose first attempt did arrive: the same job, not a second.
        response.headers["Location"] = repeated["status_url"]
        return repeated

    data, filename = await _read_upload(_one_upload(contour_map, file))
    refuse_when_queue_full()
    job_id = uuid.uuid4().hex

    from app.workers.tasks import worker_available

    options = _options_dict(opts)
    dispatched_to_worker = False
    if worker_available():
        try:
            from app.workers.tasks import analyze_task

            analyze_task.delay(job_id, data, filename, options)
            dispatched_to_worker = True
        except Exception as exc:
            # A broker that pings but refuses the publish: fall through to the
            # in-process path rather than losing the request.
            log.warning("celery dispatch failed, running in process", error=str(exc))

    if not dispatched_to_worker:
        import time as _time

        from app.services.job_runner import run_analysis_job

        # Seed a queued record synchronously, so the very first poll -- which a
        # browser issues immediately -- finds the job rather than a 404.
        from app.services.job_store import JobRecord
        from app.services.jobs import JobProgress

        now = _time.time()
        get_store().put(
            JobRecord(
                job_id=job_id,
                progress=JobProgress().as_dict(),
                params=options,
                created_at=now,
                updated_at=now,
            )
        )
        background.add_task(run_analysis_job, job_id, data, filename, options)

    status_url = f"/api/v1/analysis/{job_id}/status"
    response.headers["Location"] = status_url
    log.info(
        "analysis job accepted",
        job_id=job_id,
        filename=_safe_filename(filename),
        executor="celery" if dispatched_to_worker else "in_process",
    )
    body = {
        "job_id": job_id,
        "state": "queued",
        "status_url": status_url,
        "result_url": f"/api/v1/analysis/{job_id}/result",
        "executor": "celery" if dispatched_to_worker else "in_process",
        "estimated_duration_s": 25,
        "poll_after_s": 1,
    }
    idempotency.remember(idempotency_key, body)
    return body


@router.post(
    "/area",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start analysing a rectangle drawn on the map",
    description=(
        "The job form of `POST /analyzeArea`, for the browser's progress bar. The "
        "rectangle is validated before the job is accepted, so a box that is "
        "inverted, too small or over the cap is answered at once (400 / 413) "
        "rather than as a job that fails later.\n\n"
        "Poll `GET /analysis/{job_id}/status`, then fetch `.../result`. The steps "
        "are `terrain` (fetching Copernicus GLO-30), then the same pipeline as a "
        "contour map. Runs in this process."
    ),
)
async def start_area_analysis(
    background: BackgroundTasks,
    response: Response,
    request: AnalyzeAreaRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    repeated = idempotency.recall(idempotency_key)
    if repeated is not None:
        response.headers["Location"] = repeated["status_url"]
        return repeated
    try:
        area_service.validate_bbox(request.bbox, max_km2=float(get_settings().MAX_AOI_KM2))
    except area_service.AreaError as exc:
        raise area_problem(exc) from exc
    refuse_when_queue_full()

    import time as _time

    from app.services.job_runner import run_area_job
    from app.services.job_store import JobRecord
    from app.services.jobs import AREA_STEPS, JobProgress

    job_id = uuid.uuid4().hex
    options = area_options(request).as_dict()
    # Seeded synchronously, as for a contour job, so the browser's first poll
    # finds the job rather than a 404 -- and with the area's own steps, so that
    # first poll already shows `terrain` rather than `parse`.
    now = _time.time()
    get_store().put(
        JobRecord(
            job_id=job_id,
            progress=JobProgress(steps=AREA_STEPS).as_dict(),
            params={**options, "bbox": list(request.bbox)},
            created_at=now,
            updated_at=now,
        )
    )
    # In process, not Celery: the workers only know the contour task, and the
    # deployment spreads load across systems at the gateway rather than a queue.
    background.add_task(run_area_job, job_id, list(request.bbox), options)

    status_url = f"/api/v1/analysis/{job_id}/status"
    response.headers["Location"] = status_url
    log.info("area analysis job accepted", job_id=job_id, bbox=request.bbox)
    body = {
        "job_id": job_id,
        "state": "queued",
        "status_url": status_url,
        "result_url": f"/api/v1/analysis/{job_id}/result",
        "executor": "in_process",
        "estimated_duration_s": 20,
        "poll_after_s": 1,
    }
    idempotency.remember(idempotency_key, body)
    return body


def _record_or_404(job_id: str) -> Any:
    record = get_store().get(job_id)
    if record is None:
        raise NotFoundProblem(
            detail=(
                f"no analysis job with id {job_id!r}. Jobs are kept for 24 hours; "
                "an older one has expired, and a restart without Redis loses "
                "in-process jobs."
            ),
            job_id=job_id,
        )
    return record


@router.get(
    "/{job_id}/status",
    summary="Job state, percentage and per-step outcomes (M6-3)",
    description=(
        "Reports the state machine of HLD §3.7: `queued`, `running`, `retrying`, "
        "then one of `done`, `partial`, `failed`, `cancelled`.\n\n"
        "`progress_pct` is weighted by each step's measured share of a cold run, "
        "not by step count. Enrichment alone is 82 % of the runtime, so an "
        "evenly-weighted bar would reach 57 % and then not move for twenty "
        "seconds -- which reads as a hang.\n\n"
        "`is_terminal` says when to stop polling. `steps[]` carries every stage "
        "with its own outcome, so a client can show which one is running and "
        "which one was lost."
    ),
)
async def job_status(job_id: str) -> Any:
    record = _record_or_404(job_id)
    return {
        "job_id": job_id,
        **record.progress,
        "elapsed_s": None if record.elapsed_s is None else round(record.elapsed_s, 2),
        "result_url": (
            f"/api/v1/analysis/{job_id}/result"
            if record.progress.get("state") in ("done", "partial")
            else None
        ),
    }


@router.get(
    "/{job_id}/result",
    summary="The finished analysis (M6-5)",
    description=(
        "Available once the job reaches `done` or **`partial`**.\n\n"
        "`partial` is served rather than withheld, and that is the point of the "
        "state. If SoilGrids is unreachable there is still terrain, rainfall and "
        "a catchment; the analysis falls back to an assumed soil group and says "
        "so in `warnings`. Refusing the result because one optional layer was "
        "missing would throw away a usable answer (HLD NFR-5)."
    ),
)
async def job_result(job_id: str) -> Any:
    record = _record_or_404(job_id)
    state = record.progress.get("state")
    if state not in ("done", "partial"):
        raise UnanswerableProblem(
            detail=(
                f"job {job_id!r} is {state!r}, so there is no result to serve. "
                + (
                    "Poll the status URL until `is_terminal` is true."
                    if state not in TERMINAL_STATES
                    else "This job will not produce one."
                )
            ),
            job_id=job_id,
            state=state,
            status_url=f"/api/v1/analysis/{job_id}/status",
        )
    return {
        "job_id": job_id,
        "state": state,
        "warnings": record.progress.get("warnings", []),
        "elapsed_s": None if record.elapsed_s is None else round(record.elapsed_s, 2),
        "result": record.result,
    }


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Abandon a job and drop its record",
    description=(
        "Removes the job. In-process work already running is not interrupted -- "
        "there is no safe way to kill a thread mid-GDAL-read -- but the record "
        "goes, so nothing is served from it and the client stops polling."
    ),
)
async def cancel_job(job_id: str) -> Response:
    _record_or_404(job_id)
    get_store().delete(job_id)
    log.info("analysis job cancelled", job_id=job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
