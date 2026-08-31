import { Link } from "react-router-dom";

import { SectionPlate } from "../components/SectionPlate";
import { num, volume } from "../format";
import { useAnalysis } from "../state/analysis";

/** The four ways a village pond ends up in the wrong place. This is the problem
 *  the tool exists to address, so it is stated as specifically as possible
 *  rather than as a claim about water scarcity in general. */
const FAILURES = [
  {
    k: "1.1",
    h: "Too little catchment",
    p: "The structure is built and never fills. Contributing area was never measured, only judged from the look of the ground.",
  },
  {
    k: "1.2",
    h: "Water already there",
    p: "The chosen spot is an existing tank or a river bed. It is the wettest ground on the sheet, which is exactly why a terrain-only judgement points at it.",
  },
  {
    k: "1.3",
    h: "Storage that cannot be built",
    p: "A capacity is promised that the ground will not give without excavation deeper than the budget, or a bund longer than the land available.",
  },
  {
    k: "1.4",
    h: "No record of the reasoning",
    p: "When the pond underperforms, nothing on file says which assumptions were made, so the next one repeats the mistake.",
  },
];

const STAGES = [
  {
    n: "Stage 01",
    h: "Surface",
    p: "Contour polylines are triangulated into a metric elevation grid, in the UTM zone the sheet itself falls in.",
  },
  {
    n: "Stage 02",
    h: "Flow",
    p: "Depressions conditioned, flow directions assigned, accumulation traced — giving channels, catchments and time of concentration.",
  },
  {
    n: "Stage 03",
    h: "Site",
    p: "Nine weighted criteria rank the buildable ground that survives the exclusions — water, rivers, roads, buildings.",
  },
  {
    n: "Stage 04",
    h: "Design",
    p: "Stage–storage from the surface, yield from rainfall and soil, then depth, footprint and the constraint that binds.",
  },
];

