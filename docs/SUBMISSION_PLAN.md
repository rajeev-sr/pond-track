# Contour — implementation plan for the final submission

Order of work: **build → test on Rajeev's machine → deploy on stu79 over SSH (last)**.
Tick items as they land and add a line to the change log at the end.

### Decisions (2026-09-26)

| Question | Decision |
|---|---|
| Selection shape | **Rectangle only** — drag on the map; the API takes a bounding box |
| Area cap | **100 km²** (`MAX_AOI_KM2`, already the default) |
| Scaling | **All four systems** behind one gateway |
| Serving the web UI | **From the API process** — every system runs one process type |
| Dependencies on stu79 | **micromamba** for Postgres, Redis and WeasyPrint's pango/cairo, installed in `$HOME` without sudo |

---

## 1. Requirements and gaps

| # | Final-submission requirement | Status | Gap |
|---|---|---|---|
| 1 | A fully working front-end | Partial | Slope and shaded-relief layers blank without a tile server |
| 2 | Option to select the land area on a map | **Missing** | Only KML upload; the map click delineates one point's catchment, it does not select an area |
| 3 | Results generated from the selected area | **Missing** | No API route accepts an area |
| 4 | Results include pond location, catchment area, expected water volume | Partial | All three exist but are buried in 195 KB of JSON — no summary block, and nothing is named "expected water volume". Water balance reports "unavailable" on a `full`-tier run on the deploy |
| 5 | All three overlaid and visualised on the map | Partial | Location and catchment are drawn; area and volume only appear in the side panel |
| 6 | Fast and functional; stress, scaling and limits across the four systems | **Missing** | One process, no load test, no overload guard |

**Why 2 and 3 are wiring, not new science.** The design (ADR-7) made a contour upload and a remote DEM
interchangeable `ElevationSource` implementations. `providers/elevation/copernicus_aws.fetch_dem(bounds)`
already returns terrain for any bounding box as the same `DemGrid` the KML path produces, and
`tests/golden/test_source_interchangeability.py` proves the pipeline is source-agnostic. Nothing calls it yet.

---

## 2. Phase 0 — Local setup

- [x] ~~`make venv`~~ — PyPI is unreachable from this machine, so tests, lint and mypy run in the dev image
      `contour-api:latest` with the repo mounted at its own layout (same paths as the host)
- [x] `make lint && make typecheck && make test` green before any change (baseline: 1276 passed, 51 skipped)
- [ ] `make ui-dev` on :5173 against a local API on :8000
- [x] Confirm this machine reaches `https://copernicus-dem-30m.s3.amazonaws.com` (Phase 1 depends on it)

---

## 3. Phase 1 — Backend: analyse a selected area

### API contract

```http
POST /api/v1/analyzeArea            (synchronous — for API testers)
POST /api/v1/analysis/area          (asynchronous — 202 + job_id, used by the UI's progress bar)
Content-Type: application/json

{
  "bbox": [81.28, 21.24, 81.31, 21.26],   // [min_lon, min_lat, max_lon, max_lat]
  "max_sites": 5,          // optional, default 5
  "max_slope_pct": 8.0,    // optional
  "enrich": true           // optional
}
```

- `bbox` is the only required field — a rectangle, as decided; easy to type in Postman.
- Limits: 0.1 km² to 100 km²; longitude −180…180, latitude −90…90, min < max on both axes.
- Response: **the same shape as `/analyzeContour`**, so the existing UI, export and report render it unchanged.
- Adds a `terrain_source` block to *both* routes: provider, dataset, resolution, bounds.
  `contour_map` stays on KML runs (the grader and the report read it).
- Errors: inverted, zero-size or out-of-range box, or below 0.1 km² → 400; above 100 km² → 413
  (`AoiTooLargeProblem` already exists); Copernicus unreachable → 503 naming it.

### Changes by file

| File | Change |
|---|---|
| `services/contour_analysis.py` | Split `analyze_contour_map` into `analyze_dem(dem, source, opts)` (condition → flow → enrichment → siting → catchments) and two front doors: `analyze_contour_map` (parse + interpolate) and `analyze_area(bbox, opts)` (fetch COP30) |
| `services/contour_analysis.py` | `ContourAnalysis` stops assuming `ParsedContours`: bounds, summary and contour lines come from the source |
| `services/area.py` (new) | Bounding-box validation, geodesic area in km², the rectangle as a mask on the DEM grid |
| `services/siting.py` | Cells outside the rectangle join the `excluded` mask — sites are proposed only inside the drawn area |
| `providers/elevation/copernicus_aws.py` | Disk cache for fetched windows keyed by rounded bounds (today every call re-reads S3) |
| `services/contours.py` | Reuse `generate()` so area runs still return display contours |
| `api/v1/contour.py`, `api/v1/analysis.py` | The two routes above |
| `schemas/contour.py` | `AnalyzeAreaRequest`, `terrain_source` block, `summary` block |

