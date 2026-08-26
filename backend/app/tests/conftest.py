"""Shared fixtures. No network, no database, unless a test asks for them."""

from __future__ import annotations

import os
from collections.abc import Iterator

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
