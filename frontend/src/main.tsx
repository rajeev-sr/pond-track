import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { Brief } from "./routes/Brief";
import { Method } from "./routes/Method";
import { Reference } from "./routes/Reference";
import { Workspace } from "./routes/Workspace";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";

/** Real paths rather than hashes: nginx already falls back to index.html
 *  (`try_files $uri $uri/ /index.html`) and the dev server does the same, so a
 *  deep link like /method is served the app and resolved client-side. None of
 *  these collide with the API's own routes — /api, /docs, /redoc, /openapi.json,
 *  /tiles and /ws are all proxied past the SPA. */
const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Brief /> },
      { path: "workspace", element: <Workspace /> },
      { path: "method", element: <Method /> },
      { path: "reference", element: <Reference /> },
      // Anything else is a typo, not a page: show the brief rather than a blank.
      { path: "*", element: <Brief /> },
    ],
  },
]);

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
