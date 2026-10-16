/**
 * The OpenTelemetry web SDK, loaded only when tracing is on (see ./telemetry).
 *
 * Spans go in batches to the collector, the same one the API instances export
 * to; the fetch instrumentation creates a client span per API call and puts its
 * trace context in the `traceparent` header the server continues from.
 */
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor, WebTracerProvider } from "@opentelemetry/sdk-trace-web";

export function install(url: string): void {
  const provider = new WebTracerProvider({
    resource: resourceFromAttributes({
      "service.name": "contour-web",
      "service.namespace": "contour",
      "service.version": "0.1.0",
    }),
    spanProcessors: [
      new BatchSpanProcessor(new OTLPTraceExporter({ url }), { scheduledDelayMillis: 1000 }),
    ],
  });
  provider.register();

  registerInstrumentations({
    instrumentations: [
      new FetchInstrumentation({
        // The exporter's own requests, and the keep-warm probe every 25 s.
        ignoreUrls: [/\/v1\/traces$/, /\/api\/v1\/health$/],
        clearTimingResources: true,
        // Which instance the gateway chose: the server spans say it too, as
        // service.instance.id, and the two agreeing is affinity working.
        applyCustomAttributesOnSpan: (span, _request, result) => {
          if (result instanceof Response) {
            const upstream = result.headers.get("X-Contour-Upstream");
            if (upstream) span.setAttribute("contour.upstream", upstream);
          }
        },
      }),
    ],
  });
}