### Rules to hold to

- Catchments use the **buffered** DEM (`AOI_BUFFER_M` = 500 m): water flows in from beyond the drawn line.
- Analyse at Copernicus's **native 30 m**. At 30 m, 25 km² is 40 000 cells with the buffer and 100 km² is
  134 000 — well under the KML sheet's 342 550 — so memory is not what limits the area; fetch time is.
- State the resolution in the response and the UI: a 141 m pond is ~5 cells across at 30 m.
- Every analysis response — KML and area — gains a top-level **`summary`** block with exactly the three
  required results for the recommended site: `pond_location` (lat, lon), `catchment_area_ha` and
  `expected_water_volume_m3` with its definition. A grader reading the JSON should not have to dig.
- `area_of_interest` (already a top-level GeoJSON polygon) carries the drawn rectangle on area runs.
- Edges: no suitable site inside the area → 200 with zero sites and the reason; an area over the sea or
  outside Copernicus coverage → 422; outside India → a warning, still analysed.

### Tests

- [x] Unit: box validation (inverted, zero-size, out of range, below 0.1 km², above 100 km²), area maths, mask
- [x] Unit: no proposed site falls outside the rectangle
- [x] Integration: both routes with a mocked DEM provider — no network in CI
- [x] Golden: a synthetic area run agrees with the KML pipeline on shared stages
- [x] `network`-marked: one live Copernicus fetch over the Durg sample area
- [x] Validate the 30 m path against the 5 m KML result over the same extent — distance between the two
      recommended sites and the catchment-area difference — and quote it in the report
- [x] `docs/API.md` documents both new routes: `test_api_docs_drift` fails without it, and API.md is graded

### Phase 1 — done (2026-09-26)

What was built, where it differs from the table above, and what was measured.

- **Routes**: `POST /api/v1/analyzeArea` (sync) and `POST /api/v1/analysis/area` (job, steps
  `terrain → condition → flow_routing → enrichment → siting → catchments`). A bad box is refused before any
  fetch or job: 400 (inverted, zero-size, out of range, malformed, below 0.1 km²), 413 over 100 km²
  (with `area_km2` and `max_km2`), **422 over open sea** (Copernicus has no tile there — "draw the
  rectangle over land"), 503 when Copernicus cannot be reached.
- **Siting held inside the rectangle** from `contour_analysis._analyse` (the rectangle joins the `excluded`
  mask passed to siting), so `services/siting.py` is unchanged and the exclusion audit still reports only
  hazards.
- **Every response** — KML and area — now has `summary` (pond location, catchment area, expected water
  volume = min(live storage, 75 % dependable inflow), with its basis) and `terrain_source`. `contour_map` is
  null on area runs.
- **DEM disk cache**: a box is read from S3 once, then from `COG_STORE_PATH/dem/` (rounded to ~1 m).
- **Copernicus coverage fix**: the grid overhung the fetched window by up to 1.5 cells, so a complete
  tile reported 96.8 % coverage with an empty rim in the buffer. The window is now read two cells wider:
  100 %.
- **`dem_id` follow-ups** (streams, catchment at a point, derivatives, contours, land) work on area runs;
  the contours route traces display contours from the grid when there were no uploaded lines.
- **PDF report** names the selected area and Copernicus as the terrain source, and states the 30 m
  limitation instead of the contour-interval one. GeoJSON export works on an area job.
- **Tests**: 95 new — 93 offline (unit, integration, golden) and 2 live `network` tests (the sample extent and an
  Arabian Sea box). Full offline suite: 1358 passed; the one failure was the test runner's mount, fixed.

**30 m against 5 m, over the sample sheet's own extent** (`scripts/compare_area_vs_contour.py`, enrichment
off on both sides so only the terrain differs):

| | Contour upload (5 m) | Drawn area (Copernicus 30 m) |
|---|---|---|
| Grid | 650 × 527 | 143 × 123 (incl. 500 m buffer) |
| Elevation / relief | 267.0–298.0 m / 31.0 m | 269.0–299.1 m / 30.1 m |
| Run time (this machine) | 3.6–3.9 s | 4.6–5.0 s cold (4.8 s is the S3 read), 0.1–0.2 s warm |
| Recommended site | 21.24735, 81.28995 · score 84.0 · 264 ha | 21.25079, 81.29621 · score 67.5 · 270 ha |

Figures as re-measured after the rim-coverage fix (`docs/report/assets/compare-30m-5m.json`, written by
`scripts/compare_area_vs_contour.py --json`).

- Elevation agreement over 3,327 points: **r = 0.905**, a systematic offset of −5.0 m (a different vertical
  reference or source model; routing depends only on relative height), residual spread 2.3 m.
