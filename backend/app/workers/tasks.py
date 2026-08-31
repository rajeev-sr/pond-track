"""Celery tasks (M6-1, M6-2).

Thin by design: the whole job lifecycle is `services.job_runner`, which runs as
plain synchronous code. That split is what lets the API execute a job in its own
process when no worker is up -- a local-only deployment should not need a second
container running before it can answer -- and it keeps the interesting logic
testable without a broker.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger("workers.tasks")


@celery_app.task(name="contour.analyze", bind=True, max_retries=0)
def analyze_task(
    self: Any,
    job_id: str,
    data: bytes,
    filename: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one contour analysis as a job.

    `max_retries=0` because retries are the *step's* business, not the task's:
    each provider already backs off inside its own adapter, and re-running a
    24-second pipeline because one HTTP call failed would waste the twenty
    seconds of work that succeeded. A task-level retry would also reset the
    progress the client is watching.
    """
    from app.services.job_runner import run_analysis_job

    log.info("worker picked up job", job_id=job_id, task_id=self.request.id)
    record = run_analysis_job(job_id, data, filename, options)
    return {"job_id": job_id, "state": record.progress.get("state")}


def worker_available(timeout: float = 0.5) -> bool:
    """Whether any Celery worker answers a ping.

    Checked rather than assumed. The compose file declares a `celery-worker`
    service but it is not necessarily running, and accepting a job into a queue
    nothing is draining would leave the client polling `queued` forever -- the
    one failure mode an async API must not have.
    """
    try:
        replies = celery_app.control.inspect(timeout=timeout).ping()
    except Exception as exc:
        log.info("no celery worker reachable", error=str(exc))
        return False
    return bool(replies)
