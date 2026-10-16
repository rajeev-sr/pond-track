/**
 * Browser tracing, joined to the server's (OpenTelemetry).
 *
 * Off unless the bundle is built with VITE_OTEL_TRACES_URL, the collector's
 * OTLP/HTTP endpoint. With it, every API call carries a W3C `traceparent`
 * header, so the server's spans for that request -- through the gateway, on
 * whichever instance served it, down to the SQL and the provider calls -- join
 * the trace the browser started. And one Run, from the click to the last map
 * overlay, is one trace.
 *
 * This file is only the OpenTelemetry API: a few kilobytes that record nothing
 * until an SDK is registered. The SDK is in ./telemetry-sdk, fetched only when
 * tracing is switched on, so the lab's bundle pays nothing for it.
 */
import {
  type Attributes,
  type Context,
  type Span,
  SpanStatusCode,
  context,
  trace,
} from "@opentelemetry/api";

const tracer = trace.getTracer("contour-web");

/** The context of the user action in progress, if any. */
let action: Context | undefined;

/** Load and register the SDK, if this build was given somewhere to export to. */
export function startTelemetry(): void {
  const url = import.meta.env.VITE_OTEL_TRACES_URL;
  if (!url) return;
  import("./telemetry-sdk")
    .then((sdk) => sdk.install(url))
    .catch((err) => console.warn("tracing unavailable", err));
}

/** Open the span for one user action. Requests made until `endAction` join it. */
export function beginAction(name: string, attributes: Attributes): Span {
  const span = tracer.startSpan(name, { attributes });
  action = trace.setSpan(context.active(), span);
  return span;
}

export function endAction(span: Span, error?: unknown): void {
  if (error instanceof Error && error.name !== "AbortError") {
    span.recordException(error);
    span.setStatus({ code: SpanStatusCode.ERROR, message: error.message });
  }
  span.end();
  if (action && trace.getSpan(action) === span) action = undefined;
}

/**
 * Run `send` inside the current action, so the fetch it starts is that action's
 * child.
 *
 * Explicitly, because a browser has no async context of its own: OpenTelemetry's
 * context is lost at the first `await` (zone.js would carry it, at ~100 kB). The
 * fetch instrumentation reads the context synchronously as `fetch` is called --
 * so re-entering the action's context around that call is enough.
 */
export function withinAction<T>(send: () => T): T {
  return action ? context.with(action, send) : send();
}