- The nearest 30 m candidate is **432 m** from the upload's #1 (343 m before the rim-coverage fix filled the
  grid's edge cells), and catchment areas differ markedly —
  expected when each cell covers 36 times the ground. So the report and UI must say it plainly: **the drawn
  area is a screening tool for any village; the contour upload is the higher-fidelity path** where a
  survey exists.

---

## 4. Phase 2 — Frontend: select the area

### Behaviour

- Job sheet gets a mode switch: **Upload contour map · Draw area on map**.
- **Rectangle**: press and drag; release to finish. Escape cancels. Drawing again replaces it.
- Live readout of the area in km² while drawing; Run disables with a reason above the cap.
- The drawn outline stays on the map after analysis and can be edited or cleared.
- In draw mode map clicks draw; otherwise click-to-delineate works as it does now.
- **Try the sample area** button: frames the Durg sample and pre-draws a rectangle, so a grader can run it
  in one click without knowing where to draw.
- Errors shown before Run, mirroring the server: below 0.1 km², above 100 km².

### Changes by file

| File | Change |
|---|---|
| `components/workspace/JobSheet.tsx` | Mode switch; area readout; Run submits either mode |
| `components/draw/AreaDraw.ts` (new) | Rectangle tool on MapLibre's own events — no new dependency |
| `components/MapView.tsx` | Drawn-area source and layer; hand clicks to the draw tool in draw mode |
| `state/analysis.tsx` | `analyseArea(bbox)` beside `analyse(file)`; the same job polling |
| `api/client.ts`, `api/types.ts` | `analyzeAreaAsJob`, `AnalyzeAreaRequest`, `terrain_source` |
| `MapView.tsx:597`, `workspace/TitleBlock.tsx:13`, `workspace/JobSheet.tsx:28`, `routes/Brief.tsx:208` | These read `contour_map` unguarded. An area run has none, and the `MapView` one frames the map — it would crash the map. Frame and read back from `area_of_interest` and `terrain_source` instead |
| `routes/Brief.tsx`, `routes/Method.tsx`, `routes/Reference.tsx` | Describe both inputs — today they say "contour sheet · KML / KMZ" only — and the Copernicus source with its 30 m resolution |

### Tests

- [x] Browser: draw a rectangle → run → five sites, all inside the rectangle (a real CDP mouse drag; the run
      uses the sample area so the site check is deterministic)
- [x] Browser: area readout updates while dragging; over 100 km² blocks Run with a message
- [x] Browser: draw mode does not trigger click-to-delineate; leaving draw mode restores it
- [x] Browser: the sample-area button runs end to end
- [x] Browser: an area run renders the map, title block and read-back with no `contour_map`
- [ ] Existing 74 browser tests stay green

---

## 5. Phase 3 — Results overlaid on the map

| Result | On the map |
|---|---|
| Pond location | Site marker labelled with its volume: `#1 · 73,514 m³` (recommended site emphasised) |
| Catchment area | Label at the catchment's centre: `180 ha` |
| Expected water volume | Click a site → popup: location, catchment area, **collectable** volume (live storage) and **annual inflow** (mean, and 75 % dependable) |

- Labels stay DOM markers, as the rank labels already are — a MapLibre `text-field` would need a glyph server.
- Collectable volume = the smaller of live storage and dependable inflow; on the sample that is 73,514 m³
  against 326,365 m³/yr dependable inflow.
- [x] Browser: the selected site's marker shows its volume (`#1 · 72,743 m³`), every other marker carries it in
      its tooltip; the selected catchment is labelled `90 ha catchment`; clicking a marker opens a popup with
      location, catchment, water collected and inflow. Changed from "every marker": five long labels collided
      on sites 200 m apart, and all five read the same figure when every pond is capped the same way
- [x] Legend entries for the drawn area and the volume labels
- [x] Never total the inflow across sites: catchments nest, so a sum double-counts. Per-site `expected_water`
      on every candidate, and the recommended site's `summary`
- [x] PDF report and GeoJSON export work on an area run — the report now names the area and the
      Copernicus source and states the 30 m limitation (done with Phase 1)
- [ ] ~~(optional) Marker size scaled by collectable volume~~ — dropped: the volumes are usually equal (every
      pond hits the same depth and footprint caps), so size would carry no information

---

## 6. Phase 4 — Performance, stress and scaling

Designed for the real limits of each stu79 system: **512 MB memory, 1 CPU** (measured). One analysis
peaks at roughly 250–430 MB, so **one analysis at a time per system** is the ceiling.

### Changes

- [x] **Overload guard** — `MAX_CONCURRENT_ANALYSES` (default 1) per process. Synchronous routes answer
      `503` + `Retry-After` when full; jobs wait in `queued` until a slot frees; at most 8 jobs wait
