import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  analyzeContourAsJob,
  delineateCatchment,
  fetchDerivatives,
  fetchLandAvailability,
  fetchStreams,
  fetchVillageBoundary,
} from "../api/client";
import type {
  AnalyzeOptions,
  ContourAnalysis,
  DelineatedCatchment,
  JobStatus,
  LandAvailability,
  Problem,
  StreamNetworkReport,
  StreamScope,
  StreamsResponse,
  TerrainLayer,
  VillageMatch,
} from "../api/types";
import type { BasemapId, LayerVisibility, VillageOutline } from "../components/MapView";

/**
 * All analysis state, lifted out of the workspace and into a provider.
 *
 * The reason is routing: the workspace, the brief and the method pages are now
 * separate routes, and a run must survive navigating away from the workspace and
 * back. Holding it in the workspace component would discard a two-minute
 * analysis the moment someone opened the method page to check a formula.
 *
 * It also lets the brief report the *current* run rather than figures typed into
 * the markup, which matters beyond tidiness: the assignment forbids results
 * specific to the sample sheet being baked into the implementation.
 */

const DEFAULT_OPTIONS: AnalyzeOptions = {
  maxSites: 5,
  cellSizeM: null,
  maxSlopePct: 8,
  enrich: true,
  includeContours: true,
};

const DEFAULT_LAYERS: LayerVisibility = {
  // Both terrain rasters off by default, which is the opposite of what it looks
  // like it should be. Satellite imagery is the default basemap and already
  // carries terrain texture; a hillshade over it mostly desaturates the photo
  // rather than revealing anything, and the composite reads muddier than either
  // layer alone. They earn their place on the street basemap, and as something
  // to consult deliberately -- so they are offered, not imposed.
  hillshade: false,
  slope: false,
  // On by default: the channels are the single most useful overlay for judging a
  // pond site, and unlike the rasters they read clearly over the basemap.
  streams: true,
  explored: true,
  parcels: true,
  pond: true,
  contours: true,
  catchment: true,
  sites: true,
  aoi: true,
  village: true,
};

export interface AnalysisState {
  analysis: ContourAnalysis | null;
  /** Trimmed to `shownSites`; what the map and the ranked list both read. */
  shownAnalysis: ContourAnalysis | null;
  options: AnalyzeOptions;
  setOptions: (o: AnalyzeOptions) => void;
  layers: LayerVisibility;
  setLayers: (l: LayerVisibility) => void;
  basemap: BasemapId;
  setBasemap: (b: BasemapId) => void;
  selectedRank: number | null;
  setSelectedRank: (r: number | null) => void;
  shownSites: number;
  setShownSites: (n: number) => void;
  terrain: TerrainLayer[];
  streams: GeoJSON.FeatureCollection | null;
  streamSummary: StreamNetworkReport | null;
  streamScope: StreamScope;
  loadingStreams: boolean;
  setStreamScope: (s: StreamScope) => void;
  explored: DelineatedCatchment | null;
  exploring: boolean;
  explore: (lon: number, lat: number) => void;
  clearExplored: () => void;
  land: LandAvailability | null;
  loadingLand: boolean;
  loadLand: () => void;
  village: VillageOutline | null;
  villageNote: string | null;
  selectVillage: (m: VillageMatch) => void;
  clearVillage: () => void;
  busy: boolean;
  jobStatus: JobStatus | null;
  error: ApiError | Error | null;
  problem: Problem | null;
  clearError: () => void;
  analyse: (file: File) => void;
  cancel: () => void;
}

const Ctx = createContext<AnalysisState | null>(null);

