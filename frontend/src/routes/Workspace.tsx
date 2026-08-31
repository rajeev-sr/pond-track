import { useEffect, useRef } from "react";

import { Findings } from "../components/workspace/Findings";
import { JobSheet } from "../components/workspace/JobSheet";
import { LegendBox } from "../components/workspace/LegendBox";
import { TitleBlock } from "../components/workspace/TitleBlock";
import { MapView } from "../components/MapView";
import { MinButton } from "../components/workspace/MinButton";
import { num } from "../format";
import { useAnalysis } from "../state/analysis";
import { useCollapsed } from "../state/collapsed";

/**
 * The bench: job sheet, drawing, findings.
 *
 * Three fixed columns each with their own scroll, so the drawing never leaves the
 * screen. Layers live on the drawing as a legend and the sheet metadata as a
 * title block, which is where a survey plate carries them — and it keeps the
 * left rail for the job rather than for map chrome.
 */
export function Workspace() {
  const s = useAnalysis();
  const map = s.shownAnalysis;
  const [noteShut, toggleNote, setNoteShut] = useCollapsed("drawing-note");

  /** A click on the sheet is a request for that catchment, so the note opens to
   *  answer it even if the reader had folded it away — withholding what someone
   *  just asked for is not what a collapsed panel should mean. It stays open
   *  only for that result; collapsing again sticks. */
  const lastExplored = useRef<string | null>(null);
  useEffect(() => {
    const key = s.explored ? JSON.stringify(s.explored.outlet) : null;
    if (key && key !== lastExplored.current) setNoteShut(false);
    lastExplored.current = key;
  }, [s.explored, setNoteShut]);

  return (
    <div className="bench">
      <JobSheet />

      <div className="drawing">
        <MapView
          analysis={map}
          visibility={s.layers}
          selectedRank={s.selectedRank}
          basemap={s.basemap}
          village={s.village}
          terrain={s.terrain}
          streams={s.streams}
          explored={s.explored}
          parcels={s.land?.parcels ?? null}
          onDelineate={map?.dem_id ? s.explore : null}
          onSelectSite={s.setSelectedRank}
        />

        {/* Rendered as soon as there is any layer to control — not only after
            an analysis. Searching a village before uploading a sheet draws its
            outline on the map, and gating the legend on the analysis left no way
            to turn that off again. */}
        {(s.analysis || s.village) && (
          <LegendBox
            visibility={s.layers}
            onChange={s.setLayers}
            available={{
              contours: Boolean(s.analysis?.contours),
              hillshade: s.terrain.length > 0,
              slope: s.terrain.length > 0,
              streams: Boolean(s.streams?.features.length),
              catchment: Boolean(s.analysis),
              sites: Boolean(s.analysis?.candidate_sites.length),
              pond: Boolean(s.analysis?.recommended_site?.pond?.available),
              explored: Boolean(s.explored),
              parcels: Boolean(s.land),
              village: Boolean(s.village),
              aoi: Boolean(s.analysis),
            }}
            streamScope={s.streamScope}
            onStreamScopeChange={s.setStreamScope}
            loadingStreams={s.loadingStreams}
            basemap={s.basemap}
            onBasemapChange={s.setBasemap}
          />
        )}

        <TitleBlock analysis={s.analysis} />

        {s.exploring && (
          <div className="drawing-note" role="status" aria-live="polite">
            <div className="panel-head">
              <span className="stamp">Delineating</span>
              <MinButton collapsed={noteShut} onToggle={toggleNote} label="this note" />
            </div>
            <div hidden={noteShut}>Tracing the catchment above that point.</div>
          </div>
        )}
        {/* A labelled live region, so the result is announced rather than merely
            appearing — and so it is addressable, which the delineation test
            relies on to read back one area per click. */}
        {!s.exploring && s.explored && (
          <div
            className="drawing-note"
            role="status"
            aria-live="polite"
            aria-labelledby="explored-heading"
          >
            <div className="panel-head">
              <span className="stamp" id="explored-heading">
                Clicked catchment
              </span>
              <MinButton collapsed={noteShut} onToggle={toggleNote} label="this note" />
            </div>
            <div hidden={noteShut}>
              {num(s.explored.metrics.area_ha, 1)} ha drains to that point.
              {s.explored.snapped?.moved_m ? (
                <>
                  {" "}
                  The point was moved {num(s.explored.snapped.moved_m, 0)} m onto the nearest
                  channel.
                </>
              ) : null}
              <div style={{ marginTop: 9 }}>
                <button
                  type="button"
                  className="act line"
                  style={{ padding: "5px 10px", fontSize: 10 }}
                  onClick={s.clearExplored}
                >
                  Clear
                </button>
              </div>
            </div>
          </div>
        )}
        {/* Standing invitation while nothing has been delineated. Click-to-
            delineate is not discoverable otherwise — there is no affordance on a
            map to say it answers clicks — and the previous layout carried the
            same hint in a sidebar panel. */}
        {s.analysis && !s.exploring && !s.explored && (
          <div className="drawing-note">
            <div className="panel-head">
              <span className="stamp" id="explored-heading">
                Clicked catchment
              </span>
              <MinButton collapsed={noteShut} onToggle={toggleNote} label="this note" />
            </div>
            <div hidden={noteShut}>
              Click anywhere on the sheet to delineate the catchment above that point.
            </div>
          </div>
        )}
        {!s.analysis && !s.busy && (
          <div className="drawing-note">
            <div className="panel-head">
              <span className="stamp">Empty sheet</span>
              <MinButton collapsed={noteShut} onToggle={toggleNote} label="this note" />
            </div>
            <div hidden={noteShut}>Choose a contour survey in the job sheet and press Run.</div>
          </div>
        )}

        {s.error && (
          <div className="overlay overlay--error" role="alert">
            <span className="stamp" style={{ display: "block", marginBottom: 6 }}>
              Could not analyse this file
            </span>
            <p style={{ fontSize: 13.5, color: "var(--ink-2)" }}>
              {s.problem?.detail ?? s.error.message}
            </p>
            {s.problem?.errors?.map((e) => (
              <p key={e.field} style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 6 }}>
                {e.field}: {e.message}
              </p>
            ))}
            {s.problem?.trace_id && (
              <p className="fignum" style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 8 }}>
                trace {s.problem.trace_id}
              </p>
            )}
            <div style={{ marginTop: 11 }}>
              <button type="button" className="act line" onClick={s.clearError}>
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      <Findings />
    </div>
  );
}