- [x] **`dem_id` eviction bug** — with `DEM_CACHE_LIMIT=1`, a second user's upload evicts the first user's
      terrain and their follow-up calls 404. Every grid is now written to disk as it is registered and read
      back on a miss (LRU in memory, 200 on disk); a `dem_id` also survives a restart
- [x] **Serve the built frontend** (`npm run build`) instead of the Vite dev server — saves a Node process
      per system and loads faster (`FRONTEND_DIST`; hashed assets cached a year, `index.html` revalidated)
- [x] **Gateway** — nginx, `deploy/nginx/gateway.conf.in`, rendered by `deploy/render_gateway.py`. Affinity
      by browser: the UI sends `X-Client-Id` (and `?client=` on the export link); `hash … consistent`, falling
      back to the full client address. **Not `ip_hash`**: it keys on the first three octets, which would send
      every grader on one lab subnet to the same instance
- [x] **Auto-restart** — `deploy/supervise.sh <name> <cmd>`: restarts on exit, doubling backoff to 60 s for a
      process that dies at once, logs every start and exit code, stops cleanly on SIGTERM
- [x] **Gateway health checks** — passive (`max_fails=2 fail_timeout=20s`, retry another instance on a
      failed connect). Tested: an instance killed mid-session — its user moved to another instance within
      3 s and back after the restart at 9.6 s; **0 of 18 requests failed**
- [x] Serve `frontend/dist` from the API process (decided), so every system runs one process type and the
      gateway proxies all paths with affinity
- [x] **Measure** peak memory per analysis for both routes — see *Measured* below
- [x] External-API pressure: Overpass refused every new window over 25 km² (HTTP 504 from all three
      mirrors), and each run waited the whole 20 s enrichment budget, warm or not. Now one request per window
      at a time (single-flight), and a refused window is remembered for 10 min, so a repeat run takes ~3 s.
      A late Overpass answer is still cached when it arrives
- [x] Limits stated in the UI (Reference → Limits; the job sheet; the queued progress text) — report: Phase 6

### Targets (one stu79 system, written down before measuring)

Baseline measured on the deployed 1-CPU system: the KML run takes **13.7 s with enrichment warm**
(parse 1.8 s, interpolate 4.7 s, condition 1.8 s, catchments 2.4 s).

| Run | Warm | Cold |
|---|---|---|
| KML sample sheet (342 550 cells) | ≤ 15 s — no regression | ≤ 30 s |
| Drawn area, 25 km² — a typical village (~40 000 cells) | ≤ 8 s | ≤ 30 s |
| Drawn area, 100 km² — the cap (~134 000 cells) | ≤ 15 s | ≤ 45 s |
| Four concurrent runs across four systems | p95 ≤ 1.5 × a single run | — |

### Local emulation of the four systems (before touching stu79)

- [x] `deploy/lab/compose.yml`: four API replicas, each 512 MB with no swap and one CPU, own cache dir,
      behind the real gateway config (hash on the client id — `ip_hash` rejected, see above)
- [x] `scripts/loadtest.py` — places users deliberately (single, one per instance, two per instance, all on
      one); sync or job mode; area or contour; p50/p95, error mix, follow-up success, peak memory
- [x] Show three results: the guard turns overload into 503s instead of an OOM kill; four replicas
      serve ~4× one; follow-up calls keep working under load — all three, below

### Measured (2026-09-26, local emulation: 512 MB, 1 CPU per system)

One analysis per fresh container, enrichment on:

| Run | Idle | Peak | Cold | Warm |
|---|---|---|---|---|
| Contour sample sheet (342 550 cells) | 164 MB | 309–361 MB | 23.7 s¹ | 5.3 s (through the gateway) |
| Drawn area 8.5 km² | 163–208 MB | 256–259 MB | 11.4 s | 2.2 s |
| Drawn area 25 km² | 163 MB | 209–259 MB | 20.3 s¹ | 2.7 s |
| Drawn area 100 km² | 163 MB | 221–232 MB | 21.0–22.0 s¹ | 3.9 s |

¹ Mostly the enrichment budget, spent waiting for Overpass (15.7–20 s); the terrain stages at 100 km² take 0.9 s.
Against the targets: contour warm ≤ 15 s ✓, cold ≤ 30 s ✓; 25 km² warm ≤ 8 s ✓, cold ≤ 30 s ✓;
100 km² warm ≤ 15 s ✓, cold ≤ 45 s ✓.

Four instances behind the gateway, warm caches:

