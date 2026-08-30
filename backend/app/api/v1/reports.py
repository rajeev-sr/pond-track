"""PDF report endpoints (M7-3).

    POST /api/v1/reports/generate     {job_id} -> report_id
    GET  /api/v1/reports/{id}/download

Two steps rather than one, because rendering is slow enough to notice — a page
of vector contours takes a couple of seconds — and because a report is a
*document* with an identity. Handing back an id means it can be fetched twice,
linked to, or downloaded by someone who did not run the analysis.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Form, Response, status

from app.core.errors import NotFoundProblem, UnanswerableProblem
from app.core.logging import get_logger
from app.services.job_store import get_store

log = get_logger("api.reports")

router = APIRouter(prefix="/reports", tags=["reports"])

#: Rendered PDFs, in process and bounded. A PDF is a few hundred kilobytes and
#: this is a single-node local deployment; the durable artefact is the analysis
#: it came from, which can always be re-rendered.
_REPORTS: dict[str, dict[str, Any]] = {}
_REPORT_LIMIT = 8
_lock = threading.Lock()


def _remember(report_id: str, pdf: bytes, filename: str, job_id: str) -> None:
    with _lock:
        while len(_REPORTS) >= _REPORT_LIMIT:
            _REPORTS.pop(next(iter(_REPORTS)))
        _REPORTS[report_id] = {
            "pdf": pdf,
            "filename": filename,
            "job_id": job_id,
            "generated_at": time.time(),
        }


@router.post(
    "/generate",
    status_code=status.HTTP_201_CREATED,
    summary="Render a finished analysis as a PDF report (M7-1, M7-3)",
    description=(
        "Renders the analysis behind `job_id` into an A4 PDF and returns the id "
        "to download it by.\n\n"
        "The report is written for someone who has to act on the recommendation "
        "or defend it, not for someone who already knows how the tool works. It "
        "carries the map, the catchment and rainfall figures, the per-criterion "
        "score breakdown, the data sources with their licences — and a "
        "limitations section that is not an appendix.\n\n"
        "That last part is the reason this is not just a JSON dump. A PDF "
        "outlives the API response that explained itself and gets forwarded to "
        "people who never saw the tool, so the caveats have to travel with the "
        "numbers: the terrain is interpolated between contours, the pond "
        "footprint has no orientation, land tenure is not modelled, and the cost "
        "is a single rate applied to a volume.\n\n"
        "Available for `partial` analyses too, with the missing layer stated on "
        "the first page."
    ),
)
async def generate_report(
    job_id: Annotated[str, Form(description="From POST /api/v1/analysis.")],
) -> Any:
    record = get_store().get(job_id)
    if record is None:
        raise NotFoundProblem(detail=f"no analysis job with id {job_id!r}.", job_id=job_id)
    state = record.progress.get("state")
    if state not in ("done", "partial") or not record.result:
        raise UnanswerableProblem(
            detail=(
                f"job {job_id!r} is {state!r}, so there is nothing to report on. "
                f"Poll /api/v1/analysis/{job_id}/status until it is terminal."
            ),
            job_id=job_id,
            state=state,
        )

    from fastapi.concurrency import run_in_threadpool

    from app.services import report

    def _render() -> bytes:
        return report.render_pdf(record.result or {}, warnings=record.progress.get("warnings"))

    try:
        pdf = await run_in_threadpool(_render)
    except ValueError as exc:
        # An analysis with no candidate sites has nothing to recommend, which is
        # a legitimate answer rather than a rendering failure.
        raise UnanswerableProblem(detail=str(exc), job_id=job_id) from exc
    except ImportError as exc:
        raise UnanswerableProblem(
            detail=(
                "the PDF renderer is not available in this deployment: "
                f"{exc}. WeasyPrint needs Pango and Cairo from the system "
                "packages; see backend/Dockerfile."
            ),
            job_id=job_id,
        ) from exc

    report_id = uuid.uuid4().hex[:16]
    filename = f"pond-siting-report-{job_id[:8]}.pdf"
    _remember(report_id, pdf, filename, job_id)
    log.info("report generated", report_id=report_id, job_id=job_id, bytes=len(pdf))
    return {
        "report_id": report_id,
        "job_id": job_id,
        "download_url": f"/api/v1/reports/{report_id}/download",
        "filename": filename,
        "size_bytes": len(pdf),
        "pages_hint": "4 pages of A4",
    }


@router.get(
    "/{report_id}/download",
    summary="Download a rendered report",
    description=(
        "Returns the PDF as an attachment. Reports are held in memory and "
        "bounded to the most recent few — the durable artefact is the analysis, "
        "which can always be rendered again."
    ),
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_report(report_id: str) -> Response:
    with _lock:
        entry = _REPORTS.get(report_id)
    if entry is None:
        raise NotFoundProblem(
            detail=(
                f"no report with id {report_id!r}. Reports are held in memory and "
                "do not survive a restart; generate it again from its analysis."
            ),
            report_id=report_id,
        )
    return Response(
        content=entry["pdf"],
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )
