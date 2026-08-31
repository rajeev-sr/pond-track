const INTERFACES = [
  {
    href: "/docs",
    h: "API documentation",
    p: "Every endpoint with schemas and examples, and a request builder that runs against this instance.",
    go: "Open Swagger UI ↗",
  },
  {
    href: "/redoc",
    h: "Reference reader",
    p: "The same specification laid out for reading rather than for calling.",
    go: "Open ReDoc ↗",
  },
  {
    href: "/openapi.json",
    h: "Raw specification",
    p: "The machine-readable OpenAPI document, for generating a client.",
    go: "openapi.json ↗",
  },
];

const ROUTES = [
  ["POST /api/v1/analyzeContour", "Ranked sites, catchments and designs — the whole analysis"],
  ["POST /api/v1/findCatchment", "The same, under the name the brief uses"],
  ["POST /api/v1/terrain/contour-map", "Parsed sheet and interpolated surface, without siting"],
  ["POST /api/v1/terrain/derivatives", "Slope and shaded relief as tiled rasters"],
  ["POST /api/v1/hydrology/streams", "Drainage network, clipped to a catchment or whole-sheet"],
  ["POST /api/v1/hydrology/catchment", "The catchment above an arbitrary point"],
  ["POST /api/v1/land/available", "Buildable parcels after exclusions"],
  ["POST /api/v1/suitability/weights/ahp", "Weights from a pairwise matrix, with consistency ratio"],
  ["POST /api/v1/reports/generate", "The drawing, as PDF"],
];

const SOURCES = [
  ["Elevation", "Copernicus GLO-30", "Copernicus"],
  ["Land cover", "ESA WorldCover 10 m", "CC BY 4.0"],
  ["Soil", "ISRIC SoilGrids", "CC BY 4.0"],
  ["Features", "OpenStreetMap via Overpass", "ODbL"],
  ["Rainfall", "ERA5-Land · NASA POWER", "Open"],
  ["Boundaries", "SHRUG · geoBoundaries", "CC0 · ODbL"],
];

/** Where to find the interfaces and what the data is. The API links are plain
 *  anchors, not routes: /docs, /redoc and /openapi.json are the API's own pages,
 *  proxied past the SPA by both nginx and the dev server. */
export function Reference() {
  return (
    <div className="sheet">
      <div className="blk" style={{ borderTop: 0 }}>
        <div className="mgn" style={{ paddingTop: 44 }}>
          <div className="no">A</div>
          <span className="stamp">Interfaces</span>
        </div>
        <div className="bdy" style={{ paddingTop: 44 }}>
          <h2>Reference</h2>
          <p style={{ color: "var(--ink-2)", marginTop: 12, maxWidth: "58ch" }}>
            Every stage of the analysis is reachable on its own, so the surface, the flow network,
            the catchment or the ranking can be taken and checked separately.
          </p>
          <div className="refgrid" style={{ marginTop: 26 }}>
            {INTERFACES.map((i) => (
              <a key={i.href} href={i.href}>
                <h3>{i.h}</h3>
                <p>{i.p}</p>
                <span className="go">{i.go}</span>
              </a>
            ))}
          </div>
        </div>
      </div>

      <div className="blk">
        <div className="mgn">
          <div className="no">B</div>
          <span className="stamp">Endpoints</span>
        </div>
        <div className="bdy">
          <h3 style={{ marginBottom: 16 }}>Principal routes</h3>
          <table className="svy">
            <thead>
              <tr>
                <th>Route</th>
                <th>Returns</th>
              </tr>
            </thead>
            <tbody>
              {ROUTES.map(([route, returns]) => (
                <tr key={route}>
                  <td className="fignum" style={{ fontSize: 12.5, whiteSpace: "nowrap" }}>
                    {route}
                  </td>
                  <td>{returns}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="blk">
        <div className="mgn">
          <div className="no">C</div>
          <span className="stamp">Sources</span>
        </div>
        <div className="bdy" style={{ paddingBottom: 60 }}>
          <h3 style={{ marginBottom: 16 }}>Data and attribution</h3>
          <p style={{ color: "var(--ink-2)", marginBottom: 18, maxWidth: "58ch" }}>
            Elevation for a run comes from the contour sheet you upload. The layers below supply
            everything the terrain cannot say — what is already on the ground, what the soil does
            with rain, and how much rain there is.
          </p>
          <table className="svy">
            <thead>
              <tr>
                <th>Layer</th>
                <th>Source</th>
                <th>Licence</th>
              </tr>
            </thead>
            <tbody>
              {SOURCES.map(([layer, source, licence]) => (
                <tr key={layer}>
                  <td>{layer}</td>
                  <td>{source}</td>
                  <td>{licence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