| Scenario | Users | Mode | OK | 503 | p50 | p95 | Follow-ups |
|---|---|---|---|---|---|---|---|
| single | 1 | job | 2/2 | 0 | 3.0 s | 3.0 s | 2/2 |
| one per instance | 4 | job | 8/8 | 0 | 3.0 s | 3.0 s — 1.0 × single ✓ | 8/8 |
| two per instance | 8 | job | 16/16 | 0 | 4.0 s | 5.0 s (queued) | 16/16 |
| all on one instance | 4 | job | 8/8 | 0 | 7.0 s | 8.1 s (queued) | 8/8 |
| two per instance | 8 | sync | 8 | 8 | — | — | 8/8 |
| contour, one per instance | 4 | sync | 4/4 | 0 | 6.1 s | 6.2 s — 1.1 × single ✓ | 4/4 |

No OOM kill in any run. Sync overload is a 503 with `Retry-After`; job overload queues.

**Memory found and fixed**: after a mixed workload an instance kept ~390 MB (idle is ~165 MB) and peaked at
444 MB — too close to 512 MB. GDAL sizes its block cache from the *host's* RAM, and glibc keeps a malloc arena
per thread: `GDAL_CACHEMAX=64` and `MALLOC_ARENA_MAX=2` brought the same workload's peak to 358–369 MB. The
in-memory job store also kept every result for 24 h without bound; now the 50 most recent finished jobs.

---

## 7. Phase 5 — Fixes

- [x] Water balance "unavailable at a degraded tier" on a `full` run — ET0 came only from Open-Meteo, which
      rate-limits (HTTP 429) often; NASA POWER then won and carried none. POWER is now asked for daily Tmax/Tmin
      and ET0 computed by Hargreaves (FAO-56 eq. 52; radiation checked against FAO-56 Example 8). Live: a run
      with Open-Meteo at 429 now has ET0 1,780 mm/yr and a 12-month water balance. The message no longer
      blames the tier
- [x] Slope and shaded relief without a tile server — `GET /terrain/{dem_id}/overlays` and
      `GET /terrain/{dem_id}/overlay/{product}`: one PNG per layer, pinned by its four corners as a MapLibre
      image source; cached per `dem_id`; relief exaggerated 4× as the tiled layer was
- [ ] `make seed STATE=chhattisgarh` on the deploy so village search returns results
- [x] Any control whose backend dependency is down (village search, PDF export) is hidden or disabled
      with a reason, driven by `/health/ready` — never a button that fails. Village search is hidden when the
      database is unreachable, and the API answers 503 "database unavailable" instead of a bare 500. The UI
      has no PDF button, so there is nothing there to hide

---

## 8. Local test plan — definition of done

| Check | Command | Pass |
|---|---|---|
| Lint, types | `make lint && make typecheck` | clean |
| Unit + integration | `make test` | all green, new tests included |
| Golden | `pytest -m slow backend/app/tests/golden` | green |
| Browser | `CONTOUR_FRONTEND_URL=http://localhost:5173 pytest backend/app/tests/e2e` | green, new draw/overlay tests included |
| Area demo | Draw over the Durg sample area in the UI | 5 sites inside the area; volume and area on the map |
| KML demo | `POST /api/v1/analyzeContour` with `contour_map` only | 200, unchanged output |
| Load | `scripts/loadtest.py` against the 4×512 MB emulation | numbers recorded for the report |
| Area route | `curl -X POST .../api/v1/analyzeArea -H "Content-Type: application/json" -d @area.json` | 200; `summary` holds all three results |
| Sample button | Click Try the sample area → Run | results in ≤ target, sites inside the rectangle |
| Report on area run | Generate PDF and GeoJSON export for an area job | both download |
| Auto-restart | `kill -9` the API process | serving again within 10 s |
| Failover | Stop one of the four replicas mid-load | requests still served; no 5xx beyond the guard's 503s |
| Docs drift | `pytest backend/app/tests/integration/test_api_docs_drift.py` | green — API.md lists the new routes |

---

## 9. Phase 6 — Report

- [x] Area selection: request, response and screenshots — report §5, from `area.json` and two screenshots
- [x] The lab architecture (gateway on sys2, three API systems; sys1 unreachable) and the load-test numbers —
      report §6, from `loadtest-*.json` and `failover.json`, measured on the lab systems
- [x] Accuracy of the 30 m path against the 5 m KML result (from the Phase 1 validation) — report §5.2, from
      `compare-30m-5m.json`
