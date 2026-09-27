"""`/land/available` answers within a deadline, from the cache an analysis filled.

It used to read land cover straight from S3 on every call, with no deadline.
From the lab systems that took minutes and the gateway answered 504.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.api.v1 import contour as contour_api
from app.providers.elevation.base import Bounds
from app.providers.landcover import worldcover
from app.providers.landcover.worldcover import LandCover
from app.providers.vector import osm_cache
from app.providers.vector.overpass import OsmContext
from app.services import dem_cache, provider_cache
from app.tests.area_fakes import DURG_BBOX, fake_dem

pytestmark = pytest.mark.integration


@pytest.fixture
def terrain(monkeypatch: pytest.MonkeyPatch) -> tuple[str, Any, Bounds]:
    monkeypatch.setattr(osm_cache, "fetch_cached", lambda *a, **k: (OsmContext(), True))
    bounds = Bounds(*DURG_BBOX)
    dem = fake_dem(bounds)
    dem_id = dem_cache.remember(None, dem, None, bounds=bounds, source="copernicus_glo30")
    return dem_id, dem, bounds


def cover_for(dem: Any) -> LandCover:
    return LandCover(
        codes=np.full(dem.shape, 40, dtype=np.uint8),  # cropland
        fractions={"cropland": 1.0},
        dominant_class="cropland",
        tiles_used=["fake"],
    )


def test_a_hanging_land_cover_is_left_out_not_waited_for(
    client: Any, monkeypatch: pytest.MonkeyPatch, terrain: tuple[str, Any, Bounds]
) -> None:
    release = threading.Event()

    def hang(*_a: object, **_k: object) -> LandCover:
        release.wait(10.0)
        raise AssertionError("the route should have stopped waiting")

    monkeypatch.setattr(worldcover, "fetch_landcover", hang)
    monkeypatch.setattr(contour_api, "LAND_BUDGET_S", 0.5)
    dem_id, _, _ = terrain
    try:
        t = time.perf_counter()
        r = client.post("/api/v1/land/available", data={"dem_id": dem_id})
        elapsed = time.perf_counter() - t
    finally:
        release.set()
    assert r.status_code == 200
    assert elapsed < 5.0, f"it waited for the hanging layer ({elapsed:.1f}s)"
    assert [u["layer"] for u in r.json()["unavailable"]] == ["land_cover"]


def test_the_land_cover_an_analysis_cached_is_used(
    client: Any, monkeypatch: pytest.MonkeyPatch, terrain: tuple[str, Any, Bounds]
) -> None:
    from app.config import get_settings

    dem_id, dem, bounds = terrain
    provider_cache.cached_landcover(
        bounds.as_tuple(),
        dem.shape,
        dem.transform,
        dem.epsg,
        Path(get_settings().COG_STORE_PATH),
        fetch=lambda *a, **k: cover_for(dem),
    )

    def offline(*_a: object, **_k: object) -> LandCover:
        raise AssertionError("the cached land cover should have been used")

    monkeypatch.setattr(worldcover, "fetch_landcover", offline)
    r = client.post("/api/v1/land/available", data={"dem_id": dem_id})
    assert r.status_code == 200
    assert r.json()["unavailable"] == []
    assert r.json()["sources"]["land_cover"] is not None
