import { Link } from "react-router-dom";

/** Attribution and interface links, in the footer where a drawing carries them.
 *
 *  These used to be a panel in the controls sidebar, competing for space with the
 *  inputs. Licences have to be present — ODbL and CC BY require it — but they do
 *  not have to be in the way. */
export function Colophon() {
  return (
    <footer className="colophon">
      <div className="sheet">
        <div className="cols">
          <div>
            <span className="stamp">Contour</span>
            <p style={{ maxWidth: "36ch", color: "var(--ink-2)", fontSize: 13.5 }}>
              Pond siting and catchment assessment for Indian villages, from the contour sheet a
              Panchayat already has.
            </p>
          </div>
          <div>
            <span className="stamp">Pages</span>
            <Link to="/">Brief</Link>
            <Link to="/workspace">Workspace</Link>
            <Link to="/method">Method</Link>
            <Link to="/reference">Reference</Link>
          </div>
          <div>
            <span className="stamp">Interfaces</span>
            <a href="/docs">API documentation</a>
            <a href="/redoc">Reference reader</a>
            <a href="/openapi.json">openapi.json</a>
          </div>
          <div>
            <span className="stamp">Sources</span>
            <Link to="/reference">Data and licences</Link>
          </div>
        </div>
        <div className="foot">
          <span>
            Contains modified Copernicus data · © OpenStreetMap contributors, ODbL · ESA WorldCover,
            CC BY 4.0 · ISRIC SoilGrids, CC BY 4.0
          </span>
          <span className="fignum">CSD Assignment 1</span>
        </div>
      </div>
    </footer>
  );
}