- [x] Capacity and limits: concurrency, area cap, upload cap, behaviour under overload — report §6.3
- [x] Refreshed from the stu79 deployment (2026-09-27): `analysis.json`, `area.json`, `openapi.json` (34
      operations), `ready.json` and `endpoints.json` (19 routes, from `scripts/capture_endpoints.py`, now the
      source of the report's endpoint table) requested from sys3 through the public URL; load tests and
      failover run on the lab systems with memory sampled on each; figures 1–6 taken from the live site.
      `captures.json` names each source and the report prints it under its section
- [x] `docs/API.md` and `README.md`: the area routes, the four-system architecture, how to run
- [ ] Confirm whether the final submission also asks for a demo video; record one if so

---

## 10. Deployment on stu79 over SSH — done (2026-09-27)

**Live at <http://10.1.75.53:3274>** — the web application, the API under `/api/v1`, and its
documentation at `/docs`, all through one gateway.

### Port mapping — measured

Each system listens on its *internal* ports 3000, 4000, 5000, 6000 and 7000; the lab host publishes
them on 10.1.75.53 at `internal + 272 + n` for system n. The rule first written here (listen on the
public number itself) was wrong: nothing listening on 3274 inside sys2 is reachable from outside.

| System | Internal address | SSH | Published → internal (measured on sys2, same rule for the others) |
|---|---|---|---|
| stu79_sys1 | 172.17.0.74 | 2273 | 3273→3000 · 4273→4000 · 5273→5000 · 6273→6000 · 7273→7000 |
| stu79_sys2 | 172.17.0.75 | 2274 | 3274→3000 · 4274→4000 · 5274→5000 · 6274→6000 · 7274→7000 |
| stu79_sys3 | 172.17.0.76 | 2275 | 3275→3000 · 4275→4000 · 5275→5000 · 6275→6000 · 7275→7000 |
| stu79_sys4 | 172.17.0.77 | 2276 | 3276→3000 · 4276→4000 · 5276→5000 · 6276→6000 · 7276→7000 |

The systems reach each other directly on 172.17.0.x at any port, so the gateway talks to the APIs,
and the APIs to Postgres, on the lab network; only the published ports are reachable from outside.

### Roles

| System | Runs | Memory, 512 MB limit |
|---|---|---|
| sys1 | Nothing of ours — unreachable since 2026-09-26 (SSH 2273 and :3273 time out). Its old chatfat balancer still asks sys2:3000 for `/healthz` every second; the gateway answers 200 and nothing else happens | — |
| sys2 | **Gateway** (nginx, internal :3000 → **3274**) + API instance (:4000) | API ~250 MB idle |
| sys3 | API instance (:4000) | API ~250 MB idle |
| sys4 | API instance (:4000) + PostgreSQL 16.4 / PostGIS 3.4.2 (:5432, lab network only) — village register and rainfall cache | peak 513 MB (cgroup) during two analyses; no new OOM kill |

The gateway hashes on the browser's `X-Client-Id` (then `?client=`, then the address), so a user's
follow-ups reach the system holding their terrain; a system that stops answering is skipped for 20 s.

### How it runs, per system

- Code at `~/contour-app` (the tested tree, the built UI in `frontend/dist`, Python in `.venv`);
  nginx, PostgreSQL/PostGIS and WeasyPrint's pango from the conda environment `~/mamba/envs/infra`,
  installed offline from packages solved on the laptop (`micromamba create --offline -f explicit-local.txt`).
- Settings in `~/contour.env` (mode 600; holds the database password, never committed); logs in
  `~/contour-logs/`.
- tmux session `contour`: window `api` runs `deploy/supervise.sh api deploy/run-api.sh 4000`, restarted
  with backoff if it dies. sys2 adds window `gateway`
  (`supervise gateway nginx -c ~/contour-run/nginx.conf -g "daemon off;"`), sys4 adds `postgres`.
- Rolling update: copy the changed files, then `pkill -f "[u]vicorn app.main:app"` on one system at a
  time. The supervisor has it back in 12–17 s and the gateway routes around it meanwhile.
- Keep SSH connections few and reused (`ControlMaster`); a burst of reconnects looks like brute force
  to the lab firewall.

### Data kept on the lab systems — their internet is unreliable

Measured from sys4 on 2026-09-27:

| Service | From the lab | What is done about it |
|---|---|---|
| Copernicus DEM (S3) | no connection within 20 s | The 26 tiles covering Chhattisgarh (+5 km) kept on each system, 1.08 GB each; read from disk, S3 only outside them |
| NASA POWER | TLS connect failed after 22 s (one success the day before) | 30-year daily series (1996–2025, with Hargreaves ET0) for the 65 POWER cells over Chhattisgarh loaded into sys4's database: 712,270 days, 219 MB |
| Open-Meteo | HTTP 429 — the campus address is over the free quota, from the laptop too; the refusal took up to 68 s to arrive | Not asked again for an hour after a refusal; the ensemble returns at its deadline with the source that answered |
| SoilGrids | no connection within 20 s | Best effort; without it, hydrologic soil group C is assumed and the response says so |
| ESA WorldCover (S3) | 44 s to connect | Best effort; cached for the sample area |
| Overpass (OSM) | 5 s to connect | Best effort; cached for the sample area |

Also in sys4's database: the Chhattisgarh village register (19,715 villages, 7,595 areas, 11,532 gram
panchayats) behind village search.

