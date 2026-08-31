import type { LandAvailability } from "../api/types";
import { area, humanise, num } from "../format";

interface Props {
  land: LandAvailability | null;
  busy: boolean;
  /** False until a contour map has been analysed, so there is a DEM to read. */
  enabled: boolean;
  onLoad: () => void;
  onClear: () => void;
}

/** What each exclusion rule removed, in the order a reader would ask about it. */
const RULE_LABELS: Record<string, string> = {
  slope: "Too steep",
  land_cover: "Land cover",
  osm_building: "Near buildings",
  osm_road: "Near roads",
  osm_track: "Near tracks",
  osm_water: "Near water",
  osm_landuse: "Committed land",
};

/**
 * Land a pond could actually be dug on (FR-3).
 *
 * Loaded on request rather than with the analysis. It reads WorldCover and asks
 * Overpass for the window, which is around fifteen seconds on a cold cache
 * against roughly a second warm -- too slow to impose on every upload, and the
 * answer is only interesting once someone asks where the buildable ground is.
 */
export function LandPanel({ land, busy, enabled, onLoad, onClear }: Props) {
  if (!enabled) return null;

  return (
    <section className="panel" aria-labelledby="land-heading">
      <h2 id="land-heading">Available land</h2>

      {!land && !busy && (
        <>
          <p className="small muted">
            Parcels clear of buildings, roads, water and steep ground.
          </p>
          <button type="button" onClick={onLoad}>
            Find buildable land
          </button>
        </>
      )}

      {busy && (
        <p className="small muted" role="status">
          Reading land cover and OpenStreetMap…
        </p>
      )}

      {land && (
        <>
          <dl className="facts">
            <div className="fact">
              <dt>Parcels</dt>
              <dd>{num(land.summary.parcel_count)}</dd>
            </div>
            <div className="fact">
              <dt>Total area</dt>
              <dd>{area(land.summary.total_available_ha)}</dd>
            </div>
            <div className="fact">
              <dt>Slope limit</dt>
              <dd>{num(land.summary.criteria.max_slope_pct, 1)}%</dd>
            </div>
          </dl>

          {/* Which rule removed what. A parcel count with no audit invites the
              reader to assume the terrain was the constraint when the land-cover
              mask may have done most of the work. */}
          <details className="chart-table">
            <summary>What was ruled out</summary>
            <table>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Cells</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(land.summary.removed_by)
                  .filter(([, cells]) => cells > 0)
                  .sort((a, b) => b[1] - a[1])
                  .map(([rule, cells]) => (
                    <tr key={rule}>
                      <th scope="row">{RULE_LABELS[rule] ?? humanise(rule)}</th>
                      <td>{num(cells)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </details>

          {/* OSM silence is not evidence of open ground, and a reader deciding
              on a site needs to know which way the uncertainty points. */}
          {!land.summary.criteria.osm_exclusions_applied && (
            <p className="warn-line">
              No OpenStreetMap features were found here, so buildings and roads
              have not been subtracted. Treat the parcels as optimistic.
            </p>
          )}
          {land.unavailable.map((miss) => (
            <p key={miss.layer} className="warn-line">
              {humanise(miss.layer)} unavailable ({miss.provider}) —{" "}
              {miss.reason}
            </p>
          ))}

          <button type="button" className="link-button" onClick={onClear}>
            Clear parcels
          </button>
        </>
      )}
    </section>
  );
}
