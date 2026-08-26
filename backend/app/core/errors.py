"""RFC 7807 problem-details errors and their FastAPI handlers (HLD 5.1).

Every error the API emits has the same shape, so the frontend has exactly one
error path to handle:

    {"type": "/errors/dem-unavailable", "title": "...", "status": 503,
     "detail": "...", "instance": "/api/v1/terrain/dem", "trace_id": "..."}
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

CONTENT_TYPE = "application/problem+json"


class ProblemError(Exception):
    """Base class for errors that map cleanly onto a problem-details response."""

    status: int = 500
    type: str = "/errors/internal"
    title: str = "Internal server error"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def to_problem(self, instance: str, trace_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            "trace_id": trace_id,
        }
        body.update(self.extra)
        return body


class ValidationProblem(ProblemError):
    status, type, title = 400, "/errors/validation", "Invalid request"


class NotFoundProblem(ProblemError):
    status, type, title = 404, "/errors/not-found", "Resource not found"


class AoiTooLargeProblem(ProblemError):
    status, type, title = 413, "/errors/aoi-too-large", "Area of interest too large"


class UnanswerableProblem(ProblemError):
    """422 -- well-formed request, but the world makes it unanswerable.

    Distinct from 400 on purpose: it lets the UI say "this point receives
    negligible runoff, try lower in the valley" instead of "invalid input"
    (HLD 5.1).
    """

    status, type, title = 422, "/errors/unanswerable", "Request cannot be answered"


class RateLimitedProblem(ProblemError):
    status, type, title = 429, "/errors/rate-limited", "Rate limit exceeded"


class ProviderUnavailableProblem(ProblemError):
    """503 -- every provider in a fallback chain failed (HLD 4.2 F)."""

    status, type, title = 503, "/errors/provider-unavailable", "Upstream data source unavailable"


class NotConfiguredProblem(ProblemError):
    """503 -- the capability exists but its credentials are absent (M8-12)."""

    status, type, title = 503, "/errors/not-configured", "Capability not configured"


def _trace_id(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    if isinstance(existing, str):
        return existing
    return uuid.uuid4().hex[:12]


def register_exception_handlers(app: FastAPI) -> None:
    from app.config import ConfigError
    from app.core.crs import CRSError
    from app.core.logging import get_logger

    log = get_logger("errors")

    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        trace = _trace_id(request)
        log.warning(
            "request_failed",
            type=exc.type,
            status=exc.status,
            detail=exc.detail,
            path=request.url.path,
            trace_id=trace,
        )
        return JSONResponse(
            status_code=exc.status,
            content=exc.to_problem(request.url.path, trace),
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(RequestValidationError)
    async def _pydantic(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace = _trace_id(request)
        return JSONResponse(
            status_code=400,
            content={
                "type": "/errors/validation",
                "title": "Invalid request",
                "status": 400,
                "detail": "One or more fields failed validation.",
                "instance": request.url.path,
                "trace_id": trace,
                "errors": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            },
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(CRSError)
    async def _crs(request: Request, exc: CRSError) -> JSONResponse:
        # A CRSError is a programming error, never the caller's fault -- log it
        # loudly, because it means metric maths was attempted on degrees.
        trace = _trace_id(request)
        log.error("crs_violation", detail=str(exc), path=request.url.path, trace_id=trace)
        return JSONResponse(
            status_code=500,
            content={
                "type": "/errors/crs",
                "title": "Coordinate system error",
                "status": 500,
                "detail": str(exc),
                "instance": request.url.path,
                "trace_id": trace,
            },
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(ConfigError)
    async def _config(request: Request, exc: ConfigError) -> JSONResponse:
        trace = _trace_id(request)
        return JSONResponse(
            status_code=503,
            content={
                "type": "/errors/not-configured",
                "title": "Capability not configured",
                "status": 503,
                "detail": str(exc),
                "instance": request.url.path,
                "trace_id": trace,
            },
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, _exc: Exception) -> JSONResponse:
        trace = _trace_id(request)
        log.exception("unhandled_exception", path=request.url.path, trace_id=trace)
        # Never leak a stack trace to the client (HLD 5.1, 2.6).
        return JSONResponse(
            status_code=500,
            content={
                "type": "/errors/internal",
                "title": "Internal server error",
                "status": 500,
                "detail": "An unexpected error occurred. Quote the trace_id when reporting it.",
                "instance": request.url.path,
                "trace_id": trace,
            },
            media_type=CONTENT_TYPE,
        )
