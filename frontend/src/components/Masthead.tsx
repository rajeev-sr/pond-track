import { NavLink, Link } from "react-router-dom";

const PAGES = [
  { to: "/", label: "Brief" },
  { to: "/workspace", label: "Workspace" },
  { to: "/method", label: "Method" },
  { to: "/reference", label: "Reference" },
];

/** Toggle the paper/linen surfaces.
 *
 *  Written straight onto <html> rather than held in React state: the attribute is
 *  what every colour token keys off, and the whole point is that it applies before
 *  any component re-renders. */
function togglePlate() {
  const root = document.documentElement;
  root.setAttribute("data-plate", root.getAttribute("data-plate") === "linen" ? "paper" : "linen");
}

export function Masthead() {
  return (
    <header className="masthead">
      <Link className="mh-brand" to="/">
        <svg width="20" height="26" viewBox="0 0 20 26" fill="none" aria-hidden="true">
          <path d="M1 24c3.2-3.4 5.4-6.6 8-7.6" stroke="var(--contour)" strokeWidth="1.3" />
          <path d="M1 19.5c4-3.6 6.6-6.4 10-7.2" stroke="var(--contour)" strokeWidth="1.3" />
          <path d="M1 15c4.6-3.8 7.6-6.2 12-6.8" stroke="var(--contour)" strokeWidth="1.3" />
          <path d="M1 10.5c5-4 8.6-6 14-6.4" stroke="var(--contour)" strokeWidth="1.3" />
          <circle cx="12.5" cy="17.5" r="3" fill="var(--water)" />
        </svg>
        Contour
      </Link>
      <nav className="mh-nav" aria-label="Pages">
        {PAGES.map((p) => (
          <NavLink key={p.to} to={p.to} end={p.to === "/"}>
            {p.label}
          </NavLink>
        ))}
      </nav>
      <span className="mh-fill" />
      <span className="mh-act">
        {/* A full page load, not a route: /docs is the API's own page, proxied
            past the SPA by both nginx and the dev server. */}
        <a href="/docs">API docs ↗</a>
        <button type="button" onClick={togglePlate}>
          Paper / linen
        </button>
      </span>
    </header>
  );
}
