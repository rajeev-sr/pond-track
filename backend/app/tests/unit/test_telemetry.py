"""The telemetry module on its own: inert by default, faithful when on."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pytest

from app.core import telemetry
from app.core.logging import configure_logging, get_logger
from app.tests.otel_capture import Captured, start_capture


class TestInertByDefault:
    """No collector named: nothing is installed and nothing is recorded."""

    def test_setup_declines_without_an_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        telemetry.reset_for_tests()
        assert telemetry.setup() is False
        assert telemetry.enabled() is False

    def test_the_sdk_switch_wins_over_an_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        assert telemetry.configured_endpoint() is None

    def test_helpers_are_safe_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        telemetry.reset_for_tests()
        with telemetry.span("anything", {"k": 1}) as current:
            assert not current.is_recording()
            assert telemetry.current_trace_id() is None
        telemetry.annotate(k=1)
        telemetry.record_stage("terrain", 1.0, ok=True)
        telemetry.record_cache("dem", hit=True)
        telemetry.record_rejection("queue_full")


class TestSpans:
    def test_a_span_is_recorded_with_its_attributes(self, otel: Captured) -> None:
        with telemetry.span("work", {"contour.stage": "terrain", "n": np.int64(3)}):
            telemetry.annotate(extra="yes")
        span = otel.span("work")
        assert span.attributes["contour.stage"] == "terrain"
        assert span.attributes["n"] == 3
        assert span.attributes["extra"] == "yes"

    def test_an_escaping_exception_marks_the_span_as_an_error(self, otel: Captured) -> None:
        from opentelemetry.trace import StatusCode

        with pytest.raises(ValueError), telemetry.span("doomed"):
            raise ValueError("no terrain")
        span = otel.span("doomed")
        assert span.status.status_code is StatusCode.ERROR
        assert any(e.name == "exception" for e in span.events)

    def test_the_trace_id_is_the_w3c_form_jaeger_searches_by(self, otel: Captured) -> None:
        with telemetry.span("outer") as current:
            trace_id = telemetry.current_trace_id()
        assert trace_id == format(current.get_span_context().trace_id, "032x")
        assert len(trace_id) == 32

    def test_children_nest_under_the_current_span(self, otel: Captured) -> None:
        with telemetry.span("parent"), telemetry.span("child"):
            pass
        assert otel.span("child").parent.span_id == otel.span("parent").context.span_id

    def test_context_follows_work_into_a_thread_pool(self, otel: Captured) -> None:
        """Enrichment fans out to threads; without propagation each would start
        a trace of its own and the layer spans would float free of the analysis."""
        from concurrent.futures import ThreadPoolExecutor

        with telemetry.span("stage.enrichment"), ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(lambda: telemetry.traced("enrichment.soil", lambda: None)).result()
        child, parent = otel.span("enrichment.soil"), otel.span("stage.enrichment")
        assert child.context.trace_id == parent.context.trace_id
        assert child.parent.span_id == parent.context.span_id


class TestAttributeCleaning:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("text", "text"),
            (True, True),
            (np.float32(2.5), 2.5),
            ([1, 2], [1, 2]),
            ([1, "a"], ["1", "a"]),
            ({"nested": 1}, "{'nested': 1}"),
        ],
    )
    def test_values_become_what_the_sdk_accepts(self, value: Any, expected: Any) -> None:
        assert telemetry._clean({"k": value})["k"] == expected

    def test_none_is_dropped_rather_than_sent(self) -> None:
        assert telemetry._clean({"k": None}) == {}


class TestMetrics:
    def test_a_stage_duration_is_recorded_by_stage_and_outcome(self, otel: Captured) -> None:
        telemetry.record_stage("terrain", 0.4, ok=True)
        telemetry.record_stage("terrain", 0.2, ok=False)
        points = otel.points("contour.stage.duration")
        by_outcome = {p.attributes["contour.outcome"]: p for p in points}
        assert by_outcome["ok"].count == 1 and by_outcome["ok"].sum == pytest.approx(0.4)
        assert by_outcome["error"].attributes["contour.stage"] == "terrain"

    def test_latency_buckets_reach_an_enrichment_deadline(self, otel: Captured) -> None:
        """Default SDK buckets stop at 10 s; a 20 s enrichment needs its own."""
        telemetry.record_layer("rainfall", 19.0, "ok")
        (point,) = otel.points("contour.enrichment.layer.duration")
        assert 20.0 in point.explicit_bounds

    def test_cache_lookups_count_hits_and_misses(self, otel: Captured) -> None:
        telemetry.record_cache("dem", hit=True)
        telemetry.record_cache("dem", hit=True)
        telemetry.record_cache("dem", hit=False)
        points = otel.points("contour.cache.lookups")
        assert {p.attributes["contour.result"]: p.value for p in points} == {"hit": 2, "miss": 1}

    def test_process_memory_is_observed(self, otel: Captured) -> None:
        (point,) = otel.points("process.memory.usage")
        assert point.value > 10 * 1024 * 1024

    def test_the_slots_gauge_reports_running_waiting_and_limit(self, otel: Captured) -> None:
        from app.services import capacity

        capacity.reset_slots()
        states = {p.attributes["contour.state"] for p in otel.points("contour.analysis.slots")}
        assert states == {"running", "waiting", "limit"}

    def test_a_busy_refusal_is_counted_by_its_reason(self, otel: Captured) -> None:
        from app.api.v1.slots import _busy

        _busy("full", reason="queue_full")
        (point,) = otel.points("contour.requests.rejected")
        assert point.attributes["contour.reason"] == "queue_full" and point.value == 1


class TestLogs:
    def test_a_structlog_event_is_exported_inside_its_trace(self, otel: Captured) -> None:
        configure_logging("INFO", json_output=True)
        with telemetry.span("request") as current:
            get_logger("services.test").info("analysis job failed", job_id="j1", cells=3)
        (record,) = [r for r in otel.logs() if r.body == "analysis job failed"]
        assert record.trace_id == current.get_span_context().trace_id
        assert record.attributes["job_id"] == "j1"
        assert record.attributes["cells"] == 3
        assert record.attributes["logger_name"] == "services.test"
        assert record.severity_text == "INFO"

    def test_a_secret_is_masked_before_it_is_exported(self, otel: Captured) -> None:
        configure_logging("INFO", json_output=True)
        get_logger("services.test").info("calling provider", api_key="s3cr3t")
        (record,) = [r for r in otel.logs() if r.body == "calling provider"]
        assert record.attributes["api_key"] != "s3cr3t"

    def test_a_stdlib_record_is_exported_too(self, otel: Captured) -> None:
        configure_logging("INFO", json_output=True)
        logging.getLogger("app.providers.rainfall.ensemble").warning("source %s failed", "x")
        assert any(r.body == "source x failed" for r in otel.logs())

    def test_the_sdks_own_records_are_not_fed_back_into_it(self, otel: Captured) -> None:
        configure_logging("INFO", json_output=True)
        logging.getLogger("opentelemetry.exporter.otlp").warning("export failed")
        assert not any(r.body == "export failed" for r in otel.logs())


class TestResource:
    def test_the_environment_wins_over_the_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_SERVICE_NAME", "contour-api-lab")
        monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.instance.id=sys2")
        telemetry.reset_for_tests()
        try:
            captured = start_capture()
            with telemetry.span("probe"):
                pass
            attrs = captured.span("probe").resource.attributes
            assert attrs["service.name"] == "contour-api-lab"
            assert attrs["service.instance.id"] == "sys2"
            assert attrs["service.namespace"] == "contour"
        finally:
            telemetry.reset_for_tests()
