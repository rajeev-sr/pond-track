import type {
  AnalyzeOptions,
  ContourAnalysis,
  DelineatedCatchment,
  JobStart,
  JobStatus,
  LandAvailability,
  Problem,
  StreamsResponse,
  TerrainDerivatives,
  VillageBoundary,
  VillageSearchResult,
} from "./types";

const BASE = "/api/v1";

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

  const response = await fetch(`${BASE}/analyzeContour`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as ContourAnalysis;
}

export async function health(): Promise<{ status: string; version: string }> {
  const response = await fetch(`${BASE}/health`);
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as { status: string; version: string };
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

  const response = await fetch(`${BASE}/villages/search?${params}`, { signal });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as VillageSearchResult;
}

/** The best available boundary for a village, labelled with what it outlines. */
export async function fetchVillageBoundary(
  villageId: string,
  signal?: AbortSignal,
): Promise<VillageBoundary> {
  const response = await fetch(`${BASE}/villages/${villageId}/boundary`, {
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as VillageBoundary;
}

/** Ask for slope and hillshade tiles for a DEM already held by the API.
 *
 *  `demId` comes from the analysis response, so this needs no second upload.
 *  Requires the tiles service to be running; the caller should treat a failure
 *  as "no terrain layers" rather than a failed analysis.
 */
export async function fetchDerivatives(
  demId: string,
  options: { products?: string; zFactor?: number },
  signal?: AbortSignal,
): Promise<TerrainDerivatives> {
  const form = new FormData();
  form.set("dem_id", demId);
  if (options.products) form.set("products", options.products);
  if (options.zFactor != null)
    form.set("hillshade_z_factor", String(options.zFactor));

  const response = await fetch(`${BASE}/terrain/derivatives`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw new ApiError(await toProblem(response));
  return (await response.json()) as TerrainDerivatives;
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

  const response = await fetch(`${BASE}/hydrology/streams`, {
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

  const response = await fetch(`${BASE}/hydrology/catchment`, {
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

  const response = await fetch(`${BASE}/land/available`, {
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
): Promise<ContourAnalysis> {
  const form = new FormData();
  form.append("file", file);
  form.append("max_sites", String(options.maxSites));
  form.append("max_slope_pct", String(options.maxSlopePct));
  form.append("enrich", String(options.enrich));
  form.append("include_contours", String(options.includeContours));
  if (options.cellSizeM !== null)
    form.append("cell_size_m", String(options.cellSizeM));

  const accepted = await fetch(`${BASE}/analysis`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!accepted.ok) throw new ApiError(await toProblem(accepted));
  const start = (await accepted.json()) as JobStart;

  // Poll until terminal. No timeout of its own: the caller's AbortSignal is the
  // way out, so a slow provider does not silently abandon a job that is still
  // making progress.
  for (;;) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const response = await fetch(`${BASE}/analysis/${start.job_id}/status`, {
      signal,
    });
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

  const finished = await fetch(`${BASE}/analysis/${start.job_id}/result`, {
    signal,
  });
  if (!finished.ok) throw new ApiError(await toProblem(finished));
  const body = (await finished.json()) as { result: ContourAnalysis };
  return body.result;
}
