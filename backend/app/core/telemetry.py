"""OpenTelemetry: traces, metrics and logs, exported over OTLP.

Inert unless a collector is named. With `OTEL_EXPORTER_OTLP_ENDPOINT` unset --
the lab systems, the test suite, a laptop without the observability stack --
`setup()` installs nothing and every helper here is a no-op that costs a branch.
Set it (for example `http://otel-collector:4318`) and this process exports:

* **traces** -- a span per HTTP request (FastAPI), per outgoing provider call
  (httpx), per SQL statement (SQLAlchemy), and the spans this codebase opens
  itself: the analysis job, its wait for a slot, each pipeline stage, each
  enrichment layer and each rainfall source;
* **metrics** -- request rate and latency, stage, analysis and job durations,
  queue depth, 503 refusals, cache hits, enrichment tiers and process memory;
* **logs** -- every structlog event and stdlib record, stamped with the trace and
  span it was written in, so a log line leads to its trace and back.

Everything else is the standard OpenTelemetry environment -- OTEL_SERVICE_NAME,
OTEL_RESOURCE_ATTRIBUTES, OTEL_METRIC_EXPORT_INTERVAL, OTEL_TRACES_SAMPLER --
read by the SDK itself, so operating it needs nothing Contour-specific.

Observability must never take the service down with it. A missing package, an
unreachable collector or a failed export degrades to *no telemetry*, never to a
failed request: exporters run on background threads and drop what they cannot
send.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, TypeVar

try:
    from opentelemetry import trace as otel_trace
except ImportError:  # pragma: no cover - a host venv that predates the dependency
    otel_trace = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

#: Instrumentation scope for every span, metric and log this codebase emits.
SCOPE = "contour"
#: Matches the FastAPI app's `version`.
SERVICE_VERSION = "0.1.0"
DEFAULT_SERVICE_NAME = "contour-api"

#: Not traced: the liveness probe (Docker's healthcheck and the page's
#: keep-warm request hit it every few seconds) and static assets. They are still
#: counted in the request metrics, which is where their volume belongs.
TRACE_EXCLUDED_URLS = r"api/v1/health$,/assets/,favicon"

#: Request headers recorded on the server span. `X-Client-Id` is the per-browser
#: id the gateway hashes on, so it answers "which instance served this user?".
CAPTURED_HEADERS = ["x-client-id", "x-request-id"]

#: Seconds. Stage and request latency here span four orders of magnitude, from a
#: 5 ms status poll to a 20 s enrichment, so the SDK's default buckets (tuned for
#: milliseconds) would put every analysis in the last one.
LATENCY_BUCKETS_S: Sequence[float] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0,
)  # fmt: skip

T = TypeVar("T")


@dataclass
class _Instruments:
    http_duration: Any
    http_active: Any
    stage_duration: Any
    analysis_duration: Any
    queue_wait: Any
    jobs: Any
    rejected: Any
    layer_duration: Any
    tiers: Any
    rainfall_sources: Any
    cache_lookups: Any


_lock = threading.Lock()
_enabled = False
_tracer: Any = None
_instruments: _Instruments | None = None
_meter: Any = None
_otel_logger: Any = None
_log_handler: logging.Handler | None = None
_providers: list[Any] = []
_gauges: list[tuple[str, str, str, Callable[[], Iterable[tuple[float, dict[str, Any]]]]]] = []


# ── configuration ────────────────────────────────────────────────────────────
def configured_endpoint() -> str | None:
    """The collector this process should export to, or None to stay inert."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    return endpoint or None


def enabled() -> bool:
    return _enabled


def status() -> dict[str, Any]:
    """For the startup report: whether telemetry is on, and where it goes."""
    return {"enabled": _enabled, "endpoint": configured_endpoint() if _enabled else None}


def setup(
    *,
    span_exporter: Any = None,
    metric_reader: Any = None,
    log_exporter: Any = None,
) -> bool:
    """Install the SDK and library instrumentation, once per process.

    Returns whether telemetry is on. With no collector configured and nothing
    injected it installs nothing and returns False. Tests inject in-memory
    exporters instead of naming a collector.
    """
    global _enabled
    with _lock:
        if _enabled:
            return True
        injected = any(x is not None for x in (span_exporter, metric_reader, log_exporter))
        if otel_trace is None or (configured_endpoint() is None and not injected):
            return False
        try:
            _install(span_exporter, metric_reader, log_exporter)
        except Exception as exc:  # telemetry is optional; the service is not
            log.warning("telemetry disabled: %s: %s", type(exc).__name__, exc)
            return False
        _enabled = True
    log.info("telemetry enabled, exporting to %s", configured_endpoint() or "in-memory")
    return True


def _resource() -> Any:
    """Who is emitting. The environment wins over the defaults here.

    `Resource.create` merges explicit attributes *over* the environment, so a
    default passed for a key the operator set would silently override them --
    `OTEL_SERVICE_NAME` would lose to "contour-api". Defaults are therefore only
    offered for keys the environment leaves unset.
    """
    from opentelemetry.sdk.resources import Resource

    from app.config import get_settings

    env_keys = {
        pair.split("=", 1)[0].strip()
        for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(",")
        if "=" in pair
    }
    if os.environ.get("OTEL_SERVICE_NAME"):
        env_keys.add("service.name")
    defaults = {
        "service.name": DEFAULT_SERVICE_NAME,
        "service.namespace": "contour",
        "service.version": SERVICE_VERSION,
        "service.instance.id": socket.gethostname(),
        "deployment.environment.name": get_settings().ENV,
    }
    return Resource.create({k: v for k, v in defaults.items() if k not in env_keys})


def _install(span_exporter: Any, metric_reader: Any, log_exporter: Any) -> None:
    global _tracer, _instruments, _meter, _otel_logger
    from opentelemetry import _logs
    from opentelemetry import metrics as otel_metrics
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    resource = _resource()

    # Traces. Batch export: spans are queued and sent from a background thread,
    # so a slow collector costs the request nothing.
    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    otel_trace.set_tracer_provider(tracer_provider)

    # Metrics. Cumulative temporality (the OTLP default), which is what
    # Prometheus expects; the interval comes from OTEL_METRIC_EXPORT_INTERVAL.
    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    otel_metrics.set_meter_provider(meter_provider)

    # Logs.
    logger_provider = LoggerProvider(resource=resource)
    if log_exporter is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    else:
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    _logs.set_logger_provider(logger_provider)

    _providers[:] = [tracer_provider, meter_provider, logger_provider]
    _tracer = tracer_provider.get_tracer(SCOPE, SERVICE_VERSION)
    _meter = meter_provider.get_meter(SCOPE, SERVICE_VERSION)
    _otel_logger = logger_provider.get_logger(SCOPE, SERVICE_VERSION)
    _instruments = _create_instruments(_meter)
    _create_gauge(
        "process.memory.usage",
        "Resident memory of this process. A lab system has 512 MB in all.",
        "By",
        _observe_memory,
    )
    for name, description, unit, observe in _gauges:
        _create_gauge(name, description, unit, observe)

    # Outgoing HTTP (every provider goes through httpx) and thread hand-offs:
    # enrichment and the rainfall ensemble fan out to thread pools, and without
    # this each thread would start a trace of its own.
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.threading import ThreadingInstrumentor

    HTTPXClientInstrumentor().instrument()
    ThreadingInstrumentor().instrument()


def _create_instruments(meter: Any) -> _Instruments:
    def histogram(name: str, description: str) -> Any:
        return meter.create_histogram(
            name, unit="s", description=description,
            explicit_bucket_boundaries_advisory=LATENCY_BUCKETS_S,
        )  # fmt: skip

    return _Instruments(
        http_duration=histogram(
            "http.server.request.duration",
            "Time from the request arriving to the last byte of the response.",
        ),
        http_active=meter.create_up_down_counter(
            "http.server.active_requests", unit="{request}", description="Requests in flight."
        ),
        stage_duration=histogram("contour.stage.duration", "One pipeline stage of an analysis."),
        analysis_duration=histogram(
            "contour.analysis.duration", "One whole analysis, from terrain to catchments."
        ),
        queue_wait=histogram(
            "contour.job.queue_wait", "How long a job waited for an analysis slot."
        ),
        jobs=meter.create_counter(
            "contour.jobs", unit="{job}", description="Analysis jobs by terminal state."
        ),
        rejected=meter.create_counter(
            "contour.requests.rejected",
            unit="{request}",
            description="Requests answered 503 Busy, by which limit refused them.",
        ),
        layer_duration=histogram(
            "contour.enrichment.layer.duration", "One enrichment layer (soil, rain, ...)."
        ),
        tiers=meter.create_counter(
            "contour.enrichment.tier",
            unit="{analysis}",
            description="Analyses by data tier: full, no_soil_lulc or terrain_only.",
        ),
        rainfall_sources=meter.create_counter(
            "contour.rainfall.source",
            unit="{request}",
            description="Rainfall source outcomes: ok, failed, or no answer in budget.",
        ),
        cache_lookups=meter.create_counter(
            "contour.cache.lookups", unit="{lookup}", description="Cache lookups by hit or miss."
        ),
    )


