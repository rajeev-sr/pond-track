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
from typing import Any

from app.core.logging import get_logger
from app.services import dem_cache
from app.services.contour_analysis import ContourAnalysisOptions, analyze_contour_map
from app.services.job_store import JobRecord, JobStore, get_store
from app.services.jobs import JobProgress

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
    """Execute one analysis job to a terminal state and return its record.

    Never raises for an analysis failure: a failed job is a `failed` record with
    an RFC 7807-shaped `error`, which is what the status endpoint serves. A raise
    here would lose the job instead of reporting it.
    """
    target = store if store is not None else get_store()
    progress = JobProgress()
    now = time.time()
    record = JobRecord(
        job_id=job_id,
        progress=progress.as_dict(),
        params=dict(options or {}),
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    target.put(record)

    reporter = _Reporter(record, progress, target)
    progress.start()
    reporter.flush()

    try:
        opts = ContourAnalysisOptions(**(options or {}))
        analysis = analyze_contour_map(data, filename, opts, reporter=reporter)
    except Exception as exc:
        # A trace id travels with the failure, as it does on every synchronous
        # error. Without one the reason reaches the screen but nothing connects
        # it to the log line that has the traceback -- which is the whole point
        # of quoting an id to a user.
        trace_id = uuid.uuid4().hex[:12]
        progress.error = {
            "type": "/errors/analysis-failed",
            "title": "Analysis failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "trace_id": trace_id,
        }
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
        body["dem_id"] = dem_cache.remember(analysis.parsed, analysis.dem, analysis.interpolation)
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
