import type {
  AnalyzeOptions,
  Bbox,
  ContourAnalysis,
  DelineatedCatchment,
  JobStart,
  JobStatus,
  LandAvailability,
  Problem,
  StreamsResponse,
  TerrainOverlay,
  VillageBoundary,
  VillageSearchResult,
} from "./types";

const BASE = "/api/v1";

/**
 * Which browser this is, sent with every API call.
 *
 * On the lab deployment a gateway spreads users over four API instances, and a
 * user's follow-up calls -- streams, click-to-delineate, export -- must reach
 * the instance holding their terrain. Hashing on the client IP would put every
 * grader on one lab subnet onto one instance (nginx's `ip_hash` keys on the
 * first three octets); this id keys on the browser instead.
 *
 * `getRandomValues`, not `randomUUID`: the lab URL is plain HTTP, and
 * `randomUUID` exists only in secure contexts.
 */
export const CLIENT_ID: string = (() => {
  const make = () =>
    Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
  try {
    const saved = localStorage.getItem("contour-client");
    if (saved && /^[0-9a-f]{24}$/.test(saved)) return saved;
    const id = make();
    localStorage.setItem("contour-client", id);
    return id;
  } catch {
    return make(); // storage blocked: a per-tab id still keeps affinity
  }
})();

/** `fetch` with the client id attached. Every API call goes through this. */
function api(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Client-Id", CLIENT_ID);
  return fetch(url, { ...init, headers });
}

/** A request that got no answer at all: the connection failed, not the server. */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

/** Called before a request is sent again, so the page can say why it waits. */
export type OnRetry = (attempt: number, tries: number) => void;

function sleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * `api` with a deadline, sent again on a fresh connection when nothing answers.
 *
 * From the campus Wi-Fi about half of all new connections to the lab address
 * hang instead of failing, and a browser waits about 90 s on one before giving
 * up: with Run pressed the page showed nothing at all. A request with no answer
 * by its deadline is abandoned and sent again. Only a missing answer is retried
 * -- an HTTP error is an answer -- and a request the caller cancelled is not.
 */
async function request(
  url: string,
  init: RequestInit,
  { timeoutMs, tries, onRetry }: { timeoutMs: number; tries: number; onRetry?: OnRetry },
): Promise<Response> {
  const outer = init.signal;
  for (let attempt = 1; ; attempt++) {
    if (outer?.aborted) throw new DOMException("aborted", "AbortError");
    const controller = new AbortController();
    const stop = () => controller.abort();
    outer?.addEventListener("abort", stop, { once: true });
    const timer = setTimeout(stop, timeoutMs);
    try {
      return await api(url, { ...init, signal: controller.signal });
    } catch (err) {
      if (outer?.aborted) throw err;
      if (attempt >= tries) {
        throw new NetworkError(
          `The server did not answer: the network between this browser and the server ` +
            `dropped the connection ${tries} times in a row. Check the connection and press ` +
            `Run again.`,
        );
      }
      onRetry?.(attempt + 1, tries);
      await sleep(Math.min(1000 * attempt, 3000), outer);
    } finally {
      clearTimeout(timer);
      outer?.removeEventListener("abort", stop);
    }
  }
}

/** A one-off key per job start, so a start sent twice is recognised as one. */
function startKey(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

/** Deadlines, and attempts before giving up. A start answers at once; a contour
 *  upload is up to 50 MB; a result can be a few megabytes. */
const START_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 120_000;
const POLL_TIMEOUT_MS = 10_000;
const RESULT_TIMEOUT_MS = 60_000;
const START_TRIES = 4;
const POLL_TRIES = 6;

/** How often a page in view touches the server, below every idle timeout on the
 *  way (the gateway's two minutes, a browser's own). */
const KEEP_WARM_MS = 25_000;

/**
 * Keep a connection to the server open while the page is in view.
 *
 * On the campus network it is new connections that hang; one already open keeps
 * working. A small request every 25 s means the next Run reuses an open
 * connection instead of opening one. Returns the function that stops it.
 */
export function keepConnectionWarm(): () => void {
  const ping = () => {
    if (document.visibilityState !== "visible") return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8_000);
    api(`${BASE}/health`, { signal: controller.signal, cache: "no-store" })
      .then((r) => r.arrayBuffer()) // read it, so the connection goes back to the pool
      .catch(() => undefined)
      .finally(() => clearTimeout(timer));
  };
  const id = window.setInterval(ping, KEEP_WARM_MS);
  return () => window.clearInterval(id);
}

