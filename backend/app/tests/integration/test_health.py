"""Health endpoints through the real ASGI app.

Marked integration because it builds the app, but it needs no database: the
readiness probe is expected to report dependencies as down here, and that is
precisely the behaviour under test.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestLiveness:
    def test_returns_ok_with_no_backing_services(self, client) -> None:  # type: ignore[no-untyped-def]
        # Liveness must never depend on Postgres or Redis, or Docker will kill a
        # container that is merely waiting for its database.
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "contour-api"
        assert "uptime_s" in body

    def test_echoes_a_trace_id_header(self, client) -> None:  # type: ignore[no-untyped-def]
        r = client.get("/api/v1/health", headers={"X-Request-ID": "trace-me-123"})
        assert r.headers["X-Request-ID"] == "trace-me-123"

    def test_generates_a_trace_id_when_absent(self, client) -> None:  # type: ignore[no-untyped-def]
        assert len(client.get("/api/v1/health").headers["X-Request-ID"]) == 12


class TestReadiness:
    def test_reports_degraded_when_dependencies_are_down(self, client) -> None:  # type: ignore[no-untyped-def]
        r = client.get("/api/v1/health/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert set(body["checks"]) == {"database", "redis"}

    def test_lists_every_gated_feature(self, client) -> None:  # type: ignore[no-untyped-def]
        features = client.get("/api/v1/health/ready").json()["features"]
        for f in ("dem_acquisition", "bhuvan_layers", "bhoonidhi_cartodem", "sentinel2_ndwi"):
            assert f in features

    def test_unconfigured_features_say_what_is_missing(self, client) -> None:  # type: ignore[no-untyped-def]
        features = client.get("/api/v1/health/ready").json()["features"]
        assert "BHUVAN_TOKEN" in features["bhuvan_layers"]

    def test_dem_acquisition_available_without_any_key(self, client) -> None:  # type: ignore[no-untyped-def]
        # The headline consequence of Decision 12: an empty .env still gives a
        # runnable mandatory pipeline.
        features = client.get("/api/v1/health/ready").json()["features"]
        assert features["dem_acquisition"] == "available"

    def test_never_leaks_a_credential_value(self, client) -> None:  # type: ignore[no-untyped-def]
        assert "PASSWORD" not in client.get("/api/v1/health/ready").text.upper()


class TestOpenApi:
    def test_schema_is_served(self, client) -> None:  # type: ignore[no-untyped-def]
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"].startswith("Contour")
        assert "/api/v1/health" in schema["paths"]

    def test_swagger_ui_loads(self, client) -> None:  # type: ignore[no-untyped-def]
        assert client.get("/docs").status_code == 200


class TestErrorHandling:
    def test_unknown_route_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        assert client.get("/api/v1/nope").status_code == 404