To redo the staging (a reset lab, another state): `scripts/stage_region.py --iso IN-CT --tiles-out <dir>
--power` in the API environment on the laptop; its docstring gives the copy to the lab. The tiles went to
sys2 once (82 s) and on to sys3 and sys4 over the lab network.

Before the rainfall fix every drawn area came back `terrain_only`: the cached POWER series was ready in
1.8 s, but the ensemble waited for Open-Meteo's 68 s refusal and missed the 20 s enrichment deadline.
Measured on sys4 after it:

| Area | Answer | Time | Layers |
|---|---|---|---|
| Sample rectangle | 5 sites, catchment 89.9 ha, 68,203 m³ | 2.4 s (1.0 s warm) | full: terrain, soil, land cover, rainfall (NASA POWER), OSM |
| New rectangle near Bilaspur | 5 sites, catchment 500.9 ha, 72,743 m³ | 21 s | terrain, soil, rainfall |
| New rectangle near Jagdalpur | 5 sites, catchment 447.3 ha, 72,743 m³ | 21 s | terrain, rainfall |

72,743 m³ is the live storage of the largest pond the design allows; those catchments deliver 5–12 times
that, and the response says `limited_by: storage`.

### Deploy checklist

| Step | sys1 | sys2 | sys3 | sys4 |
|---|---|---|---|---|
| Old processes stopped | unreachable | ✓ chatfat node | ✓ chatfat node | ✓ chatfat node and its Postgres (data kept in `~/pgdata`) |
| Code at the tested tree | — | ✓ | ✓ | ✓ |
| Python and conda environments | — | ✓ | ✓ | ✓ |
| API instance under the restart loop | — | ✓ | ✓ | ✓ |
| Gateway + UI on 3274 | — | ✓ | — | — |
| Terrain tiles, warm caches | — | ✓ | ✓ | ✓ |
| Database: villages + rainfall | — | — | — | ✓ |
| Verified (section 11) | — | ✓ | ✓ | ✓ |

- [ ] **Cutover (Rajeev)**: update the Google form to `http://10.1.75.53:3274/api/v1/analyzeContour`. The
      old :3272 is not ours and no longer answers
- [ ] **Commit and push (Rajeev)** so the repository matches the deployed tree

### Network note

From the laptop on campus Wi-Fi (10.50.x), 40–60 % of new TCP connections to 10.1.75.53 time out, on
every port including SSH, in stretches of about 10 s. From inside the lab the same public address
answers 10 of 10. The service is not the cause; a browser retries the handshake, so a user on that path
sees a delay rather than an error.

---

## 11. Submission gate — what the grader sees

Run on the deployed URL from outside the lab network. Every row must pass before the form is updated.
Results on 2026-09-27 against `http://10.1.75.53:3274`:

| # | Requirement | Pass when | Result |
|---|---|---|---|
| 1 | Fully working front-end | `http://10.1.75.53:3274` loads; upload, draw, run, every layer toggle, export and village search work or are hidden with a reason; no console errors | ✓ `scripts/verify_deployment.py` 15/15 from the laptop and from inside the lab; browser suite against the live site 88 passed on the first run, the 4 misses were the laptop's dropped connections in plain-Python checks (now retried; area 18/18 and routing 16/16 on rerun); no console errors |
| 2 | Select the land area on a map | Rectangle draw with a live km² readout; Try the sample area works | ✓ browser suite; figures 4–5 of the report taken from the live site |
| 3 | Results from the selected area | Run completes within the Phase 4 targets; every proposed site lies inside the drawn area | ✓ sample area 0.7–1.0 s warm, full tier; 5/5 sites inside; new rectangles near Bilaspur and Jagdalpur 21 s (terrain + rainfall) |
| 4 | Pond location, catchment area, expected water volume | The JSON `summary` names all three; the UI shows all three | ✓ |
| 5 | Overlaid on the map | Volume label on each site, area label on the catchment, popup with all three, pond footprint and catchment outline drawn | ✓ |
| 6 | Fast; stress, scaling and limits | Measured table meets the targets; systems behind the gateway; overload gives 503 or queues, never a crash; one system down still serves; limits stated in the report | ✓ three systems (sys1 unreachable): 6 users queued 12/12, direct overload 503; failover 0/44 failed; peak 403 MB, no OOM kill |
| — | Earlier brief still holds | `POST /api/v1/analyzeContour` with only `contour_map` returns 200 and the same output | ✓ 200 in 11 s warm |
| — | Graded documents | `docs/API.md`, `README.md` and the report updated; repository link and form URL correct | ✓ documents; **form URL and the push are Rajeev's** |

---

## 12. Risks and open decisions