export function useAnalysis(): AnalysisState {
  const value = useContext(Ctx);
  if (!value) throw new Error("useAnalysis used outside AnalysisProvider");
  return value;
}

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [analysis, setAnalysis] = useState<ContourAnalysis | null>(null);
  const [options, setOptions] = useState<AnalyzeOptions>(DEFAULT_OPTIONS);
  const [layers, setLayers] = useState<LayerVisibility>(DEFAULT_LAYERS);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);
  /** How many of the ranked sites are drawn.
   *
   *  Separate from `options.maxSites`, which decides how many the *analysis*
   *  computes. Once a run is done, changing this filters what is shown without
   *  paying for another analysis -- and it is capped at how many were actually
   *  found, which the terrain decides. */
  const [shownSites, setShownSites] = useState(0);
  const [basemap, setBasemap] = useState<BasemapId>("imagery");
  const [terrain, setTerrain] = useState<TerrainLayer[]>([]);
  const [streams, setStreams] = useState<GeoJSON.FeatureCollection | null>(null);
  const [streamSummary, setStreamSummary] = useState<StreamNetworkReport | null>(null);
  /** Which drainage network is drawn. `site` is the network above the recommended
   *  site, which is what the drainage *density* describes — that figure is a
   *  property of a basin, so measuring it over the survey rectangle would average
   *  unrelated catchments. `sheet` is every channel, which is what you want when
   *  reading the terrain rather than the site. */
  const [streamScope, setScope] = useState<StreamScope>("site");
  const [loadingStreams, setLoadingStreams] = useState(false);
  const streamCache = useRef<Partial<Record<StreamScope, StreamsResponse>>>({});
  const streamsAbort = useRef<AbortController | null>(null);
  const [explored, setExplored] = useState<DelineatedCatchment | null>(null);
  const [exploring, setExploring] = useState(false);
  const exploreAbort = useRef<AbortController | null>(null);
  const [land, setLand] = useState<LandAvailability | null>(null);
  const [loadingLand, setLoadingLand] = useState(false);
  const landAbort = useRef<AbortController | null>(null);
  const [village, setVillage] = useState<VillageOutline | null>(null);
  const [villageNote, setVillageNote] = useState<string | null>(null);
  const villageAbort = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const abort = useRef<AbortController | null>(null);

  /** Fetch (or reuse) one drainage network.
   *
   *  `source` is passed explicitly rather than read from state because the first
   *  call happens inside `analyse`, before React has committed the new analysis. */
  const loadStreams = useCallback(
    async (scope: StreamScope, source: ContourAnalysis | null, signal?: AbortSignal) => {
      const demId = source?.dem_id;
      if (!demId) return;

      setScope(scope);
      const cached = streamCache.current[scope];
      if (cached) {
        setStreams(cached.streams);
        setStreamSummary(cached.network);
        return;
      }

      streamsAbort.current?.abort();
      const controller = new AbortController();
      streamsAbort.current = controller;
      setLoadingStreams(true);
      try {
        const site = source.recommended_site;
        const network = await fetchStreams(
          demId,
          // Omitting lon/lat is what asks for the whole sheet; the endpoint
          // clips to a catchment only when given a point.
          scope === "site"
            ? { thresholdHa: 1, lon: site?.location.lon, lat: site?.location.lat }
            : { thresholdHa: 1 },
          signal ?? controller.signal,
        );
        streamCache.current[scope] = network;
        setStreams(network.streams);
        setStreamSummary(network.network);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          console.warn("drainage network unavailable", err);
        }
      } finally {
        setLoadingStreams(false);
      }
    },
    [],
  );

  const analyse = useCallback(
    async (file: File) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setBusy(true);
      setError(null);
      setJobStatus(null);
      try {
        const result = await analyzeContourAsJob(file, options, setJobStatus, controller.signal);
        setAnalysis(result);
        setShownSites(result.candidate_sites.length);
        setSelectedRank(result.recommended_site?.rank ?? null);

        setTerrain([]);
        setStreams(null);
        setStreamSummary(null);
        setExplored(null);
        setLand(null);
        if (result.dem_id) {
          // Independent of the terrain tiles: the channels need no tile server,
          // so a missing tiler must not cost them too.
          streamCache.current = {};
          await loadStreams("site", result, controller.signal);
        }
        if (result.dem_id) {
          try {
            const derived = await fetchDerivatives(
              result.dem_id,
              { products: "hillshade,slope", zFactor: 4 },
              controller.signal,
            );
            setTerrain(derived.layers);
          } catch (err) {
            if ((err as Error).name !== "AbortError") {
              console.warn("terrain tiles unavailable", err);
            }
          }
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setError(err as Error);
        setAnalysis(null);
        setTerrain([]);
        setStreams(null);
        setStreamSummary(null);
      } finally {
        setBusy(false);
      }
    },
    [options, loadStreams],
  );

  /** Trimmed in one place rather than in each consumer: `candidate_sites` is read
   *  by both the map and the ranked list, and filtering separately in each is how
   *  the markers and the list end up disagreeing. */
  const shownAnalysis = useMemo<ContourAnalysis | null>(() => {
    if (!analysis) return null;
    if (shownSites >= analysis.candidate_sites.length) return analysis;
    return {
      ...analysis,
      candidate_sites: analysis.candidate_sites.slice(0, Math.max(1, shownSites)),
    };
  }, [analysis, shownSites]);

  const selectVillage = useCallback(async (match: VillageMatch) => {
    villageAbort.current?.abort();
    const controller = new AbortController();
    villageAbort.current = controller;
    setVillageNote(null);
    try {
      const boundary = await fetchVillageBoundary(match.id, controller.signal);
      if (!boundary.available || !boundary.geometry) {
        setVillage(null);
        setVillageNote(boundary.reason ?? "No boundary is available for this village.");
        return;
      }
      setVillage({
        id: match.id,
        name: match.name,
        geometry: boundary.geometry,
        represents: boundary.represents,
        isVillageBoundary: boundary.is_village_boundary,
        focus: match.focus ? { lon: match.focus.lon, lat: match.focus.lat } : null,
      });
      // The caveat is the API saying the polygon is coarser than the village
      // asked for. Surfacing it is the whole point of it existing.
      setVillageNote(boundary.caveat);
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setVillage(null);
      setVillageNote(err instanceof ApiError ? err.problem.detail : (err as Error).message);
    }
  }, []);

  const demId = analysis?.dem_id ?? null;

  const explore = useCallback(
    async (lon: number, lat: number) => {
      if (!demId) return;
      exploreAbort.current?.abort();
      const controller = new AbortController();
      exploreAbort.current = controller;
      setExploring(true);
      try {
        setExplored(await delineateCatchment(demId, lon, lat, {}, controller.signal));
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        // A click outside the surveyed area is an ordinary answer, not a failure
        // of the analysis, so it clears the exploration rather than taking over
        // the error overlay.
        setExplored(null);
        console.info("no catchment there", err);
      } finally {
        if (!controller.signal.aborted) setExploring(false);
      }
    },
    [demId],
  );

  const loadLand = useCallback(async () => {
    if (!demId) return;
    landAbort.current?.abort();
    const controller = new AbortController();
    landAbort.current = controller;
    setLoadingLand(true);
    try {
      setLand(await fetchLandAvailability(demId, {}, controller.signal));
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      // Both providers degrade inside the endpoint, so a throw here is the
      // request itself failing — reported without taking over the page, since
      // the analysis is still good without parcels.
      setLand(null);
      console.warn("available land unavailable", err);
    } finally {
      if (!controller.signal.aborted) setLoadingLand(false);
    }
  }, [demId]);

  const value = useMemo<AnalysisState>(
    () => ({
      analysis,
      shownAnalysis,
      options,
      setOptions,
      layers,
      setLayers,
      basemap,
      setBasemap,
      selectedRank,
      setSelectedRank,
      shownSites,
      setShownSites,
      terrain,
      streams,
      streamSummary,
      streamScope,
      loadingStreams,
      setStreamScope: (scope) => void loadStreams(scope, analysis),
      explored,
      exploring,
      explore: (lon, lat) => void explore(lon, lat),
      clearExplored: () => setExplored(null),
      land,
      loadingLand,
      loadLand: () => void loadLand(),
      village,
      villageNote,
      selectVillage: (m) => void selectVillage(m),
      clearVillage: () => {
        setVillage(null);
        setVillageNote(null);
      },
      busy,
      jobStatus,
      error,
      problem: error instanceof ApiError ? error.problem : null,
      clearError: () => setError(null),
      analyse: (file) => void analyse(file),
      cancel: () => {
        abort.current?.abort();
        setBusy(false);
      },
    }),
    [
      analysis, shownAnalysis, options, layers, basemap, selectedRank, shownSites,
      terrain, streams, streamSummary, streamScope, loadingStreams, explored,
      exploring, land, loadingLand, village, villageNote, busy, jobStatus, error,
      analyse, explore, loadLand, loadStreams, selectVillage,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