def _create_gauge(
    name: str,
    description: str,
    unit: str,
    observe: Callable[[], Iterable[tuple[float, dict[str, Any]]]],
) -> None:
    from opentelemetry.metrics import CallbackOptions, Observation

    def callback(_options: CallbackOptions) -> Iterable[Observation]:
        try:
            return [Observation(value, attrs) for value, attrs in observe()]
        except Exception:  # a broken gauge must not break the export cycle
            return []

    _meter.create_observable_gauge(name, callbacks=[callback], unit=unit, description=description)


def register_gauge(
    name: str,
    description: str,
    unit: str,
    observe: Callable[[], Iterable[tuple[float, dict[str, Any]]]],
) -> None:
    """A value read at each export rather than recorded as it changes.

    Safe to call at import time, before `setup()`: the gauge is created when
    telemetry starts, or at once if it already has.
    """
    with _lock:
        _gauges.append((name, description, unit, observe))
        live = _meter is not None
    if live:
        _create_gauge(name, description, unit, observe)


def _observe_memory() -> list[tuple[float, dict[str, Any]]]:
    """RSS from /proc: the figure the kernel's OOM killer compares to the limit."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:  # noqa: PTH123
            resident_pages = int(fh.read().split()[1])
    except (OSError, IndexError, ValueError):
        return []
    return [(float(resident_pages * os.sysconf("SC_PAGE_SIZE")), {})]


# ── library instrumentation the app factory and engine need ──────────────────
def instrument_app(app: Any) -> None:
    """Trace every request, and time it to its last byte (see the middleware)."""
    if not _enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider

    app.add_middleware(RequestTelemetryMiddleware)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=TRACE_EXCLUDED_URLS,
        # The per-message `http send` / `http receive` child spans double the
        # span count of every request and say nothing a reader of this app needs.
        exclude_spans=["receive", "send"],
        http_capture_headers_server_request=CAPTURED_HEADERS,
        # Its request-duration histogram is measured to the end of the ASGI
        # call, which for a job start includes the whole background job: a 40 ms
        # 202 would be recorded as a 20 s request. RequestTelemetryMiddleware
        # records the same metric, measured to the last byte sent.
        meter_provider=NoOpMeterProvider(),
    )


def instrument_engine(engine: Any) -> None:
    """A span per SQL statement on this engine (the village register, the
    rainfall cache). Called where the engine is built, because the engine is
    created lazily -- after `setup()` -- by `app.db.session.get_engine`."""
    if not _enabled:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    try:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception as exc:  # pragma: no cover - instrumentation is optional
        log.warning("SQL tracing unavailable: %s", exc)


class RequestTelemetryMiddleware:
    """Request metrics measured to the last byte, and the server span ended there.

    Starlette runs a route's `BackgroundTasks` inside the same ASGI call, after
    the response has been sent -- and the job routes start their analysis that
    way. The OpenTelemetry ASGI middleware ends its span and records its duration
    when that call returns, so `POST /analysis/area` showed the whole 20 s job as
    the request's latency while the browser had its 202 in 40 ms. Ending the
    span when the final body chunk goes out makes the request span mean what the
    client experienced, and leaves the job as a child span that outlives it --
    which is what a background job is.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        inst = _instruments
        if scope.get("type") != "http" or inst is None:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET"))
        start = time.perf_counter()
        server_span = otel_trace.get_current_span()
        state: dict[str, Any] = {"status": 500, "done": False}
        inst.http_active.add(1, {"http.request.method": method})

        def finish() -> None:
            if state["done"]:
                return
            state["done"] = True
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            inst.http_duration.record(
                time.perf_counter() - start,
                {
                    "http.request.method": method,
                    "http.route": route,
                    "http.response.status_code": int(state["status"]),
                },
            )
            inst.http_active.add(-1, {"http.request.method": method})
            if server_span.is_recording():
                server_span.end()

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                state["status"] = message.get("status", 500)
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body"):
                finish()

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            finish()


