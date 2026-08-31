const SECTIONS = [
  { id: "surface", n: 1, h: "Surface" },
  { id: "flow", n: 2, h: "Flow" },
  { id: "exclusions", n: 3, h: "Exclusions" },
  { id: "ranking", n: 4, h: "Ranking" },
  { id: "yield", n: 5, h: "Yield" },
  { id: "design", n: 6, h: "Design" },
  { id: "limits", n: 7, h: "Limitations" },
];

/**
 * How a proposal is arrived at.
 *
 * Static prose on purpose: it describes the method, not a run, so it must read
 * the same whether or not anything has been analysed — and it has to be
 * printable, because it is the part a reviewer takes away. The one place figures
 * appear is the AHP consistency ratio, which is a property of the weight matrix
 * in the code rather than of any sheet.
 */
export function Method() {
  return (
    <div className="sheet method">
      <nav className="index" aria-label="Contents">
        <span className="stamp">Contents</span>
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`}>
            {s.n} · {s.h}
          </a>
        ))}
        <div style={{ marginTop: 18 }}>
          <button
            type="button"
            className="act line"
            style={{ padding: "8px 13px" }}
            onClick={() => window.print()}
          >
            Print
          </button>
        </div>
      </nav>

      <div className="prose">
        <span className="stamp">Method</span>
        <h1 style={{ fontSize: 40, marginTop: 12 }}>How the proposal is arrived at</h1>
        <p style={{ fontSize: 16.5 }}>
          Six stages, each a published method. The intermediate artefact of every stage is available
          through the API, so a reviewer can check the surface, the flow network or the ranking
          independently rather than accepting the final figure.
        </p>

        <h2 id="surface">1 · Surface</h2>
        <p>
          Contour polylines carry elevation; the ground between them does not exist in the file.
          Vertices are triangulated and the surface interpolated linearly within each triangle,
          giving a regular grid whose cell size follows contour spacing.
        </p>
        <p>
          The working coordinate system is derived from the sheet&rsquo;s own longitude, so slope,
          area and distance are computed in metres. Nothing about the location is fixed in the code:
          a sheet is resolved to whichever UTM zone it falls in, in either hemisphere.
        </p>

        <h2 id="flow">2 · Flow</h2>
        <p>
          Closed depressions are raised to their spill level by Priority-Flood with an ε gradient, so
          water still has a descending path across filled ground. Where a depression is an artefact
          of a road or bund crossing a channel, a bounded least-cost breach is cut instead of
          filling.
        </p>
        <p>
          Flow direction follows the eight-neighbour steepest descent. Accumulation is traced in
          dependency order so every cell is visited once. A cell becomes a channel above a
          contributing-area threshold — 1 ha by default, deliberately small, because a nala draining
          a few hectares is precisely where a check dam belongs.
        </p>
        <div className="eq">
          Tc = 0.01947 · L<sup>0.77</sup> · S<sup>−0.385</sup>
          <span className="whence">
            Time of concentration, Kirpich (1940). L is the longest flow path in metres, S its
            average gradient.
          </span>
        </div>
        <p className="src">Barnes, Lehman &amp; Mulla (2014) · Lindsay (2016) · Strahler (1957)</p>

        <h2 id="exclusions">3 · Exclusions</h2>
        <p>
          Some ground cannot hold a pond however well it scores, and the veto is applied to the
          buildable mask <em>before</em> ranking, so excluded ground is never proposed and then
          withdrawn. Each standoff follows from what the hazard does to a structure.
        </p>
        <table className="svy">
          <thead>
            <tr>
              <th>Feature</th>
              <th className="n">Standoff</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Existing tank</td>
              <td className="n">0 m</td>
              <td>Its bank is sound ground; only the water is barred.</td>
            </tr>
            <tr>
              <td>River, canal</td>
              <td className="n">50 m</td>
              <td>A village pond cannot impound a river, and the land beside one floods.</td>
            </tr>
            <tr>
              <td>Building</td>
              <td className="n">50 m</td>
              <td>Impoundment against a dwelling is a safety matter.</td>
            </tr>
            <tr>
              <td>Road</td>
              <td className="n">20 m</td>
              <td>The embankment and its borrow area need the room.</td>
            </tr>
            <tr>
              <td>Stream, drain, ditch</td>
              <td className="n">none</td>
              <td>Never excluded — a check dam belongs on one.</td>
            </tr>
          </tbody>
        </table>
        <p>
          A river is read from both conventions used to map it: the centreline, and the areal water
          body which carries no waterway tag at all. Reading only the first classified a large river
          as standing water and left its bank open to proposals.
        </p>

        <h2 id="ranking">4 · Ranking</h2>
        <p>
          Nine criteria, weighted by the Analytic Hierarchy Process. Weights are the principal
          eigenvector of the pairwise comparison matrix, and the matrix is tested for consistency
          before any weight is used. The weights themselves are reported with every run, under{" "}
          <span className="fignum">suitability.criteria_weights</span>.
        </p>
        <div className="eq">
          CI = (λ<sub>max</sub> − n) / (n − 1)&nbsp;&nbsp;&nbsp;CR = CI / RI(n)
          <span className="whence">
            For the nine criteria in use: λ<sub>max</sub> 9.1058 · CI 0.0132 · RI(9) 1.45 → CR
            0.0091, against Saaty&rsquo;s admissible limit of 0.10.
          </span>
        </div>
        <p className="src">
          Saaty (1980), <em>The Analytic Hierarchy Process</em>
        </p>

        <h2 id="yield">5 · Yield</h2>
        <p>
          Runoff follows SCS-CN with the initial abstraction taken at 0.3S rather than the textbook
          0.2S, following Central Water Commission and IMD practice for Indian catchments. The curve
          number comes from hydrologic soil group and land cover, adjusted for antecedent moisture.
        </p>
        <div className="eq">
          Q = (P − 0.3S)² / (P + 0.7S)&nbsp;&nbsp;&nbsp;S = 25400/CN − 254
          <span className="whence">P and Q in mm. Q is zero for P ≤ 0.3S.</span>
        </div>
        <p>
          A single empirical formula should not be trusted alone, so the result is cross-checked
          against four independent Indian relations — Inglis–DeSouza (1929), Khosla (1960), Barlow
          (1912) and the Rational method. Design yield is the 75 % dependable value from the annual
          series, which is the basis used for minor irrigation works.
        </p>

        <h2 id="design">6 · Design</h2>
        <p>
          The stage–storage relation is built by flood-filling the conditioned surface level by
          level. Volume between two levels uses the prismoidal rule, exact for a linearly varying
          cross-section:
        </p>
        <div className="eq">
          V = (d/3) · ( A<sub>top</sub> + A<sub>bottom</sub> + √(A<sub>top</sub> · A
          <sub>bottom</sub>) )
          <span className="whence">
            d is the level increment; A the plan area impounded at each level.
          </span>
        </div>
        <p>
          Design depth is set by whichever constraint binds first — practical excavation depth,
          available land, sustainable share of catchment yield, or side-slope closure — and the
          report names it. A depth quoted without its binding constraint invites the wrong next
          question.
        </p>
        <p>
          A monthly balance then subtracts open-water evaporation and seepage by soil group from
          inflow, over a three-year spin-up, and reports how many months the pond holds water above
          dead storage.
        </p>

        <h2 id="limits">7 · Limitations</h2>
        <ul className="bul">
          <li>
            <b>The surface is interpolated, not surveyed.</b> A hollow smaller than the contour
            interval cannot appear in it at all.
          </li>
          <li>
            <b>Flow is routed to a single neighbour.</b> On genuinely flat ground this gives parallel
            flow lines where real water spreads.
          </li>
          <li>
            <b>A catchment clipped by the sheet edge is a lower bound</b>, and is reported as such
            rather than as a measurement.
          </li>
          <li>
            <b>Yield is a design estimate.</b> SCS-CN is an accepted method, not a gauge reading; the
            cross-checks bound it, they do not calibrate it.
          </li>
          <li>
            <b>Tenure is outside the model.</b> Ownership must be confirmed from the cadastral
            record.
          </li>
        </ul>
      </div>
    </div>
  );
}
