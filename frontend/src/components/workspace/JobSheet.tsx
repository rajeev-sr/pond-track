import { useEffect, useId, useRef, useState } from "react";

import { villageSearchAvailable, withClient } from "../../api/client";
import type { ContourAnalysis } from "../../api/types";
import { humanise, num } from "../../format";
import { AREA_MAX_KM2, bboxAreaKm2, bboxProblem, bboxSizeKm } from "../../geo";
import { useAnalysis, type InputMode } from "../../state/analysis";
import { JobProgress } from "../JobProgress";
import { VillageSearch } from "../VillageSearch";

const ACCEPT = ".kml,.kmz,.xml";

const MODES: { id: InputMode; label: string }[] = [
  { id: "upload", label: "Upload contour map" },
  { id: "area", label: "Draw area on map" },
];

/**
 * The left rail: what the run is, what it was told, and what came back.
 *
 * One card rather than the six stacked panels this replaces. Village search,
 * the input and parameters are all "setting up a run", so they read as one job
 * sheet; layers moved onto the drawing where map controls belong, and
 * attribution moved to the colophon.
 *
 * Two inputs, one Run: a contour sheet uploaded as KML/KMZ, or a rectangle
 * drawn on the map, which is analysed on the Copernicus 30 m terrain model.
 */