# ── spans ────────────────────────────────────────────────────────────────────
class _NoSpan:
    """What `span()` yields with no OpenTelemetry API installed at all."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def set_attributes(self, _attributes: Mapping[str, Any]) -> None:
        return None

    def is_recording(self) -> bool:
        return False


_NO_SPAN = _NoSpan()


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
    """A child span of whatever is current, ended when the block exits.

    An exception leaving the block is recorded on the span and marks it as an
    error, then propagates unchanged.
    """
    if _tracer is None:
        yield otel_trace.INVALID_SPAN if otel_trace is not None else _NO_SPAN
        return
    with _tracer.start_as_current_span(name, attributes=_clean(attributes)) as current:
        yield current


def traced(name: str, fn: Callable[[], T], attributes: Mapping[str, Any] | None = None) -> T:
    """Run `fn` inside a span named `name`."""
    with span(name, attributes):
        return fn()


def annotate(**attributes: Any) -> None:
    """Add attributes to the current span, if one is recording."""
    if otel_trace is None or not attributes:
        return
    current = otel_trace.get_current_span()
    if current.is_recording():
        current.set_attributes(_clean(attributes))


def current_trace_id() -> str | None:
    """The W3C trace id of the current span -- the id Jaeger searches by."""
    if otel_trace is None:
        return None
    ctx = otel_trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def mark_problem(problem_type: str, status: int) -> None:
    """Tag the request span with the RFC 9457 problem it was answered with."""
    annotate(**{"contour.problem.type": problem_type, "contour.problem.status": status})


def record_exception(exc: BaseException) -> None:
    if otel_trace is None:
        return
    from opentelemetry.trace import Status, StatusCode

    current = otel_trace.get_current_span()
    if current.is_recording():
        current.record_exception(exc)
        current.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))


def _clean(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """OpenTelemetry attribute values: str, bool, int, float, or lists of one.

    Anything else (a dict, a numpy scalar, None) would be dropped by the SDK with
    a warning per call, so it is coerced here instead.
    """
    out: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            out[key] = value
        elif isinstance(value, list | tuple):
            items = [v if isinstance(v, str | bool | int | float) else str(v) for v in value]
            if items and all(type(v) is type(items[0]) for v in items):
                out[key] = items
            else:
                out[key] = [str(v) for v in items]
        elif hasattr(value, "item"):  # numpy scalar
            out[key] = value.item()
        else:
            out[key] = str(value)
    return out


# ── metrics ──────────────────────────────────────────────────────────────────
def record_stage(stage: str, seconds: float, *, ok: bool) -> None:
    if _instruments is not None:
        _instruments.stage_duration.record(
            seconds, {"contour.stage": stage, "contour.outcome": "ok" if ok else "error"}
        )


def record_analysis(kind: str, seconds: float, *, ok: bool) -> None:
    if _instruments is not None:
        _instruments.analysis_duration.record(
            seconds, {"contour.input": kind, "contour.outcome": "ok" if ok else "error"}
        )


def record_queue_wait(kind: str, seconds: float) -> None:
    if _instruments is not None:
        _instruments.queue_wait.record(seconds, {"contour.input": kind})


def record_job(kind: str, state: str) -> None:
    if _instruments is not None:
        _instruments.jobs.add(1, {"contour.input": kind, "contour.state": state})


def record_rejection(reason: str) -> None:
    if _instruments is not None:
        _instruments.rejected.add(1, {"contour.reason": reason})


def record_layer(layer: str, seconds: float, outcome: str) -> None:
    if _instruments is not None:
        _instruments.layer_duration.record(
            seconds, {"contour.layer": layer, "contour.outcome": outcome}
        )


def record_tier(tier: str) -> None:
    if _instruments is not None:
        _instruments.tiers.add(1, {"contour.tier": tier})


def record_rainfall_source(source: str, outcome: str) -> None:
    if _instruments is not None:
        _instruments.rainfall_sources.add(1, {"contour.source": source, "contour.outcome": outcome})


def record_cache(cache: str, *, hit: bool) -> None:
    if _instruments is not None:
        _instruments.cache_lookups.add(
            1, {"contour.cache": cache, "contour.result": "hit" if hit else "miss"}
        )


# ── logs ─────────────────────────────────────────────────────────────────────
def _severity(level: str) -> Any:
    from opentelemetry._logs import SeverityNumber

    return {
        "debug": SeverityNumber.DEBUG,
        "info": SeverityNumber.INFO,
        "warning": SeverityNumber.WARN,
        "warn": SeverityNumber.WARN,
        "error": SeverityNumber.ERROR,
        "exception": SeverityNumber.ERROR,
        "critical": SeverityNumber.FATAL,
    }.get(level, SeverityNumber.INFO)


#: structlog keys that are already a field of the OTel log record -- the trace
#: and span ids travel in the record's own trace context.
_RECORD_FIELDS = frozenset({"event", "level", "timestamp", "trace_id", "span_id"})


def structlog_processor(_logger: Any, method_name: str, event_dict: Any) -> Any:
    """A structlog processor: stamp the trace id, and send a copy over OTLP.

    The trace id is added to the console line too, so `docker logs` output can
    be matched to a trace even without the log backend. The event is returned
    unchanged otherwise: the console renderer after this one still prints it.
    """
    if otel_trace is None:
        return event_dict
    ctx = otel_trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    if _otel_logger is None:
        return event_dict
    try:
        level = str(event_dict.get("level", method_name)).lower()
        attributes = _clean({k: v for k, v in event_dict.items() if k not in _RECORD_FIELDS})
        if "exception" in attributes:
            attributes["exception.stacktrace"] = attributes.pop("exception")
        _otel_logger.emit(
            timestamp=time.time_ns(),
            severity_number=_severity(level),
            severity_text=level.upper(),
            body=str(event_dict.get("event", "")),
            attributes=attributes,
        )
    except Exception:  # pragma: no cover - a log export must never fail a log call
        pass
    return event_dict


def _not_from_opentelemetry(record: logging.LogRecord) -> bool:
    """Keep the SDK's own records (a failed export, say) out of the pipeline
    they describe: exporting "export failed" through the exporter that failed is
    a feedback loop for as long as the collector is down."""
    return not record.name.startswith("opentelemetry")


def attach_log_handler() -> None:
    """Send stdlib `logging` records (uvicorn excepted) to the log pipeline too.

    Called by `configure_logging` after it has set up the console handler:
    attaching earlier would give the root logger a handler, and
    `logging.basicConfig` then silently declines to add the console one.
    """
    global _log_handler
    if not _enabled or _log_handler is not None:
        return
    from opentelemetry.instrumentation.logging.handler import LoggingHandler

    handler = LoggingHandler(level=logging.INFO)
    handler.addFilter(_not_from_opentelemetry)
    logging.getLogger().addHandler(handler)
    _log_handler = handler


# ── lifecycle ────────────────────────────────────────────────────────────────
def shutdown() -> None:
    """Flush whatever is queued. Called on application shutdown."""
    for provider in list(_providers):
        with suppress(Exception):
            provider.force_flush()


def reset_for_tests() -> None:
    """Return the process to its pre-`setup()` state.

    OpenTelemetry lets a provider be set once per process; tests need several.
    This resets the API's private once-flags -- the same thing the upstream
    `opentelemetry-test-utils` helpers do -- and undoes the instrumentation.
    """
    global _enabled, _tracer, _instruments, _meter, _otel_logger, _log_handler
    if otel_trace is None:
        return
    from opentelemetry._logs import _internal as logs_internal
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.instrumentation.threading import ThreadingInstrumentor
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.util._once import Once

    instrumentors = (HTTPXClientInstrumentor(), ThreadingInstrumentor(), SQLAlchemyInstrumentor())
    for instrumentor in instrumentors:
        if getattr(instrumentor, "_is_instrumented_by_opentelemetry", False):
            instrumentor.uninstrument()
    for provider in list(_providers):
        with suppress(Exception):
            provider.shutdown()
    if _log_handler is not None:
        logging.getLogger().removeHandler(_log_handler)
    # FastAPI's instrumentation wraps BackgroundTask.__call__ once per process,
    # closing over the tracer current at that moment. Left in place, every later
    # app's background-task spans would go to this, the shut-down, provider.
    from starlette.background import BackgroundTask

    original_call = getattr(BackgroundTask, "_otel_original_call", None)
    if original_call is not None:
        BackgroundTask.__call__ = original_call  # type: ignore[method-assign]
        delattr(BackgroundTask, "_otel_original_call")

    otel_trace._TRACER_PROVIDER_SET_ONCE = Once()
    otel_trace._TRACER_PROVIDER = None
    otel_trace._PROXY_TRACER_PROVIDER = otel_trace.ProxyTracerProvider()
    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    metrics_internal._PROXY_METER_PROVIDER = metrics_internal._ProxyMeterProvider()
    logs_internal._LOGGER_PROVIDER_SET_ONCE = Once()
    logs_internal._LOGGER_PROVIDER = None
    logs_internal._PROXY_LOGGER_PROVIDER = logs_internal.ProxyLoggerProvider()

    with _lock:
        _enabled = False
        _tracer = _instruments = _meter = _otel_logger = _log_handler = None
        _providers.clear()
