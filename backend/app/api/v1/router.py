"""v1 API router. Feature routers are mounted here as each phase lands."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import contour, health, villages

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(contour.router)
api_router.include_router(villages.router)

# Mounted in later phases (docs/IMPLEMENTATION_PLAN.md 3):
#   M2  villages, terrain      M3  hydrology
#   M4  rainfall, land/soil    M5  land/available, pond
#   M6  suitability, analysis  M7  reports, export
