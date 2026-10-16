"""In-memory OpenTelemetry pipelines, for tests that read what the app emits.

`telemetry.setup()` normally exports over OTLP to a collector. Given in-memory
exporters instead it installs the same providers and instrumentation, so a test
sees exactly the spans, metric points and log records a collector would.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from app.core import telemetry


@dataclass
class Captured:
    span_exporter: Any
    metric_reader: Any
    log_exporter: Any

    def spans(self) -> list[Any]:
        return list(self.span_exporter.get_finished_spans())

    def span(self, name: str) -> Any:
        found = [s for s in self.spans() if s.name == name]
        assert found, f"no span named {name!r}; have {sorted({s.name for s in self.spans()})}"
        return found[0]

    def logs(self) -> list[Any]:
        return [r.log_record for r in self.log_exporter.get_finished_logs()]

    def points(self, metric: str) -> list[Any]:
        """Every data point of one metric, from a fresh collection."""
        data = self.metric_reader.get_metrics_data()
        out: list[Any] = []
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for m in scope_metrics.metrics:
                    if m.name == metric:
                        out.extend(m.data.data_points)
        return out


def start_capture() -> Captured:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    captured = Captured(InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogRecordExporter())
    assert telemetry.setup(
        span_exporter=captured.span_exporter,
        metric_reader=captured.metric_reader,
        log_exporter=captured.log_exporter,
    )
    return captured


@pytest.fixture
def otel() -> Iterator[Captured]:
    telemetry.reset_for_tests()
    try:
        yield start_capture()
    finally:
        telemetry.reset_for_tests()
