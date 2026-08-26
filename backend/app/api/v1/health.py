"""Liveness and readiness endpoints.

/health       -- is the process up? No dependencies touched, never fails on a
                 degraded backing service. Used by Docker HEALTHCHECK.
/health/ready -- can it actually serve work? Probes DB and Redis and reports
                 per-dependency status plus which provider features are
                 configured. Returns 503 only if a *required* dependency is down.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response

from app.config import get_settings

router = APIRouter(tags=["system"])
_STARTED = time.monotonic()


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "status": "ok",
        "service": "contour-api",
        "version": "0.1.0",
        "env": s.ENV,
        "demo_mode": s.DEMO_MODE,
        "uptime_s": round(time.monotonic() - _STARTED, 1),
    }


def _probe_database() -> dict[str, Any]:
    try:
        from sqlalchemy import text

        from app.db.session import get_engine

        t0 = time.perf_counter()
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            postgis = conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'postgis'")
            ).scalar_one_or_none()
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "postgis": bool(postgis),
        }
    except Exception as exc:
        return {"status": "down", "error": f"{type(exc).__name__}: {exc}"[:200]}


def _probe_redis() -> dict[str, Any]:
    try:
        import redis as redis_lib

        t0 = time.perf_counter()
        client = redis_lib.Redis.from_url(get_settings().REDIS_URL, socket_timeout=2)
        client.ping()
        return {"status": "ok", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:
        return {"status": "down", "error": f"{type(exc).__name__}: {exc}"[:200]}


@router.get("/health/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    s = get_settings()
    checks = {"database": _probe_database(), "redis": _probe_redis()}
    required_down = [name for name, c in checks.items() if c["status"] != "ok"]
    if required_down:
        response.status_code = 503
    return {
        "status": "degraded" if required_down else "ready",
        "checks": checks,
        "features": {
            f: ("available" if s.is_available(f) else f"missing: {', '.join(s.missing_for(f))}")
            for f in s.FEATURE_REQUIREMENTS
        },
    }