| Item | Detail | Owner |
|---|---|---|
| Copernicus reachability | **Happened**: the lab cannot reach S3. The 26 tiles covering Chhattisgarh are on each system's disk; a rectangle outside them still needs S3 and will fail from the lab | resolved for Chhattisgarh |
| Memory | 512 MB per system; one analysis at a time. Load beyond three concurrent analyses is queued or refused, not served. Measured peak 403 MB (a contour upload), no OOM kill | measured |
| Gateway on stu79 | nginx from conda-forge in `$HOME`, under the restart loop, hashing on the browser id | done |
| Lab disk | One disk shared by every container on the host: 6.6 GB free (98 %) after the tiles went on | watch |
| sys1 | Unreachable over SSH and on :3273 since 2026-09-26; three systems serve instead of four | lab admin |
| Large areas | 100 km² means more Overpass features and a larger WorldCover read; the 20 s enrichment budget degrades the tier rather than stalling | Phase 4 targets |
| External rate limits | Open-Meteo refuses the campus address (the lab and the laptop alike); NASA POWER, pre-loaded into the database, answers instead. Soil, land cover and OSM for new areas stay best-effort from the lab | accepted |

---

## 13. Change log

| Date | Change |
|---|---|
| 2026-09-27 | **Deployed properly on stu79** at <http://10.1.75.53:3274>: gateway on sys2, APIs on sys2–sys4, PostgreSQL/PostGIS on sys4; verified 15/15 from outside and inside, 19/19 routes, e2e against the live site; load and failover measured on the lab. Fixes found by deploying: (1) every drawn area came back `terrain_only` — the rainfall ensemble joined both sources' threads, so Open-Meteo's 68 s refusal held back the cached POWER series; it now returns at its deadline with whoever answered, remembers a refusal for an hour, learns it from a one-day probe and rechecks in the background; (2) the lab cannot reach S3, NASA POWER or SoilGrids, so Chhattisgarh's 26 terrain tiles and 30 years of POWER rainfall for its 65 cells were staged from the laptop; (3) `/land/available` read land cover from S3 with no deadline and the gateway answered 504 — it now uses the analysis's cache and the 20 s budget; (4) `/health/ready` said `degraded` without Redis, which the lab runs without on purpose — Redis unset is now `not_configured`; (5) every contour job paid a 6 s Celery ping — skipped with no broker, a failed ping trusted for 60 s; (6) `run-api.sh` re-reads `CONTOUR_ENV_FILE` at each start, so a settings change reaches the next restart |
| 2026-09-26 | Bug found in browser testing: with 11 sites on a 15.7 km² box, two natural depressions came back outside the rectangle. `siting._depression_regions` fell back to *any* cell of a depression with no feasible cell, so the hard veto — the drawn area's edge, and equally existing water, buildings, roads and steep ground — did not bind depressions. Now the fallback is buildable cells only, and a depression with none is dropped with a warning (13–16 were, on that box). The sample sheet keeps its five sites and #1; scores shift ~1 point because vetoed depressions no longer enter the relative scoring. 4 regression tests, one checked to fail on the old code |
| 2026-09-26 | Phase 6 drafted: report §5 (selected area, 30 m vs 5 m) and §6 (architecture, load, failover, limits) built from captures whose source each section names; README and API.md updated. Captures to be refreshed from stu79 after deployment |
| 2026-09-26 | Phase 5 done except seeding (a deploy step): ET0 from NASA POWER by Hargreaves, so the water balance survives an Open-Meteo 429; slope and relief as image overlays with no tile server; village search hidden without a database, 503 not 500 |
| 2026-09-26 | Phases 2–4 done: rectangle draw tool, sample-area button, per-site volumes, catchment label and popup on the map; export link fixed (it had 404'd on every run); overload guard, `dem_id` disk store, UI served by the API, gateway with per-browser affinity, restart loop, OSM single-flight and refusal cache, memory limits; four-system emulation load-tested |
| 2026-09-26 | Phase 1 done: both area routes, `summary` + `terrain_source` on every response, DEM disk cache, 422 over open sea, Copernicus rim-coverage fix, report wording for area runs; 95 new tests; 30 m vs 5 m measured (r = 0.905; nearest 30 m candidate 432 m from the 5 m #1) |
| 2026-09-26 | Decided: rectangle only, 100 km² cap, all four systems, UI served by the API, micromamba for dependencies. Implementation started |
| 2026-09-26 | Reviewed against the six criteria; 23 tasks added (28 → 51) plus a submission gate and measured targets — response `summary`, four unguarded `contour_map` reads that would crash an area run, sample-area button, page wording, API.md, auto-restart, gateway health checks, realistic targets from the 13.7 s baseline, submission gate, cutover |
| 2026-09-26 | Plan written. stu79 systems inventoried: 512 MB / 1 CPU each, chatfat processes still running on sys1, sys3, sys4 |
