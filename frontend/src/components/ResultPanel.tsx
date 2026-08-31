import type {
  CandidateSite,
  ContourAnalysis,
  JobStatus,
  StreamNetworkReport,
  StreamScope,
} from "../api/types";
import { area, distance, humanise, num, rupees, volume } from "../format";
import { t } from "../i18n";
import RainfallPanel from "./RainfallPanel";
import StageStorageChart from "./StageStorageChart";

interface Props {
  analysis: ContourAnalysis | null;
  /** The settled job, when the analysis came through the async path. Carries the
   *  PARTIAL state and the warnings that explain it. */
  job: JobStatus | null;
  /** Drainage network summary, when the network was fetched. */
  streams: StreamNetworkReport | null;
  streamScope: StreamScope;
  /** How many sites the run produced, before the display filter. */
  totalSites: number;
  shownSites: number;
  onShownSitesChange: (count: number) => void;
  selectedRank: number | null;
  onSelectSite: (rank: number) => void;
}

/** What an order actually means, since the number alone says little to a reader. */
function strahlerHint(order: number): string {
  if (order <= 1) return "headwater only";
  if (order === 2) return "small tributaries";
  if (order === 3) return "a defined nala";
  return "a substantial channel";
}

const TIER_TONE: Record<string, string> = {
  full: "ok",
  no_soil_lulc: "warn",
  terrain_only: "warn",
};

function Fact({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>
        {value}
        {hint && <span className="muted small"> {hint}</span>}
      </dd>
    </div>
  );
}