export function Brief() {
  const { analysis } = useAnalysis();
  const site = analysis?.recommended_site ?? null;
  const pond = site?.pond?.available ? site.pond.recommended : null;
  const balance = site?.pond?.water_balance?.available ? site.pond.water_balance : null;

  return (
    <>
      <div className="sheet">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.02fr 1fr",
            gap: 48,
            padding: "54px 0 60px",
            alignItems: "start",
          }}
          className="brief-open"
        >
          <div>
            <span className="stamp">The aim</span>
            <h1 style={{ marginTop: 14 }}>
              Where should this
              <br />
              village build its pond?
            </h1>
            <p
              style={{
                fontFamily: "var(--serif)",
                fontSize: 20,
                lineHeight: 1.5,
                color: "var(--ink-2)",
                marginTop: 22,
                maxWidth: "40ch",
              }}
            >
              A Gram Panchayat has a contour sheet and a budget. Contour answers the question that
              sheet cannot:{" "}
              <b style={{ color: "var(--ink)", fontWeight: 500 }}>
                which point on this land will hold water, how much catchment feeds it, how much it
                will yield in a normal year, and how big the structure should be.
              </b>
            </p>
            <div style={{ display: "flex", gap: 10, marginTop: 28 }}>
              <Link className="act" to="/workspace">
                Open the workspace
              </Link>
              <Link className="act line" to="/method">
                Read the method
              </Link>
            </div>
            <div
              style={{
                display: "flex",
                gap: 26,
                marginTop: 30,
                paddingTop: 20,
                borderTop: "1px solid var(--rule)",
                flexWrap: "wrap",
              }}
            >
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                <span className="stamp" style={{ display: "block", marginBottom: 4 }}>
                  Input
                </span>
                Contour survey · KML / KMZ
              </div>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                <span className="stamp" style={{ display: "block", marginBottom: 4 }}>
                  Output
                </span>
                Ranked sites · catchment · yield · design
              </div>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                <span className="stamp" style={{ display: "block", marginBottom: 4 }}>
                  Scale
                </span>
                Village, 1–10 km²
              </div>
            </div>
          </div>

          {/* A schematic here, not a result: the brief describes what the tool
              produces, and inventing reduced levels for a sheet nobody uploaded
              would be a figure with nothing behind it. */}
          <SectionPlate caption="What the tool proposes — schematic" />
        </div>
      </div>

      <div className="sheet">
        <div className="blk">
          <div className="mgn">
            <div className="no">01</div>
            <span className="stamp">Problem</span>
          </div>
          <div className="bdy">
            <h2 style={{ maxWidth: "22ch" }}>Four ways a village pond is put in the wrong place.</h2>
            <p style={{ color: "var(--ink-2)", marginTop: 14, maxWidth: "62ch" }}>
              Siting is usually settled by eye, or by an engineer&rsquo;s single visit. The contour
              sheet that already exists for the village holds the answer, but reading catchment area
              and storage off it by hand is slow and rarely done. Each failure below wastes public
              money in a different way.
            </p>
            <div className="ledger" style={{ marginTop: 26 }}>
              {FAILURES.map((f) => (
                <div className="row" key={f.k}>
                  <div className="k">{f.k}</div>
                  <div>
                    <h3>{f.h}</h3>
                  </div>
                  <div>
                    <p>{f.p}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="blk">
          <div className="mgn">
            <div className="no">02</div>
            <span className="stamp">Approach</span>
          </div>
          <div className="bdy">
            <h2 style={{ maxWidth: "24ch" }}>From contour lines to a dimensioned proposal.</h2>
            <p style={{ color: "var(--ink-2)", marginTop: 14, maxWidth: "62ch" }}>
              Four stages. Each leaves an artefact you can inspect and disagree with — a surface, a
              flow network, a ranking, a design — rather than a single number to take on trust.
            </p>
            <div className="stages" style={{ marginTop: 26 }}>
              {STAGES.map((s) => (
                <div key={s.n}>
                  <span className="stamp">{s.n}</span>
                  <h3>{s.h}</h3>
                  <p>{s.p}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="blk">
          <div className="mgn">
            <div className="no">03</div>
            <span className="stamp">Result</span>
          </div>
          <div className="bdy">
            {site ? (
              <>
                <h2 style={{ maxWidth: "24ch" }}>This sheet, read end to end.</h2>
                <p style={{ color: "var(--ink-2)", marginTop: 14, maxWidth: "60ch" }}>
                  {num(analysis?.contour_map.lines_parsed ?? 0)} contour lines
                  {analysis?.contour_map.contour_interval_m != null &&
                    ` at ${num(analysis.contour_map.contour_interval_m, 1)} m`}
                  , reduced to a ranked proposal.
                </p>
                <div className="readings" style={{ marginTop: 26 }}>
                  <div>
                    <span className="stamp">Suitability</span>
                    <div className="v">
                      {num(site.suitability_score, 1)}
                      <small>/100</small>
                    </div>
                    <div className="sub">
                      rank {site.rank} of {analysis?.candidate_sites.length ?? 1} candidates
                    </div>
                  </div>
                  <div>
                    <span className="stamp">Catchment</span>
                    <div className="v">{num(site.catchment.metrics.area_ha, 1)}<small>ha</small></div>
                    <div className="sub">
                      Tc {num(site.catchment.metrics.time_of_concentration_min ?? 0, 1)} min ·
                      relief {num(site.catchment.metrics.relief_m, 1)} m
                    </div>
                  </div>
                  <div>
                    <span className="stamp">Storage</span>
                    <div className="v">
                      {pond ? volume(pond.gross_capacity_m3) : "—"}
                    </div>
                    <div className="sub">
                      {pond ? `${num(pond.depth_m, 2)} m deep` : "no design was sized"}
                    </div>
                  </div>
                  <div>
                    <span className="stamp">Reliability</span>
                    <div className="v">
                      {balance?.months_with_water ?? "—"}
                      <small>/12 mo</small>
                    </div>
                    <div className="sub">
                      {balance ? "holds water through the year" : "water balance unavailable"}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <>
                <h2 style={{ maxWidth: "26ch" }}>Nothing has been analysed yet.</h2>
                <p style={{ color: "var(--ink-2)", marginTop: 14, maxWidth: "58ch" }}>
                  Upload a contour survey in the workspace and this block reports that run — the
                  ranked site, the area draining to it, the storage the ground will give and how many
                  months of the year it holds water. The figures are read from the analysis, never
                  written into the page.
                </p>
                <p style={{ marginTop: 22 }}>
                  <Link className="act" to="/workspace">
                    Open the workspace
                  </Link>
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