export function JobSheet() {
  const {
    analysis, options, setOptions, busy, jobStatus, connection, analyse, cancel,
    land, loadingLand, loadLand, village, villageNote, selectVillage, clearVillage,
    inputMode, setInputMode, drawnArea, setDrawnArea, drawing, setDrawing,
    pickSampleArea, analyseArea,
  } = useAnalysis();
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  // Hidden until the server says it can search villages: on a system with no
  // village database the field could only ever answer with an error.
  const [villagesOn, setVillagesOn] = useState(false);
  useEffect(() => {
    let live = true;
    void villageSearchAvailable().then((on) => live && setVillagesOn(on));
    return () => {
      live = false;
    };
  }, []);
  const ids = { sites: useId(), slope: useId(), file: useId(), area: useId() };

  const areaProblem = drawnArea ? bboxProblem(drawnArea) : null;
  const canRun =
    inputMode === "upload" ? Boolean(file) : Boolean(drawnArea) && !areaProblem && !drawing;
  const runNow = () => {
    if (inputMode === "upload") {
      if (file) analyse(file);
    } else if (drawnArea && !areaProblem) {
      analyseArea(drawnArea);
    }
  };

  return (
    <aside className="jobsheet" aria-label="Job sheet">
      <section>
        <span className="stamp">Job</span>
        {villagesOn && (
          <VillageSearch onSelect={selectVillage} selectedId={village?.id ?? null} />
        )}
        {villageNote && (
          <p className="note" style={{ fontSize: 12.5, margin: "0 0 12px" }}>
            {villageNote}
          </p>
        )}
        {village && (
          <button
            type="button"
            className="act line"
            style={{ width: "100%", marginBottom: 12 }}
            onClick={clearVillage}
          >
            Clear village
          </button>
        )}

        <div className="modes" role="tablist" aria-label="Input">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="tab"
              aria-selected={inputMode === m.id}
              className={inputMode === m.id ? "on" : undefined}
              disabled={busy}
              onClick={() => setInputMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>

        {inputMode === "upload" ? (
          <div className="fld">
            <label htmlFor={ids.file}>Contour survey</label>
            <input
              ref={fileInput}
              id={ids.file}
              type="file"
              accept={ACCEPT}
              style={{ display: "none" }}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className="act line"
              style={{ width: "100%", textTransform: "none", letterSpacing: 0, fontFamily: "var(--sans)", fontSize: 13 }}
              onClick={() => fileInput.current?.click()}
            >
              {file ? file.name : "Choose a KML or KMZ file"}
            </button>
            <p className="area-hint">KML or KMZ contour lines, up to 50 MB.</p>
          </div>
        ) : (
          <div className="fld">
            <label htmlFor={ids.area}>Area on the map</label>
            <button
              id={ids.area}
              type="button"
              className={drawing ? "act" : "act line"}
              aria-pressed={drawing}
              style={{ width: "100%", textTransform: "none", letterSpacing: 0, fontFamily: "var(--sans)", fontSize: 13 }}
              disabled={busy}
              onClick={() => setDrawing(!drawing)}
            >
              {drawing
                ? "Drag on the map · Esc to cancel"
                : drawnArea
                  ? "Redraw the rectangle"
                  : "Draw a rectangle"}
            </button>
            <AreaReadout />
            <div style={{ display: "flex", gap: 8, marginTop: 9 }}>
              <button
                type="button"
                className="act line"
                style={{ flex: 1, padding: "7px 8px", fontSize: 10.5 }}
                disabled={busy}
                onClick={pickSampleArea}
              >
                Use the sample area
              </button>
              {drawnArea && (
                <button
                  type="button"
                  className="act line"
                  style={{ padding: "7px 10px", fontSize: 10.5 }}
                  disabled={busy}
                  onClick={() => {
                    setDrawnArea(null);
                    setDrawing(false);
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            <p className="area-hint">
              Terrain comes from the Copernicus 30 m global model, fetched for the rectangle plus a
              500 m margin. Up to {AREA_MAX_KM2} km². Coarser than a surveyed sheet: use it to screen
              a village, and upload contours where a survey exists.
            </p>
          </div>
        )}

        {busy ? (
          <button type="button" className="act line" style={{ width: "100%" }} onClick={cancel}>
            Cancel
          </button>
        ) : (
          <button
            type="button"
            className="act"
            style={{ width: "100%" }}
            disabled={!canRun}
            onClick={runNow}
          >
            Run
          </button>
        )}
        {busy && !jobStatus && (
          // Before the server has answered at all: say so, rather than showing
          // a pressed button and nothing else while a connection stalls.
          <div className="job" role="status" aria-live="polite" style={{ marginTop: 14 }}>
            <div className="job-head">
              <span>{connection ?? "Contacting the server…"}</span>
            </div>
          </div>
        )}
        {busy && jobStatus && (
          <div style={{ marginTop: 14 }}>
            <JobProgress status={jobStatus} />
            {connection && (
              <p role="status" style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6 }}>
                {connection}
              </p>
            )}
          </div>
        )}
      </section>

      <section>
        <span className="stamp">Parameters</span>
        <div className="fld">
          <div className="pair">
            <label htmlFor={ids.sites}>Candidate sites</label>
            <span className="rv">{options.maxSites}</span>
          </div>
          {/* 25 is the API's own ceiling (`max_sites` is le=25). How many come
              back is bounded by the terrain, not by this. */}
          <input
            id={ids.sites}
            type="range"
            min={1}
            max={25}
            value={options.maxSites}
            onChange={(e) => setOptions({ ...options, maxSites: Number(e.target.value) })}
          />
        </div>
        <div className="fld">
          <div className="pair">
            <label htmlFor={ids.slope}>Slope limit</label>
            <span className="rv">{options.maxSlopePct} %</span>
          </div>
          <input
            id={ids.slope}
            type="range"
            min={1}
            max={20}
            value={options.maxSlopePct}
            onChange={(e) => setOptions({ ...options, maxSlopePct: Number(e.target.value) })}
          />
        </div>
        <label className="lg" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={options.enrich}
            onChange={(e) => setOptions({ ...options, enrich: e.target.checked })}
          />
          <span className="name" style={{ fontSize: 12.5 }}>
            Fetch soil, land cover and rainfall
          </span>
        </label>
      </section>

      {analysis && <ReadBack analysis={analysis} />}

      {analysis && (
        <section>
          <span className="stamp">Land</span>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", margin: "0 0 11px" }}>
            {land
              ? `${num(land.summary.parcel_count)} parcels, ${num(land.summary.total_available_ha, 1)} ha after exclusions.`
              : "Parcels are a separate, slower read of the buildable ground."}
          </p>
          <button
            type="button"
            className="act line"
            style={{ width: "100%" }}
            disabled={loadingLand}
            onClick={loadLand}
          >
            {loadingLand ? "Reading…" : land ? "Re-read" : "Load available land"}
          </button>
        </section>
      )}

      {analysis?.job_id && (
        <section>
          <span className="stamp">Issue</span>
          <div style={{ display: "grid", gap: 8 }}>
            {/* Addressed by the job, not by `analysis_id`: export reads the job
                store, and the analysis id is not a key in it. */}
            <a
              className="act line"
              href={withClient(`/api/v1/export/${analysis.job_id}`)}
              style={{ textDecoration: "none" }}
            >
              Geometry · GeoJSON
            </a>
          </div>
        </section>
      )}
    </aside>
  );
}

/** The drawn rectangle's size and corners, and why it cannot run if it cannot. */
function AreaReadout() {
  const { drawnArea } = useAnalysis();
  if (!drawnArea) {
    return (
      <p className="area-readout is-empty">
        No area yet. Press the button, then drag a rectangle on the map.
      </p>
    );
  }
  const km2 = bboxAreaKm2(drawnArea);
  const { width, height } = bboxSizeKm(drawnArea);
  const problem = bboxProblem(drawnArea);
  const [w, s, e, n] = drawnArea;
  return (
    <div className={problem ? "area-readout is-bad" : "area-readout"} aria-live="polite">
      <div className="pair">
        <b>{num(km2, km2 < 10 ? 2 : 1)} km²</b>
        <span>
          {num(width, 2)} × {num(height, 2)} km
        </span>
      </div>
      <div className="corners">
        {s.toFixed(4)}–{n.toFixed(4)}° N · {w.toFixed(4)}–{e.toFixed(4)}° E
      </div>
      {problem && <div className="why">{problem}</div>}
    </div>
  );
}

/** What was read, per input: lines and levels for a sheet; the terrain model,
 *  area and cache state for a drawn rectangle. */
function ReadBack({ analysis }: { analysis: ContourAnalysis }) {
  const grid = analysis.interpolated_terrain;
  const sheet = analysis.contour_map;
  const source = analysis.terrain_source;
  const tier = analysis.suitability.analysis_tier;
  const exclusions = analysis.suitability.exclusions ?? null;
  return (
    <section>
      <span className="stamp">Read-back</span>
      <div className="readback">
        {sheet ? (
          <>
            <div className="pair">
              <span>Lines read</span>
              <span>{num(sheet.lines_parsed)}</span>
            </div>
            <div className="pair">
              <span>Levels</span>
              <span>
                {sheet.levels}
                {sheet.contour_interval_m != null && ` · ${num(sheet.contour_interval_m, 1)} m`}
              </span>
            </div>
            <div className="pair">
              <span>Relief</span>
              <span>{num(sheet.relief_m, 1)} m</span>
            </div>
            <div className="pair">
              <span>Elevations from</span>
              <span>{humanise(sheet.elevation_strategy)}</span>
            </div>
          </>
        ) : (
          <>
            <div className="pair">
              <span>Terrain</span>
              <span>Copernicus GLO-30</span>
            </div>
            <div className="pair">
              <span>Area</span>
              <span>{source.area_km2 != null ? `${num(source.area_km2, 2)} km²` : "—"}</span>
            </div>
            <div className="pair">
              <span>Relief</span>
              <span>{source.relief_m != null ? `${num(source.relief_m, 1)} m` : "—"}</span>
            </div>
            <div className="pair">
              <span>Terrain read</span>
              <span>{source.cached ? "from cache" : "from Copernicus"}</span>
            </div>
          </>
        )}
        <div className="pair">
          <span>Grid</span>
          <span>
            {grid.grid_size[0]} × {grid.grid_size[1]} @ {num(grid.grid_resolution_m, 0)} m
          </span>
        </div>
        <div className="pair">
          <span>Data tier</span>
          <span className={tier === "full" ? "tag v" : "tag e"}>{humanise(tier)}</span>
        </div>
        {exclusions && (
          <div className="pair">
            <span>Exclusions</span>
            <span
              className={
                exclusions.confidence === "high"
                  ? "tag v"
                  : exclusions.confidence === "partial"
                    ? "tag e"
                    : "tag a"
              }
            >
              {exclusions.confidence}
            </span>
          </div>
        )}
        <div className="pair">
          <span>Elapsed</span>
          <span>{num(analysis.elapsed_s, 2)} s</span>
        </div>
      </div>
    </section>
  );
}
