"""The API process serving the built UI (`FRONTEND_DIST`), as on the lab systems.

What matters is what must *not* change: every API route, the docs and the spec
answer exactly as before, and an unknown API path is still a 404 problem rather
than the app's HTML with a 200.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root></div>")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)")
    (root / "favicon.svg").write_text("<svg/>")
    return root


@pytest.fixture
def web(dist: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


class TestTheUiIsServed:
    def test_the_root_is_the_app(self, web: Any) -> None:
        response = web.get("/")
        assert response.status_code == 200
        assert "id=root" in response.text

    @pytest.mark.parametrize("route", ["/workspace", "/method", "/reference"])
    def test_client_side_routes_fall_back_to_the_app(self, web: Any, route: str) -> None:
        """A reload on /workspace must not 404: the router is in the page."""
        response = web.get(route)
        assert response.status_code == 200
        assert "id=root" in response.text

    def test_index_is_always_revalidated(self, web: Any) -> None:
        assert web.get("/").headers["cache-control"] == "no-cache"

    def test_hashed_assets_are_cached_for_a_year(self, web: Any) -> None:
        response = web.get("/assets/index-abc123.js")
        assert response.status_code == 200
        assert "immutable" in response.headers["cache-control"]

    def test_root_files_are_served_as_themselves(self, web: Any) -> None:
        assert web.get("/favicon.svg").text == "<svg/>"

    def test_no_path_escapes_dist(self, web: Any) -> None:
        response = web.get("/..%2F..%2Fetc%2Fpasswd")
        assert "root:" not in response.text


class TestTheApiIsUntouched:
    def test_health(self, web: Any) -> None:
        assert web.get("/api/v1/health").json()["status"] == "ok"

    def test_docs_and_spec(self, web: Any) -> None:
        assert web.get("/openapi.json").json()["openapi"]
        assert "swagger" in web.get("/docs").text.lower()

    def test_an_unknown_api_path_is_a_404_problem_not_the_app(self, web: Any) -> None:
        response = web.get("/api/v1/nope")
        assert response.status_code == 404
        assert "id=root" not in response.text

    def test_the_spa_is_not_in_the_schema(self, web: Any) -> None:
        paths = web.get("/openapi.json").json()["paths"]
        assert "/{path}" not in paths and "/" not in paths


class TestOffByDefault:
    def test_without_frontend_dist_the_root_is_not_the_app(self, client: Any) -> None:
        assert "id=root" not in client.get("/").text
