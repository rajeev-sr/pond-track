/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The collector's OTLP/HTTP traces endpoint, e.g. http://localhost:4318/v1/traces.
   *  Unset (the default, and on the lab): the browser records no traces. */
  readonly VITE_OTEL_TRACES_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
