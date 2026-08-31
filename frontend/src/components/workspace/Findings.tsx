import { useState } from "react";

import type { CandidateSite } from "../../api/types";
import { num, volume } from "../../format";
import { humanise } from "../../format";
import { useAnalysis } from "../../state/analysis";
import RainfallPanel from "../RainfallPanel";
import { SectionPlate } from "../SectionPlate";
import StageStorageChart from "../StageStorageChart";

type Tab = "proposal" | "candidates" | "hydrology" | "yield" | "caveats";

const TABS: { id: Tab; label: string }[] = [
  { id: "proposal", label: "Proposal" },
  { id: "candidates", label: "Candidates" },
  { id: "hydrology", label: "Hydrology" },
  { id: "yield", label: "Yield" },
  { id: "caveats", label: "Caveats" },
];

/**
 * The right pane, in five tabs.
 *
 * It replaces one continuous scroll that stacked the file summary, terrain,
 * drainage, environment, a rainfall panel, a stage–storage chart, up to
 * twenty-five site cards and the warnings — several thousand pixels in which
 * nothing was prioritised because everything was visible at once. The proposal
 * is what a reader wants first; the rest is one click away.
 */
export function Findings() {
  const {
    analysis, shownAnalysis, streamSummary, streamScope, selectedRank, setSelectedRank,
    shownSites, setShownSites, explored,
  } = useAnalysis();
  const [tab, setTab] = useState<Tab>("proposal");

  if (!analysis || !shownAnalysis) {
    return (
      <aside className="findings" aria-label="Findings">
        <div className="f-body">
          <span className="stamp">No run yet</span>
          <p style={{ color: "var(--ink-2)", fontSize: 13.5 }}>
            Choose a contour survey in the job sheet and press Run. The proposal, the candidates it
            was chosen from, the hydrology behind it and every caveat appear here.
          </p>
        </div>
      </aside>
    );
  }

  const sites = shownAnalysis.candidate_sites;
  const site: CandidateSite | null =
    sites.find((s) => s.rank === selectedRank) ?? analysis.recommended_site ?? sites[0] ?? null;
  const pond = site?.pond?.available ? site.pond : null;
  const design = pond?.recommended ?? null;
  const balance = pond?.water_balance?.available ? pond.water_balance : null;
  const runoff = site?.runoff?.available ? site.runoff : null;
  const metrics = site?.catchment.metrics ?? null;
  const exclusions = analysis.suitability.exclusions ?? null;
  const env = analysis.environment;
  const total = analysis.candidate_sites.length;

  return (
    <aside className="findings" aria-label="Findings">
      <div className="f-tabs" role="tablist">
        {TABS.map((tb) => (
          <button
            key={tb.id}
            type="button"
            role="tab"
            aria-selected={tab === tb.id}
            className={tab === tb.id ? "on" : undefined}
            onClick={() => setTab(tb.id)}
          >
            {tb.label}
          </button>
        ))}
      </div>

      <div className="f-body">
        {tab === "proposal" && site && metrics && (
          <>
            <div className="rec">
              <div className="hd">
                <span className="r">SITE {site.rank}</span>
                <span className="k">{humanise(site.site_kind)}</span>
                <span className="s">
                  {num(site.suitability_score, 1)}
                  <span style={{ fontSize: 12, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>
                    /100
                  </span>
                </span>
              </div>
              <div className="bd">
                <table className="svy">
                  <tbody>
                    <tr>
                      <td>
                        Catchment
                        <small>{num(metrics.area_km2, 2)} km²</small>
                      </td>
                      <td className="n">{num(metrics.area_ha, 1)} ha</td>
                    </tr>
                    {design && (
                      <>
                        <tr>
                          <td>
                            Design depth
                            <small>
                              {num(design.top_length_m, 0)} × {num(design.top_width_m, 0)} m
                              footprint
                            </small>
                          </td>
                          <td className="n">{num(design.depth_m, 2)} m</td>
                        </tr>
                        <tr>
                          <td>Gross storage</td>
                          <td className="n">{volume(design.gross_capacity_m3)}</td>
                        </tr>
                        <tr>
                          <td>
                            Live storage
                            <small>above dead level</small>
                          </td>
                          <td className="n">{volume(design.live_storage_m3)}</td>
                        </tr>
                      </>
                    )}
                  </tbody>
                </table>
                {pond?.binding_constraint && (
                  <div className="note warn" style={{ marginTop: 13 }}>
                    <b>Binding constraint: {humanise(pond.binding_constraint)}.</b> That is what
                    would have to change to raise capacity.
                  </div>
                )}
              </div>
            </div>

            {site.criteria_breakdown && site.criteria_breakdown.length > 0 && (
              <>
                <span className="stamp">Criteria contribution</span>
                <div className="wgt">
                  {(() => {
                    const ranked = [...site.criteria_breakdown]
                      .sort((a, b) => b.contribution - a.contribution)
                      .slice(0, 6);
                    // Scaled against the largest bar shown, so the shortest is
                    // still legible; the figure beside it carries the value.
                    const top = ranked[0]?.contribution || 1;
                    return ranked.map((c) => (
                        <div className="r" key={c.criterion}>
                          <span>{humanise(c.criterion)}</span>
                          <span className="tr">
                            <span
                              className="fl"
                              style={{ width: `${Math.max(2, (c.contribution / top) * 100)}%` }}
                            />
                          </span>
                          <span className="n">{num(c.contribution, 3)}</span>
                        </div>
                    ));
                  })()}
                </div>
              </>
            )}

            {design && (
              <>
                <span className="stamp">Section</span>
                <SectionPlate
                  fslM={site.terrain.elevation_m}
                  depthM={design.depth_m}
                  caption={`Section at site ${site.rank}`}
                />
              </>
            )}
          </>
        )}

        {tab === "candidates" && (
          <>
            <p style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 14 }}>
              {total} candidate{total === 1 ? "" : "s"} cleared the thresholds
              {shownSites < total ? `, showing ${shownSites}` : ""}.
            </p>
            <table className="svy picklist">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Form</th>
                  <th className="n">Score</th>
                  <th className="n">Catchment</th>
                  <th className="n">Storage</th>
                </tr>
              </thead>
              <tbody>
                {sites.map((s) => (
                  <tr
                    key={s.rank}
                    className={s.rank === site?.rank ? "on" : undefined}
                    onClick={() => setSelectedRank(s.rank)}
                  >
                    <td>{s.rank}</td>
                    <td>{humanise(s.site_kind)}</td>
                    <td className="n">{num(s.suitability_score, 1)}</td>
                    <td className="n">{num(s.catchment.metrics.area_ha, 1)}</td>
                    <td className="n">
                      {s.pond?.recommended
                        ? num(s.pond.recommended.gross_capacity_m3)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {total > 1 && (
              <div className="fld" style={{ marginTop: 18 }}>
                <div className="pair">
                  <label htmlFor="shown-sites">Shown on the sheet</label>
                  <span className="rv">
                    {Math.min(shownSites, total)} of {total}
                  </span>
                </div>
                <input
                  id="shown-sites"
                  type="range"
                  min={1}
                  max={total}
                  value={Math.min(shownSites, total)}
                  onChange={(e) => setShownSites(Number(e.target.value))}
                />
              </div>
            )}
          </>
        )}

        {tab === "hydrology" && (
          <>
            <span className="stamp">
              Drainage · {streamScope === "site" ? "site catchment" : "whole sheet"}
            </span>
            <table className="svy">
              <tbody>
                {streamSummary ? (
                  <>
                    <tr>
                      <td>
                        Channels
                        <small>above {num(streamSummary.threshold_ha, 2)} ha</small>
                      </td>
                      <td className="n">{num(streamSummary.reach_count)}</td>
                    </tr>
                    <tr>
                      <td>Total length</td>
                      <td className="n">{num(streamSummary.total_length_km, 2)} km</td>
                    </tr>
                    <tr>
                      <td>
                        Drainage density
                        <small>
                          {streamSummary.drainage_density_km_per_km2 == null
                            ? "a basin property; not defined over the whole sheet"
                            : "channel length per unit basin area"}
                        </small>
                      </td>
                      <td className="n">
                        {streamSummary.drainage_density_km_per_km2 == null
                          ? "—"
                          : `${num(streamSummary.drainage_density_km_per_km2, 2)} km/km²`}
                      </td>
                    </tr>
                    <tr>
                      <td>Highest Strahler order</td>
                      <td className="n">{streamSummary.max_strahler_order}</td>
                    </tr>
                  </>
                ) : (
                  <tr>
                    <td>Drainage network unavailable on this run</td>
                  </tr>
                )}
                {metrics && (
                  <>
                    <tr>
                      <td>Longest flow path</td>
                      <td className="n">{num(metrics.longest_flow_path_m, 0)} m</td>
                    </tr>
                    <tr>
                      <td>Time of concentration</td>
                      <td className="n">
                        {metrics.time_of_concentration_min == null
                          ? "—"
                          : `${num(metrics.time_of_concentration_min, 1)} min`}
                      </td>
                    </tr>
                    <tr>
                      <td>Relief</td>
                      <td className="n">{num(metrics.relief_m, 1)} m</td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>

            {explored && (
              <>
                <span className="stamp">Clicked catchment</span>
                <table className="svy">
                  <tbody>
                    <tr>
                      <td>Area</td>
                      <td className="n">{num(explored.metrics.area_ha, 1)} ha</td>
                    </tr>
                    <tr>
                      <td>Mean slope</td>
                      <td className="n">
                        {explored.metrics.mean_slope_pct == null
                          ? "—"
                          : `${num(explored.metrics.mean_slope_pct, 2)} %`}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </>
            )}

            {pond?.stage_storage_curve && (
              <>
                <span className="stamp">Stage–storage</span>
                <StageStorageChart
                  curve={pond.stage_storage_curve}
                  designDepthM={design?.depth_m}
                />
              </>
            )}
          </>
        )}

        {tab === "yield" && (
          <>
            <span className="stamp">Runoff</span>
            {runoff ? (
              <table className="svy">
                <tbody>
                  <tr>
                    <td>Method</td>
                    <td className="n">{runoff.method ?? "SCS-CN"}</td>
                  </tr>
                  {runoff.curve_number && (
                    <tr>
                      <td>
                        Curve number
                        <small>soil group {runoff.curve_number.hydrologic_soil_group}</small>
                      </td>
                      <td className="n">{num(runoff.curve_number.composite_cn_amc2, 1)}</td>
                    </tr>
                  )}
                  {runoff.annual_mean && (
                    <tr>
                      <td>
                        Annual mean
                        <small>C = {num(runoff.annual_mean.runoff_coefficient, 3)}</small>
                      </td>
                      <td className="n">{volume(runoff.annual_mean.runoff_volume_m3)}</td>
                    </tr>
                  )}
                  {runoff.design_75_percent_dependable && (
                    <tr>
                      <td>
                        Design yield
                        <small>75 % dependable</small>
                      </td>
                      <td className="n">
                        {volume(runoff.design_75_percent_dependable.runoff_volume_m3)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            ) : (
              <p style={{ fontSize: 13, color: "var(--ink-2)" }}>
                Runoff needs rainfall and soil, which were not available on this run.
              </p>
            )}

            {balance && (
              <>
                <span className="stamp">Through the year</span>
                <table className="svy">
                  <tbody>
                    <tr>
                      <td>Months holding water</td>
                      <td className="n">{balance.months_with_water} / 12</td>
                    </tr>
                    {balance.reliability_pct != null && (
                      <tr>
                        <td>Reliability</td>
                        <td className="n">{num(balance.reliability_pct, 0)} %</td>
                      </tr>
                    )}
                    {balance.dry_month && (
                      <tr>
                        <td>Driest month</td>
                        <td className="n">{balance.dry_month}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </>
            )}

            <RainfallPanel env={analysis.environment} site={site} />
          </>
        )}

        {tab === "caveats" && (
          <>
            {/* What the answer was actually built from. The tier alone is a
                token; the meaning is the API's own sentence explaining which
                quantities are measured and which are assumed, and a reader
                deciding how far to trust the figures needs it. */}
            <span className="stamp">Data tier</span>
            <p className="tier" style={{ fontSize: 13.5, color: "var(--ink-2)" }}>
              <b style={{ color: "var(--ink)" }}>{humanise(env.analysis_tier)}</b>
              {" — "}
              {env.tier_meaning}
            </p>
            {env.layers_unavailable.length > 0 && (
              <div className="note warn" style={{ marginTop: 11 }}>
                <b>Unavailable on this run:</b> {env.layers_unavailable.join(", ")}.
                {env.provider_failures.map((f) => (
                  <div key={`${f.layer}-${f.provider}`} style={{ marginTop: 6 }}>
                    {f.layer} — {f.provider}: {f.reason}
                  </div>
                ))}
              </div>
            )}

            {exclusions && (
              <>
                <span className="stamp">Ground ruled out first</span>
                <table className="svy">
                  <tbody>
                    {Object.entries(exclusions.removed_by)
                      .filter(([, v]) => v > 0)
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, v]) => (
                        <tr key={k}>
                          <td>{humanise(k)}</td>
                          <td className="n">{num(v)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {exclusions.notes.map((n) => (
                  <div className="note" key={n} style={{ marginTop: 11 }}>
                    {n}
                  </div>
                ))}
              </>
            )}

            <span className="stamp">Notes on this run</span>
            {analysis.warnings.length === 0 && (
              <p style={{ fontSize: 13, color: "var(--ink-2)" }}>
                Nothing was flagged on this run.
              </p>
            )}
            {analysis.warnings.map((w) => (
              <div className="note warn" key={w}>
                {w}
              </div>
            ))}

            {analysis.explanation?.recommended?.caveats?.map((c: string) => (
              <div className="note stop" key={c}>
                {c}
              </div>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
