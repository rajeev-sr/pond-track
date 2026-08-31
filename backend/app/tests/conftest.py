"""Shared fixtures. No network, no database, unless a test asks for them."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Set before app.config is imported anywhere, so tests never read a developer's
# real .env or reach a real database.
#
# Port 1 is deliberate: nothing listens there, so the readiness probe fails
# instantly and deterministically. Using "localhost:5432" would make the test
# pass or fail depending on whether the developer happens to be running
# Postgres -- a flaky test disguised as a working one.
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "1")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")


@pytest.fixture(autouse=True)
def _isolated_cache_store(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[None]:
    """Point every disk cache at a fresh temporary directory for each test.

    `COG_STORE_PATH` defaults to `/data/cache`, which is a volume inside the API
    container. On a host run that path is unwritable, so the provider cache logged
    `Permission denied: '/data'` on every enrichment — harmless, since a failed
    cache write never costs the caller their answer, but it is noise that hides
    real warnings.

    Per test rather than per session, and that distinction was not obvious: with
    one store for the whole run, a test that stubs SoilGrids to fail was served a
    profile *another* test had cached for the same coordinates, and its
    `no_soil_lulc` assertion saw `full`. Sharing a cache across analyses is
    exactly what the cache is for in production and exactly wrong between tests.
    """
    import os

    from app.config import get_settings

    store = tmp_path / "cache-store"
    previous = os.environ.get("COG_STORE_PATH")
    os.environ["COG_STORE_PATH"] = str(store)
    get_settings.cache_clear()

    # The rainfall cache lives in Postgres, and it intercepts before any stubbed
    # provider is reached. On the host that is invisible -- the database
    # hostname does not resolve, the read fails, and the stub runs. Inside the
    # API container it does resolve, so eight provider tests were served real
    # cached series instead of their fixtures and asserted against the wrong
    # data. A test must not be able to reach a cache it did not populate.
    from app.providers.rainfall import cache as rainfall_cache

    # ...but not for the integration tests that exist to exercise that cache
    # against a real database. Neutralising it for those made them assert against
    # a cache that could never return anything.
    wants_database = request.node.get_closest_marker("integration") is not None
    original_read = rainfall_cache.read
    if not wants_database:
        rainfall_cache.read = lambda *_a, **_k: None  # type: ignore[assignment]
    try:
        yield
    finally:
        rainfall_cache.read = original_read  # type: ignore[assignment]
        if previous is None:
            os.environ.pop("COG_STORE_PATH", None)
        else:
            os.environ["COG_STORE_PATH"] = previous
        get_settings.cache_clear()


@pytest.fixture
def settings() -> Iterator[object]:
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    yield Settings(_env_file=None)  # type: ignore[call-arg]
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


class StubCRS:
    """Minimal CRS stand-in, so CRSGuard is testable without GDAL/pyproj."""

    def __init__(self, projected: bool, name: str = "stub") -> None:
        self.is_projected = projected
        self._name = name

    def __str__(self) -> str:
        return self._name


@pytest.fixture
def projected_crs() -> StubCRS:
    return StubCRS(True, "EPSG:32643")


@pytest.fixture
def geographic_crs() -> StubCRS:
    return StubCRS(False, "EPSG:4326")
