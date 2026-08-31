import { useId, useRef, useState } from "react";

import { humanise, num } from "../../format";
import { useAnalysis } from "../../state/analysis";
import { JobProgress } from "../JobProgress";
import { VillageSearch } from "../VillageSearch";

const ACCEPT = ".kml,.kmz,.xml";

/**
 * The left rail: what the run is, what it was told, and what came back.
 *
 * One card rather than the six stacked panels this replaces. Village search,
 * upload and parameters are all "setting up a run", so they read as one job
 * sheet; layers moved onto the drawing where map controls belong, and
 * attribution moved to the colophon.
 */
export function JobSheet() {
  const {
    analysis, options, setOptions, busy, jobStatus, analyse, cancel,
    land, loadingLand, loadLand, village, villageNote, selectVillage, clearVillage,
  } = useAnalysis();
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const ids = { sites: useId(), slope: useId(), file: useId() };

  const grid = analysis?.interpolated_terrain;
  const map = analysis?.contour_map;
  const tier = analysis?.suitability.analysis_tier;
  const exclusions = analysis?.suitability.exclusions ?? null;

  return (
    <aside className="jobsheet" aria-label="Job sheet">
      <section>
        <span className="stamp">Job</span>
        <VillageSearch onSelect={selectVillage} selectedId={village?.id ?? null} />
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
        </div>
        {busy ? (
          <button type="button" className="act line" style={{ width: "100%" }} onClick={cancel}>
            Cancel
          </button>
        ) : (
          <button
            type="button"
            className="act"
            style={{ width: "100%" }}
            disabled={!file}
            onClick={() => file && analyse(file)}
          >
            Run
          </button>
        )}
        {busy && jobStatus && (
          <div style={{ marginTop: 14 }}>
            <JobProgress status={jobStatus} />
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

      {analysis && (
        <section>
          <span className="stamp">Read-back</span>
          <div className="readback">
            <div className="pair">
              <span>Lines read</span>
              <span>{num(map?.lines_parsed ?? 0)}</span>
            </div>
            <div className="pair">
              <span>Levels</span>
              <span>
                {map?.levels ?? "—"}
                {map?.contour_interval_m != null && ` · ${num(map.contour_interval_m, 1)} m`}
              </span>
            </div>
            <div className="pair">
              <span>Relief</span>
              <span>{num(map?.relief_m ?? 0, 1)} m</span>
            </div>
            <div className="pair">
              <span>Elevations from</span>
              <span>{map ? humanise(map.elevation_strategy) : "—"}</span>
            </div>
            <div className="pair">
              <span>Grid</span>
              <span>
                {grid ? `${grid.grid_size[0]} × ${grid.grid_size[1]}` : "—"}
              </span>
            </div>
            <div className="pair">
              <span>Data tier</span>
              <span className={tier === "full" ? "tag v" : "tag e"}>
                {tier ? humanise(tier) : "—"}
              </span>
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
      )}

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

      {analysis && (
        <section>
          <span className="stamp">Issue</span>
          <div style={{ display: "grid", gap: 8 }}>
            <a
              className="act line"
              href={`/api/v1/export/${analysis.analysis_id}`}
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
