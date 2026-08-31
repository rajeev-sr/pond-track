import { useCallback, useMemo, useRef, useState } from "react";

import {
  ApiError,
  analyzeContourAsJob,
  delineateCatchment,
  fetchDerivatives,
  fetchLandAvailability,
  fetchStreams,
  fetchVillageBoundary,
} from "./api/client";
import type {
  AnalyzeOptions,
  ContourAnalysis,
  DelineatedCatchment,
  JobStatus,
  LandAvailability,
  StreamNetworkReport,
  StreamScope,
  StreamsResponse,
  TerrainLayer,
  VillageMatch,
} from "./api/types";
import { Attribution } from "./components/Attribution";
import { ExploredPanel } from "./components/ExploredPanel";
import { JobProgress } from "./components/JobProgress";
import { LandPanel } from "./components/LandPanel";
import { LayerPanel } from "./components/LayerPanel";
import {
  MapView,
  type BasemapId,
  type LayerVisibility,
  type VillageOutline,
} from "./components/MapView";
import { ResultPanel } from "./components/ResultPanel";
import { UploadPanel } from "./components/UploadPanel";
import { VillageSearch } from "./components/VillageSearch";
import { t } from "./i18n";

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
  // pond site, and unlike the rasters they read clearly over imagery.
  streams: true,
  explored: true,
  // Both off until they exist: there are no parcels until someone asks for
  // them, and no pond footprint until a design is sized.
  parcels: true,
  pond: true,
  contours: true,
  catchment: true,
  sites: true,
  aoi: true,
  village: true,
};