/** A plain link (a download) cannot carry a header, so it carries the id. */
export function withClient(url: string): string {
  return `${url}${url.includes("?") ? "&" : "?"}client=${CLIENT_ID}`;
}

/** An API error carrying the server's problem details, so the UI can show the
 *  actual reason rather than "request failed". */
export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.detail || problem.title);
    this.name = "ApiError";
  }

  /** 422 means the upload succeeded but the file's contents cannot be analysed
   *  — a different message to the user than a malformed request. */
  get isUnanswerable(): boolean {
    return this.problem.status === 422;
  }
}

async function toProblem(response: Response): Promise<Problem> {
  try {
    const body = (await response.json()) as Problem;
    if (body && typeof body.detail === "string") return body;
  } catch {
    /* fall through to a synthetic problem below */
  }
  return {
    type: "/errors/unknown",
    title: response.statusText || "Request failed",
    status: response.status,
    detail: `The server returned ${response.status} with no problem details.`,
  };
}

export async function analyzeContour(
  file: File,
  options: AnalyzeOptions,
  signal?: AbortSignal,
): Promise<ContourAnalysis> {
  const form = new FormData();
  form.append("file", file);
  form.append("max_sites", String(options.maxSites));
  form.append("max_slope_pct", String(options.maxSlopePct));
  form.append("enrich", String(options.enrich));
  form.append("include_contours", String(options.includeContours));
  if (options.cellSizeM !== null)
    form.append("cell_size_m", String(options.cellSizeM));

  const response = await api(`${BASE}/analyzeContour`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as ContourAnalysis;
}

export async function health(): Promise<{ status: string; version: string }> {
  const response = await api(`${BASE}/health`);
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as { status: string; version: string };
}

let villageSearchProbe: Promise<boolean> | null = null;

/** Whether this server can search villages at all -- the register lives in a
 *  database that the lab systems do not all have. Asked once per page load.
 *  A server that cannot say is treated as not having it: offering a search
 *  that answers with an error is worse than not offering it. */
export function villageSearchAvailable(): Promise<boolean> {
  // /health/features, not /health/ready: readiness answers 503 whenever the
  // database is down, and the browser logs every 503 as an error.
  villageSearchProbe ??= api(`${BASE}/health/features`)
    .then((r) => (r.ok ? r.json() : null))
    .then((body: { village_search?: { available?: boolean } } | null) => {
      return body?.village_search?.available === true;
    })
    .catch(() => false);
  return villageSearchProbe;
}

/** Search villages by name. Latin or Devanagari; spelling need not be exact.
 *
 *  `signal` is not optional in practice: the caller debounces keystrokes and
 *  aborts the previous request, or a slow response for `kut` can land after the
 *  fast one for `kutela` and overwrite it.
 */
export async function searchVillages(
  query: string,
  options: { district?: string; state?: string; limit?: number },
  signal?: AbortSignal,
): Promise<VillageSearchResult> {
  const params = new URLSearchParams({ q: query });
  if (options.state) params.set("state", options.state);
  if (options.district) params.set("district", options.district);
  params.set("limit", String(options.limit ?? 8));

  const response = await api(`${BASE}/villages/search?${params}`, { signal });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as VillageSearchResult;
}

/** The best available boundary for a village, labelled with what it outlines. */
export async function fetchVillageBoundary(
  villageId: string,
  signal?: AbortSignal,
): Promise<VillageBoundary> {
  const response = await api(`${BASE}/villages/${villageId}/boundary`, {
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as VillageBoundary;
}

/** Slope and shaded relief for a DEM the API already holds, as images.
 *
 *  One PNG per layer with the four corners it belongs at -- no tile server, so
 *  it works on a deployment that runs nothing but the API. A failure means no
 *  terrain layers, never a failed analysis.
 */
export async function fetchOverlays(
  demId: string,
  signal?: AbortSignal,
): Promise<TerrainOverlay[]> {
  const response = await api(`${BASE}/terrain/${demId}/overlays`, { signal });
  if (!response.ok) throw new ApiError(await toProblem(response));
  const { overlays } = (await response.json()) as { overlays: TerrainOverlay[] };
  // The map fetches these images itself, with no header of ours, so the client
  // id rides in the URL -- or the gateway could send the request to an instance
  // that does not hold this terrain.
  return overlays.map((o) => ({ ...o, url: withClient(o.url) }));
}

/** The drainage network for a DEM, with Strahler order per reach.
 *
 *  Optionally restricted to the catchment above a pour point, which is also what
 *  makes the reported drainage density meaningful.
 */
export async function fetchStreams(
  demId: string,
  options: { thresholdHa?: number; lon?: number; lat?: number },
  signal?: AbortSignal,
): Promise<StreamsResponse> {
  const form = new FormData();
  form.set("dem_id", demId);
  if (options.thresholdHa != null)
    form.set("threshold_ha", String(options.thresholdHa));
  if (options.lon != null && options.lat != null) {
    form.set("lon", String(options.lon));
    form.set("lat", String(options.lat));
  }

  const response = await api(`${BASE}/hydrology/streams`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as StreamsResponse;
}

/** Delineate the catchment above an arbitrary point.
 *
 *  The interactive counterpart to what the analysis does at its ranked sites.
 *  Snapping is on by default because a click a few metres off the channel lands
 *  on a hillside cell whose catchment is a few hectares rather than a few
 *  hundred — and the result looks entirely plausible.
 */
export async function delineateCatchment(
  demId: string,
  lon: number,
  lat: number,
  options: { snapRadiusM?: number } = {},
  signal?: AbortSignal,
): Promise<DelineatedCatchment> {
  const form = new FormData();
  form.set("dem_id", demId);
  form.set("lon", String(lon));
  form.set("lat", String(lat));
  if (options.snapRadiusM != null)
    form.set("snap_radius_m", String(options.snapRadiusM));

  const response = await api(`${BASE}/hydrology/catchment`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as DelineatedCatchment;
}

/** Parcels a pond could actually be dug on (FR-3).
 *
 *  Slower than the other calls on a cold cache: it reads WorldCover and asks
 *  Overpass for the window. Both degrade rather than fail, so a response with a
 *  non-empty `unavailable` is still usable.
 */
export async function fetchLandAvailability(
  demId: string,
  options: {
    maxSlopePct?: number;
    minAreaM2?: number;
    allowCropland?: boolean;
    useOsm?: boolean;
  },
  signal?: AbortSignal,
): Promise<LandAvailability> {
  const form = new FormData();
  form.set("dem_id", demId);
  if (options.maxSlopePct != null)
    form.set("max_slope_pct", String(options.maxSlopePct));
  if (options.minAreaM2 != null)
    form.set("min_area_m2", String(options.minAreaM2));
  if (options.allowCropland != null)
    form.set("allow_cropland", String(options.allowCropland));
  if (options.useOsm != null) form.set("use_osm", String(options.useOsm));

  const response = await api(`${BASE}/land/available`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as LandAvailability;
}

/** How long to wait between status polls.
 *
 *  One second while the bar is moving is responsive without being wasteful; a
 *  cold analysis is around 25 seconds, so this is roughly 25 requests, each of
 *  which is a Redis read.
 */
const POLL_INTERVAL_MS = 1000;

/**
 * Run an analysis as a background job, reporting progress as it goes.
 *
 * The synchronous `analyzeContour` above is still the right call from a script.
 * This exists because a browser cannot show anything useful during a 25-second
 * request: the job endpoint reports which step is running and a percentage
 * weighted by each step's measured cost, so the bar tracks elapsed time rather
 * than step count.
 */
export async function analyzeContourAsJob(
  file: File,
  options: AnalyzeOptions,
  onProgress: (status: JobStatus) => void,
  signal?: AbortSignal,
  onRetry?: OnRetry,
): Promise<ContourAnalysis> {
  const form = new FormData();
  form.append("file", file);
  form.append("max_sites", String(options.maxSites));
  form.append("max_slope_pct", String(options.maxSlopePct));
  form.append("enrich", String(options.enrich));
  form.append("include_contours", String(options.includeContours));
  if (options.cellSizeM !== null)
    form.append("cell_size_m", String(options.cellSizeM));

  const accepted = await request(
    `${BASE}/analysis`,
    { method: "POST", body: form, headers: { "Idempotency-Key": startKey() }, signal },
    { timeoutMs: UPLOAD_TIMEOUT_MS, tries: START_TRIES, onRetry },
  );
  return followJob(accepted, onProgress, signal, onRetry);
}

/**
 * The same, for a rectangle drawn on the map: no file, just its corners.
 *
 * The server checks the box before accepting the job -- inverted, too small,
 * over the cap -- so those come back at once as a 400 or 413 rather than as a
 * job that fails a second later.
 */
export async function analyzeAreaAsJob(
  bbox: Bbox,
  options: AnalyzeOptions,
  onProgress: (status: JobStatus) => void,
  signal?: AbortSignal,
  onRetry?: OnRetry,
): Promise<ContourAnalysis> {
  const accepted = await request(
    `${BASE}/analysis/area`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": startKey() },
      body: JSON.stringify({
        bbox,
        max_sites: options.maxSites,
        max_slope_pct: options.maxSlopePct,
        enrich: options.enrich,
        include_contours: options.includeContours,
      }),
      signal,
    },
    { timeoutMs: START_TIMEOUT_MS, tries: START_TRIES, onRetry },
  );
  return followJob(accepted, onProgress, signal, onRetry);
}

/** Poll an accepted job to the end and return its result. */
async function followJob(
  accepted: Response,
  onProgress: (status: JobStatus) => void,
  signal?: AbortSignal,
  onRetry?: OnRetry,
): Promise<ContourAnalysis> {
  if (!accepted.ok) throw new ApiError(await toProblem(accepted));
  const start = (await accepted.json()) as JobStart;

  // Poll until terminal. No timeout of its own: the caller's AbortSignal is the
  // way out, so a slow provider does not silently abandon a job that is still
  // making progress.
  for (;;) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const response = await request(
      `${BASE}/analysis/${start.job_id}/status`,
      { signal },
      { timeoutMs: POLL_TIMEOUT_MS, tries: POLL_TRIES, onRetry },
    );
    if (!response.ok) throw new ApiError(await toProblem(response));
    const status = (await response.json()) as JobStatus;
    onProgress(status);

    if (status.state === "failed" || status.state === "cancelled") {
      throw new ApiError({
        type: status.error?.type ?? "/errors/analysis-failed",
        title: status.error?.title ?? "Analysis failed",
        status: 422,
        detail:
          status.error?.detail ??
          `The analysis ended as ${status.state} without a reason being recorded.`,
        // Carried through so the overlay can quote it. A reason with no id
        // cannot be traced back to the log line that has the traceback.
        trace_id: status.error?.trace_id,
      });
    }
    // `partial` is a success: the core steps ran and the result is usable, with
    // warnings saying which layer was lost.
    if (status.state === "done" || status.state === "partial") break;

    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }

  const finished = await request(
    `${BASE}/analysis/${start.job_id}/result`,
    { signal },
    { timeoutMs: RESULT_TIMEOUT_MS, tries: START_TRIES, onRetry },
  );
  if (!finished.ok) throw new ApiError(await toProblem(finished));
  const body = (await finished.json()) as { result: ContourAnalysis };
  // Older servers did not stamp the job id onto the result; the export link
  // needs it either way.
  return { ...body.result, job_id: body.result.job_id ?? start.job_id };
}