function SiteCard({
  site,
  selected,
  onSelect,
}: {
  site: CandidateSite;
  selected: boolean;
  onSelect: () => void;
}) {
  const m = site.catchment.metrics;
  const pond = site.pond?.available ? site.pond.recommended : undefined;
  const runoff = site.runoff?.available ? site.runoff : undefined;

  return (
    <li>
      <button
        type="button"
        className={`site-card${selected ? " site-card--selected" : ""}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <header>
          <span className="rank">#{site.rank}</span>
          <span className="score">{num(site.suitability_score, 1)}/100</span>
          {/* The kind is spelled out, not encoded in colour alone. */}
          <span className={`chip chip--${site.site_kind}`}>
            {humanise(site.site_kind)}
          </span>
        </header>

        <dl className="facts">
          <Fact label="Catchment" value={area(m.area_ha)} />
          <Fact
            label="Time of concentration"
            value={
              m.time_of_concentration_min
                ? `${num(m.time_of_concentration_min, 0)} min`
                : "—"
            }
          />
          <Fact label="Relief" value={`${num(m.relief_m, 1)} m`} />
          <Fact
            label="Longest flow path"
            value={distance(m.longest_flow_path_m)}
          />
          {runoff?.annual_mean && (
            <Fact
              label="Runoff"
              value={`${volume(runoff.annual_mean.runoff_volume_m3)}/yr`}
              hint={`C = ${num(runoff.annual_mean.runoff_coefficient, 3)}`}
            />
          )}
          {runoff?.design_75_percent_dependable && (
            <Fact
              label="Design (75% dependable)"
              value={volume(
                runoff.design_75_percent_dependable.runoff_volume_m3,
              )}
            />
          )}
          {pond && (
            <>
              <Fact
                label="Pond"
                value={`${num(pond.depth_m, 1)} m deep, ${num(pond.top_length_m)} × ${num(pond.top_width_m)} m`}
              />
              <Fact label="Capacity" value={volume(pond.gross_capacity_m3)} />
              <Fact
                label="Indicative cost"
                value={rupees(pond.estimated_cost_inr)}
              />
            </>
          )}
        </dl>

        {site.pond?.binding_constraint && (
          <p className="binding">
            Limited by <strong>{humanise(site.pond.binding_constraint)}</strong>
          </p>
        )}
        {!runoff && site.runoff?.reason && (
          <p className="muted small">
            Runoff not estimated: {site.runoff.reason}
          </p>
        )}

        <details className="why">
          <summary>Why it scores {num(site.suitability_score, 1)}</summary>
          <ul className="criteria">
            {site.criteria_breakdown.map((c) => (
              <li key={c.criterion}>
                <span>{humanise(c.criterion)}</span>
                <span className="bar" aria-hidden="true">
                  <span
                    style={{ width: `${Math.round(c.normalised * 100)}%` }}
                  />
                </span>
                <span className="muted small">
                  {num(c.raw_value, 2)} → {num(c.normalised, 2)} ×{" "}
                  {num(c.weight, 2)} = {num(c.contribution, 3)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      </button>
    </li>
  );
}

export function ResultPanel({
  analysis,
  job,
  streams,
  streamScope,
  totalSites,
  shownSites,
  onShownSitesChange,
  selectedRank,
  onSelectSite,
}: Props) {
  if (!analysis) {
    return (
      <section className="panel" aria-labelledby="results-heading">
        <h2 id="results-heading">{t("results.heading")}</h2>
        <p className="muted">{t("results.none")}</p>
      </section>
    );
  }

  const cm = analysis.contour_map;
  const it = analysis.interpolated_terrain;
  const env = analysis.environment;
  const partial = job?.state === "partial";
  const selectedSite =
    analysis.candidate_sites.find((s) => s.rank === selectedRank) ??
    analysis.recommended_site;
  const selectedPond = selectedSite?.pond?.available
    ? selectedSite.pond
    : undefined;
  const tone = TIER_TONE[env.analysis_tier] ?? "warn";

  return (
    <section className="panel results" aria-labelledby="results-heading">
      <h2 id="results-heading">{t("results.heading")}</h2>

      {/* PARTIAL is a success with a caveat, so it is stated before the tier
          rather than left for the reader to infer from a degraded tier name. The
          result below is real and usable; something optional was lost getting
          to it. */}
      {partial && (
        <div className="tier tier--warn" role="status">
          <p>
            <strong>Partial result.</strong> The core analysis finished, but an
            optional layer was not available. Everything below is real; the
            missing layer is named beneath.
          </p>
          {job?.warnings.map((warning) => (
            <p key={warning} className="muted small">
              {warning}
            </p>
          ))}
        </div>
      )}

      <p className={`tier tier--${tone}`}>
        <strong>{humanise(env.analysis_tier)}</strong> — {env.tier_meaning}
      </p>
      {env.provider_failures.map((f) => (
        <p key={f.layer} className="warn-line">
          {/* Name the service, not just the layer: "Soil hydrologic group
              unavailable" leaves the reader wondering whose fault it was, and
              on a timeout the reason alone names nobody. */}
          <strong>{humanise(f.layer)}</strong> unavailable — {f.provider}:{" "}
          {f.reason}
        </p>
      ))}

      <h3>{t("results.readFromFile")}</h3>
      <dl className="facts">
        <Fact
          label="Elevation found in"
          value={humanise(cm.elevation_strategy)}
        />
        <Fact
          label="Contour lines"
          value={num(cm.lines_parsed)}
          hint={`${num(cm.vertices_used)} vertices`}
        />
        <Fact
          label="Levels"
          value={`${cm.levels} @ ${cm.contour_interval_m ?? "?"} m`}
          hint={`${num(cm.elevation_min_m, 1)}–${num(cm.elevation_max_m, 1)} m`}
        />
        <Fact label="Relief" value={`${num(cm.relief_m, 1)} m`} />
        <Fact
          label="Working CRS"
          value={`EPSG:${cm.working_crs_epsg}`}
          hint="derived"
        />
      </dl>

      <h3>{t("results.terrain")}</h3>
      <dl className="facts">
        <Fact
          label="Grid resolution"
          value={`${num(it.grid_resolution_m, 1)} m`}
          hint={it.grid_resolution_derived ? "derived" : "as requested"}
        />
        <Fact
          label="Mean contour spacing"
          value={`${num(it.mean_contour_spacing_m, 1)} m`}
        />
        <Fact
          label="Grid"
          value={`${it.grid_size[0]} × ${it.grid_size[1]}`}
          hint={`${num(it.grid_cells)} cells`}
        />
        <Fact
          label="Largest upstream area"
          value={area(it.max_upstream_area_ha)}
        />
      </dl>

      {streams && streams.reach_count > 0 && (
        <>
          <h3>
            {t("results.drainage")}{" "}
            <span className="small muted">
              {streamScope === "site" ? "· site catchment" : "· whole sheet"}
            </span>
          </h3>
          <dl className="facts">
            <Fact
              label="Channels"
              value={`${num(streams.reach_count)}`}
              hint={`${num(streams.total_length_km, 1)} km, above ${num(streams.threshold_ha, 2)} ha`}
            />
            <Fact
              label="Highest Strahler order"
              value={`${streams.max_strahler_order}`}
              hint={strahlerHint(streams.max_strahler_order)}
            />
            {streams.drainage_density_km_per_km2 != null ? (
              <Fact
                label="Drainage density"
                value={`${num(streams.drainage_density_km_per_km2, 2)} km/km²`}
                hint={
                  streamScope === "site"
                    ? "of the recommended site's catchment"
                    : "of the area analysed"
                }
              />
            ) : (
              // Not an omission. Density is length per unit *basin* area; over
              // the survey rectangle it would average unrelated catchments, so
              // the API returns null rather than a figure that reads as real.
              <Fact
                label="Drainage density"
                value="—"
                hint="needs a basin; switch to the site catchment"
              />
            )}
          </dl>
        </>
      )}

      {env.rainfall && (
        <>
          <h3>Environment</h3>
          <dl className="facts">
            {env.soil && (
              <Fact
                label="Soil"
                value={humanise(env.soil.usda_texture_class)}
                hint={`HSG ${env.soil.hydrologic_soil_group}`}
              />
            )}
            {env.land_cover && (
              <Fact
                label="Dominant land cover"
                value={humanise(env.land_cover.dominant_class)}
              />
            )}
            <Fact
              label="Rainfall"
              value={`${num(env.rainfall.annual.mean_mm, 0)} mm/yr`}
              hint={`75% dependable ${num(env.rainfall.annual.dependable_75_mm, 0)} mm`}
            />
            <Fact
              label="Monsoon"
              value={`${humanise(env.rainfall.monsoon.type)} ${env.rainfall.monsoon.months.join("–")}`}
              hint={`${num(env.rainfall.monsoon.share_pct, 0)}% of annual`}
            />
          </dl>

          <RainfallPanel env={env} site={selectedSite} />
        </>
      )}

      {selectedPond?.stage_storage_curve && (
        <>
          <h3>
            How much the ground holds{" "}
            <span className="muted small">site #{selectedSite?.rank}</span>
          </h3>
          <StageStorageChart
            curve={selectedPond.stage_storage_curve}
            designDepthM={selectedPond.recommended?.depth_m}
          />
        </>
      )}

      <h3>
        {t("results.sites")}{" "}
        <span className="muted small">
          {shownSites < totalSites ? `(${shownSites} of ${totalSites})` : `(${totalSites})`}
        </span>
      </h3>

      {/* How many of the found sites to draw. Capped at what the run actually
          produced, because that is the terrain's answer and not a preference:
          the slider on the upload panel asks for a maximum, and how many come
          back depends on how many distinct sites clear the score and separation
          thresholds. Wanting more than this means re-running with a higher
          maximum, which the hint says rather than leaving the reader to guess
          why the control stops. */}
      {totalSites > 1 && (
        <div className="shown-sites">
          <label htmlFor="shown-sites">
            {t("results.showSites")}
            <span className="value">{shownSites}</span>
          </label>
          <input
            id="shown-sites"
            type="range"
            min={1}
            max={totalSites}
            value={Math.min(shownSites, totalSites)}
            onChange={(e) => onShownSitesChange(Number(e.target.value))}
          />
          <span className="small muted">
            {shownSites >= totalSites
              ? t("results.showSites.all")
              : t("results.showSites.some")}
          </span>
        </div>
      )}

      {/* Zero sites is an answer, not a blank. It happens on ground that is
          genuinely unsuitable -- too flat to impound, too steep to dig, or
          entirely built over -- and saying which constraint emptied the set is
          what lets someone act on it. */}
      {analysis.candidate_sites.length === 0 && (
        <div className="empty">
          <p>
            <strong>No site cleared the constraints.</strong>
          </p>
          <p className="muted small">
            {analysis.suitability.feasible_cells === 0
              ? "No cell in the surveyed area passed the feasibility masks: every " +
                "one was too steep, outside the data, or on land the cover rules " +
                "out. Loosening the slope limit is the first thing to try."
              : `${num(analysis.suitability.feasible_cells)} cells were buildable, ` +
                "but none formed a cluster large enough or wet enough to justify a " +
                "pond. A lower score threshold or a smaller minimum upstream area " +
                "would widen the search."}
          </p>
        </div>
      )}

      <ul className="sites">
        {analysis.candidate_sites.map((site) => (
          <SiteCard
            key={site.rank}
            site={site}
            selected={site.rank === selectedRank}
            onSelect={() => onSelectSite(site.rank)}
          />
        ))}
      </ul>

      {analysis.warnings.length > 0 && (
        <details className="warnings">
          <summary>{analysis.warnings.length} note(s)</summary>
          <ul>
            {analysis.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      )}

      <p className="muted small">
        Analysed in {num(analysis.elapsed_s, 1)} s · {analysis.analysis_id}
      </p>
    </section>
  );
}
