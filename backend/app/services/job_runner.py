"""Runs one analysis as a job, reporting progress and settling its state (M6-2..M6-5).

Separate from `workers/tasks.py` on purpose: this is the whole job lifecycle as
plain synchronous code, so it can be tested end to end without a broker, and so
the FastAPI process can run a job itself when no worker is available. The Celery
task is a thin wrapper around `run_analysis_job`.

The one piece of real judgement here is when an analysis is `PARTIAL`. The
pipeline never raises for a missing provider -- `fetch_enrichment` degrades
internally and reports which layers it lost -- so PARTIAL cannot be detected by
catching an exception. It is detected from the *tier*: anything below `full`
means a layer the model wanted was unavailable, which is precisely the
"core steps succeeded, an optional enrichment did not" case in HLD §3.7.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from app.core.logging import get_logger
from app.core.memory import release_memory
from app.services import area as area_service
from app.services import capacity, dem_cache
from app.services.contour_analysis import (
    ContourAnalysis,
    ContourAnalysisOptions,
    StageReporter,
    analyze_area,
    analyze_contour_map,
)
from app.services.job_store import JobRecord, JobStore, get_store
from app.services.jobs import AREA_STEPS, STEPS, JobProgress, Step

log = get_logger("services.job_runner")


class _Reporter:
    """Bridges `JobProgress` to the store, persisting on every step boundary.

    Writing on each boundary rather than on a timer is what makes the progress
    bar truthful: the client sees "fetching soil, land cover and rainfall" for
    the twenty seconds that step actually takes, instead of a percentage
    interpolated from a guess.
    """

    def __init__(self, record: JobRecord, progress: JobProgress, store: JobStore) -> None:
        self.record = record
        self.progress = progress
        self.store = store

    def flush(self) -> None:
        self.record.progress = self.progress.as_dict()
        self.record.updated_at = time.time()
        self.store.put(self.record)

    def start_step(self, name: str) -> None:
        self.progress.start_step(name)
        self.flush()

    def finish_step(self, name: str) -> None:
        self.progress.finish_step(name)
        self.flush()

    def fail_step(self, name: str, reason: str) -> None:
        self.progress.fail_step(name, reason)
        self.flush()


def _degrade_enrichment(progress: JobProgress, analysis: Any) -> None:
    """Mark enrichment failed when the tier says a layer was lost.

    `fetch_enrichment` swallows provider outages by design, so the exception path
    never fires for them. Reading the tier is how the job learns that soil or
    land cover went missing, and it is what turns DONE into PARTIAL.

    Note the field is `analysis.enrichment`, not `analysis.environment`:
    `environment` is only the key `as_dict()` publishes it under. Reading the
    published name here raised AttributeError *outside* the runner's try block,
    which left finished jobs pinned at 99 % `running` for ever.
    """
    enrichment = analysis.enrichment
    if enrichment.skipped:
        # The caller passed enrich=false. Not a degradation -- they asked for
        # terrain-only, and answering DONE is the honest report.
        progress.skip_step("enrichment", "enrichment was not requested")
        return
    if enrichment.tier == "full":
        return
    failures = enrichment.failures or []
    named = ", ".join(f"{f.get('layer', '?')} ({f.get('provider', '?')})" for f in failures)
    progress.fail_step("enrichment", named or f"tier degraded to {enrichment.tier}")


def run_analysis_job(
    job_id: str,
    data: bytes,
    filename: str | None,
    options: dict[str, Any] | None = None,
    *,
    store: JobStore | None = None,
) -> JobRecord:
    """Execute one contour-map analysis job to a terminal state and return its record.

    Never raises for an analysis failure: a failed job is a `failed` record with
    an RFC 7807-shaped `error`, which is what the status endpoint serves. A raise
    here would lose the job instead of reporting it.
    """
    return _run_job(
        job_id,
        options,
        store=store,
        steps=STEPS,
        params=dict(options or {}),
        analyse=lambda opts, reporter: analyze_contour_map(data, filename, opts, reporter=reporter),
    )


def run_area_job(
    job_id: str,
    bbox: Sequence[float],
    options: dict[str, Any] | None = None,
    *,
    store: JobStore | None = None,
    fetch: Any = None,
) -> JobRecord:
    """The same, for a rectangle drawn on the map.

    Its progress bar has `terrain` where a contour job has `parse` and
    `interpolate`; everything after that is the same pipeline and the same
    settling rules. `fetch` replaces the Copernicus read, for tests.
    """
    return _run_job(
        job_id,
        options,
        store=store,
        steps=AREA_STEPS,
        params={**(options or {}), "bbox": list(bbox)},
        analyse=lambda opts, reporter: analyze_area(bbox, opts, reporter=reporter, fetch=fetch),
    )


def _describe_failure(exc: Exception) -> dict[str, str]:
    """The problem a failed job reports. A terrain failure on a drawn area gets
    the same words the synchronous route uses -- "draw over land", "try again" --
    rather than an exception name the person cannot act on."""
    failure = area_service.terrain_failure(exc)
    if failure is None:
        return {
            "type": "/errors/analysis-failed",
            "title": "Analysis failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    kind, detail = failure
    if kind == "no_terrain":
        return {"type": "/errors/unanswerable", "title": "No terrain here", "detail": detail}
    return {
        "type": "/errors/provider-unavailable",
        "title": "Terrain unavailable",
        "detail": detail,
    }


def _run_job(
    job_id: str,
    options: dict[str, Any] | None,
    *,
    store: JobStore | None,
    steps: tuple[Step, ...],
    params: dict[str, Any],
    analyse: Callable[[ContourAnalysisOptions, StageReporter], ContourAnalysis],
) -> JobRecord:
    """The body both kinds of job share: run, report, settle, register."""
    target = store if store is not None else get_store()
    progress = JobProgress(steps=steps)
    now = time.time()
    record = JobRecord(
        job_id=job_id,
        progress=progress.as_dict(),
        params=params,
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    target.put(record)

    # Wait for an analysis slot while the status still says `queued`: on a
    # 512 MB system two analyses at once is an OOM kill, and a job that waits
    # its turn is the honest version of that.
    slots = capacity.get_slots()
    slots.acquire()
    try:
        return _execute(job_id, record, progress, target, options, analyse)
    finally:
        slots.release()
        release_memory()


def _execute(
    job_id: str,
    record: JobRecord,
    progress: JobProgress,
    target: JobStore,
    options: dict[str, Any] | None,
    analyse: Callable[[ContourAnalysisOptions, StageReporter], ContourAnalysis],
) -> JobRecord:
    reporter = _Reporter(record, progress, target)
    progress.start()
    reporter.flush()

    try:
        opts = ContourAnalysisOptions(**(options or {}))
        analysis = analyse(opts, reporter)
    except Exception as exc:
        # A trace id travels with the failure, as it does on every synchronous
        # error. Without one the reason reaches the screen but nothing connects
        # it to the log line that has the traceback -- which is the whole point
        # of quoting an id to a user.
        trace_id = uuid.uuid4().hex[:12]
        progress.error = {**_describe_failure(exc), "trace_id": trace_id}
        # A step already recorded its own failure via the reporter; if the throw
        # came from outside a stage (bad options, say) nothing has, so the job
        # would be unsettleable. Fail the first outstanding step to keep the
        # state machine's invariant that a terminal state is always reachable.
        _force_settle(progress, f"{type(exc).__name__}: {exc}")
        record.progress = progress.as_dict()
        record.finished_at = time.time()
        target.put(record)
        log.warning("analysis job failed", job_id=job_id, trace_id=trace_id, error=str(exc))
        return record

    try:
        _degrade_enrichment(progress, analysis)
        progress.settle()
        body = analysis.as_dict()
        # Register the parsed DEM and stamp its id onto the result, exactly as
        # the synchronous endpoint does. Without this an analysis run as a job
        # comes back with no `dem_id`, and every follow-up call -- streams,
        # terrain tiles, click-to-delineate, available land -- has nothing to
        # address. That is not a small omission: it is most of the UI.
        body["dem_id"] = dem_cache.remember_analysis(analysis)
        # The result's own address. Export and the PDF report are keyed by job,
        # not by `analysis_id`, and a client holding only the result -- the UI's
        # export link -- had no way to know it: the link it built from
        # `analysis_id` answered 404 on every run.
        body["job_id"] = job_id
        record.result = body
    except Exception as exc:
        # The analysis itself succeeded; settling it did not. Report that rather
        # than leaving the job pinned mid-run -- an unsettled job is
        # indistinguishable from a hang and the client polls it for ever.
        log.warning("analysis finished but could not be settled", job_id=job_id, error=str(exc))
        progress.error = {
            "type": "/errors/internal",
            "title": "Analysis could not be finalised",
            "detail": f"{type(exc).__name__}: {exc}",
            "trace_id": uuid.uuid4().hex[:12],
        }
        _force_settle(progress, f"{type(exc).__name__}: {exc}")

    record.progress = progress.as_dict()
    record.finished_at = time.time()
    target.put(record)
    log.info(
        "analysis job settled",
        job_id=job_id,
        state=progress.state,
        elapsed_s=round(record.elapsed_s or 0.0, 2),
    )
    return record


def _force_settle(progress: JobProgress, reason: str) -> None:
    """Drive a job to a terminal state whatever its step outcomes look like.

    The state machine refuses to settle with work outstanding, which is the right
    default -- but a job that cannot settle can never stop being polled, so the
    error path needs a way through.
    """
    if progress.state in ("done", "partial", "failed", "cancelled"):
        return
    for name, outcome in list(progress.outcomes.items()):
        if outcome in ("pending", "running"):
            progress.fail_step(name, reason)
    progress.settle()
