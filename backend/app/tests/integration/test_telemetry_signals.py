"""What the running app emits, end to end, with telemetry on.

The real FastAPI app, the real job runner and pipeline -- only the Copernicus
read is faked (`area_fakes`) and the exporters are in memory. These are the
properties the observability report rests on: one trace per job holding every
stage, request spans that mean what the client saw, and error bodies whose
trace id finds the trace.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

# Module level, not inside the test: with postponed annotations FastAPI resolves
# a route's parameter types from the module's globals, and a BackgroundTasks it
# cannot find is taken for a query parameter (422).
from fastapi import BackgroundTasks, FastAPI

from app.core import telemetry
from app.providers.elevation import copernicus_aws
from app.tests.area_fakes import DURG_BBOX, FakeFetch
from app.tests.otel_capture import Captured

STAGES = ["terrain", "condition", "flow_routing", "enrichment", "siting", "catchments"]


@pytest.fixture
def copernicus(monkeypatch: pytest.MonkeyPatch) -> FakeFetch:
    fake = FakeFetch()
    monkeypatch.setattr(copernicus_aws, "fetch_dem", fake)
    return fake


@pytest.fixture
def traced_client(otel: Captured) -> Iterator[Any]:
    """An app built *after* telemetry started, as uvicorn builds it."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def job(traced_client: Any, copernicus: FakeFetch, otel: Captured) -> dict[str, Any]:
    response = traced_client.post(
        "/api/v1/analysis/area", json={"bbox": DURG_BBOX, "enrich": False}
    )
    assert response.status_code == 202, response.text
    # TestClient runs the background job before returning, so it is finished.
    return {"response": response, "job_id": response.json()["job_id"]}


class TestOneTracePerJob:
    def test_the_job_descends_from_the_request_that_started_it(
        self, job: dict[str, Any], otel: Captured
    ) -> None:
        """request -> `BackgroundTask run_area_job` (FastAPI's span) -> job."""
        request = otel.span("POST /api/v1/analysis/area")
        task = otel.span("BackgroundTask run_area_job")
        job_span = otel.span("analysis.job")
        assert task.parent.span_id == request.context.span_id
        assert job_span.parent.span_id == task.context.span_id
        assert job_span.attributes["contour.job.id"] == job["job_id"]
        assert job_span.attributes["contour.job.state"] == "done"

    def test_every_stage_nests_under_the_analysis(
        self, job: dict[str, Any], otel: Captured
    ) -> None:
        analysis = otel.span("analysis.area")
        for stage in STAGES:
            span = otel.span(f"stage.{stage}")
            assert span.parent.span_id == analysis.context.span_id, stage
            assert span.context.trace_id == analysis.context.trace_id

    def test_the_wait_for_a_slot_is_its_own_span(self, job: dict[str, Any], otel: Captured) -> None:
        wait = otel.span("analysis.queue_wait")
        assert wait.parent.span_id == otel.span("analysis.job").context.span_id
        assert wait.attributes["contour.slots.waiting"] == 0

    def test_the_answer_is_on_the_analysis_span(self, job: dict[str, Any], otel: Captured) -> None:
        attrs = otel.span("analysis.area").attributes
        assert attrs["contour.sites"] >= 1
        assert attrs["contour.result.expected_water_m3"] > 0
        assert attrs["contour.tier"] == "terrain_only"
        assert attrs["contour.area_km2"] == pytest.approx(8.53, abs=0.05)


