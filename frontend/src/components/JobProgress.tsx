import type { JobStatus } from "../api/types";
import { num } from "../format";

/**
 * Live progress for a running analysis (M6-12).
 *
 * The percentage comes from the server already weighted by each step's measured
 * share of a cold run -- enrichment alone is 82 % of it -- so the bar tracks
 * elapsed time rather than step count. The step list is drawn to those same
 * weights, which is why "Fetching soil, land cover and rainfall" is a wide band
 * and "Reading the contour map" a sliver: the geometry says where the wait is
 * before the wait happens, so twenty motionless seconds read as expected rather
 * than as a hang.
 *
 * Outcome is never carried by colour alone (WCAG 1.4.1): each step has a text
 * mark beside its label, and the current one is named in full above the bar.
 */

/** A glyph per outcome, so the state survives greyscale and colour-blindness. */
const MARK: Record<string, string> = {
  done: "✓",
  running: "•",
  failed: "!",
  skipped: "–",
  pending: "",
};

export function JobProgress({ status }: { status: JobStatus | null }) {
  if (!status) return null;

  const pct = Math.max(0, Math.min(100, status.progress_pct));
  const running = status.state === "running" || status.state === "queued";
  const retrying = status.state === "retrying";

  return (
    <div className="job" role="status" aria-live="polite">
      <div className="job-head">
        <span>
          {status.current_step_label ??
            (status.state === "queued"
              ? "Waiting for a worker"
              : "Finishing up")}
        </span>
        <strong className="job-pct">{pct}%</strong>
      </div>

      <div
        className="job-track"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Analysis progress"
      >
        <div
          className={`job-fill${retrying ? " job-fill--retrying" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Steps at their true relative widths. A step's share of the strip is its
          share of the runtime, so the reader can see the long wait coming. */}
      <ul className="job-steps">
        {status.steps.map((step) => (
          <li
            key={step.name}
            className={`job-step job-step--${step.outcome}`}
            style={{ flexGrow: Math.max(step.weight, 0.02) }}
            title={`${step.label} — ${step.outcome}`}
          >
            {/* Geometry only: the heading above already names the running
                step, so repeating it inside a 20px band was redundant ink. The
                outcome glyph is kept for screen readers and the hover title. */}
            <span className="job-step-bar" aria-hidden="true" />
            <span className="job-step-name">
              {MARK[step.outcome]} {step.label}
            </span>
          </li>
        ))}
      </ul>

      <p className="job-meta muted small">
        {retrying && <strong>Retrying (attempt {status.attempt}). </strong>}
        {status.elapsed_s != null && <>{num(status.elapsed_s, 1)} s elapsed</>}
        {running && status.current_step === "enrichment" && (
          <> · this is the slow part, it reads three external services</>
        )}
      </p>

      {status.warnings.map((warning) => (
        <p key={warning} className="warn-line">
          {warning}
        </p>
      ))}
    </div>
  );
}
