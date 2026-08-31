import { Outlet, useLocation } from "react-router-dom";

import { Colophon } from "./components/Colophon";
import { Masthead } from "./components/Masthead";
import { AnalysisProvider } from "./state/analysis";

/**
 * The shell: masthead, the routed page, and the colophon.
 *
 * `AnalysisProvider` sits above the router outlet so a completed run survives
 * navigating to the method page and back. The colophon is hidden on the
 * workspace, which is a full-height three-column bench with its own scroll
 * regions — a footer below it would push the map off the screen.
 */
export function App() {
  const workspace = useLocation().pathname.startsWith("/workspace");
  return (
    <AnalysisProvider>
      <div className="plate-app">
        <Masthead />
        <main>
          <Outlet />
        </main>
        {!workspace && <Colophon />}
      </div>
    </AnalysisProvider>
  );
}
