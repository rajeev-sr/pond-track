import type { DelineatedCatchment } from "../api/types";
import { area, distance } from "../format";
import { t } from "../i18n";

interface Props {
  catchment: DelineatedCatchment | null;
  busy: boolean;
  /** Null when there is no analysis loaded, so clicking does nothing yet. */
  enabled: boolean;
  onClear: () => void;
}

/**
 * The catchment the user delineated by clicking, kept separate from the
 * analysis' own results.
 *
 * It exists mostly so the routing can be checked rather than trusted: clicking
 * three points and getting three visibly different catchments is the evidence
 * that the flow model is doing something, and it is the exit criterion for this
 * phase of the plan.
 */
export function ExploredPanel({ catchment, busy, enabled, onClear }: Props) {
  if (!enabled) return null;

  return (
    <section className="panel" aria-labelledby="explored-heading">
      <h2 id="explored-heading">{t("explore.heading")}</h2>

      {!catchment && !busy && <p className="small muted">{t("explore.hint")}</p>}
      {busy && (
        <p className="small muted" role="status">
          {t("explore.working")}
        </p>
      )}

      {catchment && (
        <>
          <dl className="facts">
            <div>
              <dt>Area</dt>
              <dd>{area(catchment.metrics.area_ha)}</dd>
            </div>
            <div>
              <dt>Relief</dt>
              <dd>{distance(catchment.metrics.relief_m)}</dd>
            </div>
            {catchment.metrics.time_of_concentration_min != null && (
              <div>
                <dt>Time of concentration</dt>
                <dd>{Math.round(catchment.metrics.time_of_concentration_min)} min</dd>
              </div>
            )}
            <div>
              <dt>Longest flow path</dt>
              <dd>{distance(catchment.metrics.longest_flow_path_m)}</dd>
            </div>
          </dl>

          {/* Snapping is the difference between a few hectares and a few
              hundred, so it is stated rather than hidden. */}
          {catchment.snapped.was_snapped && (
            <p className="small muted note">
              {t("explore.moved")} {distance(catchment.snapped.moved_m)}{" "}
              {t("explore.ontoChannel")}
              {catchment.snapped.hit_the_search_limit && (
                <>
                  {" "}
                  <strong>{t("explore.limitHit")}</strong>
                </>
              )}
            </p>
          )}
          {catchment.metrics.touches_grid_edge && (
            <p className="warn-line">{t("explore.touchesEdge")}</p>
          )}

          <button type="button" className="link-button" onClick={onClear}>
            {t("explore.clear")}
          </button>
        </>
      )}
    </section>
  );
}