export function App() {
  const [analysis, setAnalysis] = useState<ContourAnalysis | null>(null);
  const [options, setOptions] = useState<AnalyzeOptions>(DEFAULT_OPTIONS);
  const [layers, setLayers] = useState<LayerVisibility>(DEFAULT_LAYERS);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);
  /** How many of the ranked sites are drawn.
   *
   *  Separate from `options.maxSites`, which decides how many the *analysis*
   *  computes. Once a run is done, changing this filters what is shown without
   *  paying for another analysis -- and it is capped at how many were actually
   *  found, which the terrain decides: asking for 25 on a small sheet returns
   *  however many distinct sites clear the score and separation thresholds.
   */
  const [shownSites, setShownSites] = useState<number>(0);
  const [basemap, setBasemap] = useState<BasemapId>("imagery");
  const [terrain, setTerrain] = useState<TerrainLayer[]>([]);
  const [streams, setStreams] = useState<GeoJSON.FeatureCollection | null>(
    null,
  );
  const [streamSummary, setStreamSummary] =
    useState<StreamNetworkReport | null>(null);
  /** Which drainage network is on the map.
   *
   *  `site` is the network above the recommended site, which is what the
   *  drainage *density* describes -- that figure is a property of a basin, so
   *  measuring it over the survey rectangle would average unrelated catchments
   *  and mean nothing. `sheet` is every channel on the sheet, which is what you
   *  want when reading the terrain rather than the site. Both are useful, so
   *  neither is hidden.
   */
  const [streamScope, setStreamScope] = useState<StreamScope>("site");
  const [loadingStreams, setLoadingStreams] = useState(false);
  /** Fetched networks, kept per scope so switching back is instant. Cleared with
   *  the analysis: a network belongs to one DEM. */
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

  /** Fetch (or reuse) one drainage network and put it on the map.
   *
   *  `source` is passed explicitly rather than read from state because the first
   *  call happens inside `analyse`, before React has committed the new analysis.
   */
  const loadStreams = useCallback(
    async (
      scope: StreamScope,
      source: ContourAnalysis | null,
      signal?: AbortSignal,
    ) => {
      const demId = source?.dem_id;
      if (!demId) return;

      setStreamScope(scope);
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
        const result = await analyzeContourAsJob(
          file,
          options,
          setJobStatus,
          controller.signal,
        );
        setAnalysis(result);
        setShownSites(result.candidate_sites.length);
        setSelectedRank(result.recommended_site?.rank ?? null);

        // Terrain tiles are a separate, optional request: they need the tiles
        // service, and a failure there must not turn a completed analysis into
        // an error. Awaited rather than fired and forgotten so the layer panel
        // does not briefly offer toggles that are not yet wired.
        setTerrain([]);
        setStreams(null);
        setStreamSummary(null);
        setExplored(null);
        if (result.dem_id) {
          // The drainage network. Defaults to the recommended site's catchment,
          // so the reported density describes that catchment rather than the
          // survey rectangle; `loadStreams` switches scope on demand.
          // Independent of the terrain tiles: it needs no tile server, so a
          // missing TiTiler must not cost the channels too.
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

  /** The analysis as the map and the list should see it.
   *
   *  Trimmed in one place rather than in each consumer: `candidate_sites` is read
   *  in both `MapView` and `ResultPanel`, and filtering separately in each is how
   *  the marker layer and the ranked list end up disagreeing.
   */
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
        setVillageNote(
          boundary.reason ?? "No boundary is available for this village.",
        );
        return;
      }
      setVillage({
        id: match.id,
        name: match.name,
        geometry: boundary.geometry,
        represents: boundary.represents,
        isVillageBoundary: boundary.is_village_boundary,
        focus: match.focus
          ? { lon: match.focus.lon, lat: match.focus.lat }
          : null,
      });
      // The caveat is the API saying the polygon is coarser than the village
      // that was asked for. Surfacing it is the whole point of it existing.
      setVillageNote(boundary.caveat);
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setVillage(null);
      setVillageNote(
        err instanceof ApiError ? err.problem.detail : (err as Error).message,
      );
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
        setExplored(
          await delineateCatchment(demId, lon, lat, {}, controller.signal),
        );
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        // A click outside the surveyed area is an ordinary answer, not a
        // failure of the analysis -- so it clears the exploration rather than
        // taking over the error overlay.
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
      // request itself failing -- reported without taking over the page, since
      // the analysis is still perfectly good without parcels.
      setLand(null);
      console.warn("available land unavailable", err);
    } finally {
      if (!controller.signal.aborted) setLoadingLand(false);
    }
  }, [demId]);

  const cancel = useCallback(() => {
    abort.current?.abort();
    setBusy(false);
  }, []);

  const problem = error instanceof ApiError ? error.problem : null;

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          {t("app.title")} <span className="muted">{t("app.subtitle")}</span>
        </h1>
        <nav>
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
        </nav>
      </header>

      <div className="layout">
        <aside className="sidebar" aria-label="Controls">
          <VillageSearch
            onSelect={selectVillage}
            selectedId={village?.id ?? null}
          />
          {villageNote && (
            <p className="panel village-note" role="status">
              {villageNote}
            </p>
          )}
          <UploadPanel
            busy={busy}
            options={options}
            onOptionsChange={setOptions}
            onAnalyse={analyse}
            onCancel={cancel}
          />
          <LayerPanel
            visibility={layers}
            onChange={setLayers}
            basemap={basemap}
            onBasemapChange={setBasemap}
            hasContours={Boolean(analysis?.contours)}
            hasVillage={Boolean(village)}
            hasTerrain={terrain.length > 0}
            hasStreams={Boolean(streams?.features.length)}
            streamScope={streamScope}
            loadingStreams={loadingStreams}
            onStreamScopeChange={(scope) => void loadStreams(scope, analysis)}
            hasExplored={Boolean(explored)}
            hasParcels={Boolean(land?.parcels.features.length)}
            hasPond={Boolean(analysis?.recommended_site?.pond?.available)}
          />
          <LandPanel
            land={land}
            busy={loadingLand}
            enabled={Boolean(demId)}
            onLoad={loadLand}
            onClear={() => setLand(null)}
          />
          <ExploredPanel
            catchment={explored}
            busy={exploring}
            enabled={Boolean(demId)}
            onClear={() => setExplored(null)}
          />
          <Attribution />
        </aside>

        <main className="stage">
          <MapView
            analysis={shownAnalysis}
            visibility={layers}
            selectedRank={selectedRank}
            basemap={basemap}
            village={village}
            terrain={terrain}
            streams={streams}
            explored={explored}
            parcels={land?.parcels ?? null}
            onDelineate={demId ? explore : null}
            onSelectSite={setSelectedRank}
          />
          {busy && (
            <div className="overlay" role="status" aria-live="polite">
              {jobStatus ? (
                <div className="overlay-job">
                  <JobProgress status={jobStatus} />
                </div>
              ) : (
                <>
                  <div className="spinner" aria-hidden="true" />
                  <p>{t("upload.analysing")}</p>
                  <p className="muted small">Starting the job…</p>
                </>
              )}
            </div>
          )}
          {error && (
            <div className="overlay overlay--error" role="alert">
              <h2>{t("error.heading")}</h2>
              {/* The server's own reason, not a generic message: a 422 means the
                  file parsed but its contents cannot be analysed, and saying why
                  is the difference between a usable error and a dead end. */}
              <p>{problem?.detail ?? error.message}</p>
              {problem?.errors?.map((e) => (
                <p key={e.field} className="small">
                  <strong>{e.field}</strong>: {e.message}
                </p>
              ))}
              {problem?.trace_id && (
                <p className="muted small">trace {problem.trace_id}</p>
              )}
              <button
                type="button"
                className="secondary"
                onClick={() => setError(null)}
              >
                Dismiss
              </button>
            </div>
          )}
        </main>

        <aside className="results-pane" aria-label="Results">
          <ResultPanel
            analysis={shownAnalysis}
            job={jobStatus}
            streams={streamSummary}
            streamScope={streamScope}
            selectedRank={selectedRank}
            onSelectSite={setSelectedRank}
            totalSites={analysis?.candidate_sites.length ?? 0}
            shownSites={shownSites}
            onShownSitesChange={setShownSites}
          />
        </aside>
      </div>
    </div>
  );
}