class TestRequestSpansMeanWhatTheClientSaw:
    def test_a_job_start_span_ends_before_its_job_does(
        self, job: dict[str, Any], otel: Captured
    ) -> None:
        """Starlette runs the job inside the request's ASGI call. Measured to
        the end of that call, the 202 would read as the whole analysis."""
        request = otel.span("POST /api/v1/analysis/area")
        job_span = otel.span("analysis.job")
        assert request.end_time < job_span.end_time

    def test_the_request_metric_is_measured_to_the_last_byte(self, otel: Captured) -> None:
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.post("/start")
        def start(background: BackgroundTasks) -> dict[str, bool]:
            background.add_task(lambda: telemetry.traced("work", lambda: time.sleep(0.4)))
            return {"started": True}

        telemetry.instrument_app(app)
        with TestClient(app) as c:
            assert c.post("/start").status_code == 200

        (point,) = [
            p
            for p in otel.points("http.server.request.duration")
            if p.attributes["http.route"] == "/start"
        ]
        assert point.sum < 0.3, "the background work was billed to the request"
        assert point.attributes["http.response.status_code"] == 200
        server, work = otel.span("POST /start"), otel.span("work")
        assert (server.end_time - server.start_time) / 1e9 < 0.3
        # Still one trace: the work hangs off FastAPI's span for the task,
        # which hangs off the request.
        (task,) = [s for s in otel.spans() if s.name.startswith("BackgroundTask")]
        assert task.parent.span_id == server.context.span_id
        assert work.parent.span_id == task.context.span_id
        assert work.end_time > server.end_time

    def test_the_liveness_probe_is_counted_but_not_traced(
        self, traced_client: Any, otel: Captured
    ) -> None:
        assert traced_client.get("/api/v1/health").status_code == 200
        assert not [s for s in otel.spans() if "health" in s.name]
        routes = {p.attributes["http.route"] for p in otel.points("http.server.request.duration")}
        assert "/api/v1/health" in routes


class TestErrorsCarryTheirTrace:
    def test_a_problem_body_quotes_the_trace_id(self, traced_client: Any, otel: Captured) -> None:
        response = traced_client.get("/api/v1/analysis/no-such-job/status")
        assert response.status_code == 404
        trace_id = response.json()["trace_id"]
        request = otel.span("GET /api/v1/analysis/{job_id}/status")
        assert trace_id == format(request.context.trace_id, "032x")
        assert response.headers["X-Trace-Id"] == trace_id
        assert request.attributes["contour.problem.status"] == 404

    def test_a_gateway_request_id_is_echoed_but_the_trace_id_is_its_own(
        self, traced_client: Any, otel: Captured
    ) -> None:
        """Behind nginx every request arrives with X-Request-ID = $request_id. It
        is echoed, as before; it is not a trace id, so X-Trace-Id is separate."""
        response = traced_client.get(
            "/api/v1/analysis/no-such-job/status", headers={"X-Request-ID": "nginx-1234"}
        )
        assert response.headers["X-Request-ID"] == "nginx-1234"
        assert response.headers["X-Trace-Id"] == response.json()["trace_id"] != "nginx-1234"

    def test_a_failed_job_reports_its_own_trace(
        self, traced_client: Any, monkeypatch: pytest.MonkeyPatch, otel: Captured
    ) -> None:
        from opentelemetry.trace import StatusCode

        from app.providers.base import ProviderUnavailableError

        def down(*_a: Any, **_k: Any) -> Any:
            raise ProviderUnavailableError("copernicus", "bucket unreachable")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", down)
        started = traced_client.post(
            "/api/v1/analysis/area", json={"bbox": DURG_BBOX, "enrich": False}
        )
        status = traced_client.get(f"/api/v1/analysis/{started.json()['job_id']}/status").json()
        assert status["state"] == "failed"
        stage = otel.span("stage.terrain")
        assert stage.status.status_code is StatusCode.ERROR
        assert status["error"]["trace_id"] == format(stage.context.trace_id, "032x")


class TestMetricsOfARun:
    def test_each_stage_records_a_duration(self, job: dict[str, Any], otel: Captured) -> None:
        stages = {p.attributes["contour.stage"] for p in otel.points("contour.stage.duration")}
        assert set(STAGES) <= stages

    def test_the_job_and_its_wait_are_measured(self, job: dict[str, Any], otel: Captured) -> None:
        (state,) = otel.points("contour.jobs")
        assert state.attributes == {"contour.input": "area", "contour.state": "done"}
        (wait,) = otel.points("contour.job.queue_wait")
        assert wait.count == 1
        (analysis,) = otel.points("contour.analysis.duration")
        assert analysis.attributes["contour.outcome"] == "ok"

    def test_a_terrain_cache_lookup_is_counted(self, job: dict[str, Any], otel: Captured) -> None:
        results = {
            p.attributes["contour.result"]
            for p in otel.points("contour.cache.lookups")
            if p.attributes["contour.cache"] == "dem"
        }
        assert results == {"miss"}
