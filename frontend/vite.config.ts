import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies the API so the browser sees one origin, matching how
// nginx serves it in the container. Without this, `npm run dev` would need CORS
// rules that production does not use -- two behaviours to keep in step.
//
// The list must mirror `nginx.conf`'s `location` blocks, and a missing entry does
// not 404 -- it falls through to the SPA and returns `index.html` with HTTP 200.
// `/docs` was proxied while `/openapi.json` was not, so FastAPI's Swagger page
// loaded, fetched the spec, got HTML back, and reported "the provided definition
// does not specify a valid version field". A 200 of the wrong content type is a
// much worse failure than a 404, which is why every one of these is listed
// explicitly rather than left to a catch-all.
const API = "http://localhost:8000";
// TiTiler is published on 8001 on the host; inside compose nginx reaches it at
// titiler:8000. Same service, different address either side of the network.
const TILER = "http://localhost:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/docs": { target: API, changeOrigin: true },
      "/redoc": { target: API, changeOrigin: true },
      "/openapi.json": { target: API, changeOrigin: true },
      "/ws": { target: API, changeOrigin: true, ws: true },
      "/tiles": {
        target: TILER,
        changeOrigin: true,
        // nginx strips the prefix (`rewrite ^/tiles/(.*)$ /$1`), so the dev
        // server has to as well or every tile 404s on a doubled path.
        rewrite: (path) => path.replace(/^\/tiles/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        // MapLibre is ~800 kB on its own and changes far less often than the
        // app. Splitting it lets the shell render while the map engine loads,
        // and keeps it cached across deploys of our own code.
        manualChunks: { maplibre: ["maplibre-gl"] },
      },
    },
  },
});
