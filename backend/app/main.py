"""FastAPI application factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse

from app.api.v1.router import api_router
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.LOG_LEVEL, s.LOG_JSON)
    log = get_logger("startup")

    # M0-17: fail fast on anything fatal, warn about optional capabilities.
    warnings = s.validate_startup()
    log.info("starting", **s.startup_report())
    for w in warnings:
        log.warning("capability_unavailable", detail=w)
    # Terrain acquisition needs no credential (Copernicus GLO-30 open bucket),
    # so there is deliberately no warning for it here. Anything that *is*
    # unconfigured has already been logged above as a capability warning.
    yield
    log.info("shutdown")


#: Pinned deliberately -- see the `/redoc` route below for what a moving tag did.
REDOC_JS_URL = "https://cdn.jsdelivr.net/npm/redoc@2.5.3/bundles/redoc.standalone.js"


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Contour - AI-based Village Pond Planning System",
        description=(
            "Recommends pond sites for Indian villages from terrain, rainfall, "
            "soil and land-cover data. See docs/HLD.md for the design."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        # ReDoc is mounted by hand below so its bundle can be pinned.
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @app.get("/redoc", include_in_schema=False)
    async def redoc() -> HTMLResponse:
        """ReDoc, with its script pinned to a version that exists.

        FastAPI's default points at `redoc@next` on jsdelivr, and that tag now
        resolves to 3.0.0-rc.0, whose published files do not include
        `bundles/redoc.standalone.js`. The request 404s, nothing is logged, and
        the page renders as a blank white screen -- no error, no clue. Swagger UI
        is unaffected because its URL pins a major version (`swagger-ui-dist@5`).
        So this pins an exact one: a moving tag is what broke, and `@2` would
        leave the same trap set for whenever 2.x is superseded.
        """
        return get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} - ReDoc",
            redoc_js_url=REDOC_JS_URL,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _trace(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Attach a trace id so every log line and error body can be correlated."""
        request.state.trace_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.trace_id
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
