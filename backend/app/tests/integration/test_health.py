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
    """Readiness has to be correct in both directions, and the suite runs in both.

    The offline conftest points the database and Redis probes at a closed port,
    so the endpoint should report `degraded`. Run inside the container -- which
    is how the integration suite executes against a real stack -- those services
    genuinely answer, and it should report `ready`.

    An earlier version asserted 503 unconditionally and failed the moment it met
    a working stack, reporting healthy dependencies as a defect. What is actually
    invariant is that the reported status matches the probes, and that both
    dependencies are always named either way.
    """

    def test_the_status_matches_what_the_probes_found(self, client) -> None:  # type: ignore[no-untyped-def]
        r = client.get("/api/v1/health/ready")
        body = r.json()
        assert set(body["checks"]) == {"database", "redis"}, "a dependency vanished from the report"

        # Each check is `{"status": "ok" | ..., "latency_ms": ...}`.
        all_up = all(check.get("status") == "ok" for check in body["checks"].values())
        if all_up:
            assert r.status_code == 200
            assert body["status"] == "ready"
        else:
            assert r.status_code == 503
            assert body["status"] == "degraded"

    def test_a_closed_port_is_reported_degraded(self, client) -> None:  # type: ignore[no-untyped-def]
        """The offline case specifically: nothing reachable means not ready."""
        r = client.get("/api/v1/health/ready")
        checks = r.json()["checks"]
        down = [name for name, check in checks.items() if check.get("status") != "ok"]
        if not down:
            pytest.skip("every dependency is reachable here; nothing to degrade")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"

    def test_lists_the_data_capabilities(self, client) -> None:  # type: ignore[no-untyped-def]
        features = client.get("/api/v1/health/ready").json()["features"]
        for f in ("dem_acquisition", "land_cover", "soil", "rainfall_reanalysis"):
            assert f in features

    def test_every_feature_is_available_because_none_needs_a_key(
        self, client
    ) -> None:  # type: ignore[no-untyped-def]
        """★ Readiness reports no missing credentials, because there are none.

        This used to assert the opposite -- that `bhuvan_layers` reported
        "missing: BHUVAN_TOKEN". Five such lines described features that were
        never built, and read to anyone running the stack as five things wrong
        with their setup.
        """
        features = client.get("/api/v1/health/ready").json()["features"]
        assert features, "no features reported at all"
        for name, state in features.items():
            assert state == "available", f"{name} reports {state!r}"
            assert "missing" not in state

    def test_the_response_names_no_credential(self, client) -> None:  # type: ignore[no-untyped-def]
        body = str(client.get("/api/v1/health/ready").json()).lower()
        for word in ("api_key", "token", "client_secret", "bhuvan", "bhoonidhi"):
            assert word not in body, word

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
