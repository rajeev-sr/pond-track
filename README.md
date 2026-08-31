# Contour — AI-based Village Pond Planning System

Upload a **contour map** of a village and get back where a pond should go, how
much land drains to it, how much runoff that catchment yields, and how deep and
how large the pond should be — as structured JSON, on an interactive map-ready
GeoJSON footing.

```bash
cp .env.example .env
docker compose up -d
./scripts/demo_contour.sh          # analyses the bundled sample map
```

Then open **http://localhost:8000/docs**.

No API key is needed. Runs locally; nothing is deployed.

---

## What it does

`POST /api/v1/analyzeContour` takes a KML/KMZ contour map and returns, in one
response:

| | |
|---|---|
| **What was read from the file** | where the elevations were found, line and vertex counts, the derived contour interval, extent, and working CRS |
| **Interpolated terrain** | grid resolution and how it was derived, depression filling, flow accumulation |
| **Ranked pond sites** | each with a per-criterion score breakdown, so a recommendation can be defended |
| **The catchment for each site** | polygon, area, relief, slope, longest flow path, time of concentration, form factor |
| **Runoff** | composite Curve Number from soil and land cover, SCS-CN annual and 75 %-dependable volume |
| **Pond design** | depth, plan dimensions, capacity, cost, and **which constraint bound the answer** |

### On the bundled sample

```
elevation found in     placemark_name        (not the z ordinate — see below)
contour lines          1,355 (159,113 vertices), 32 levels @ 1.0 m, 267–298 m
working CRS            EPSG:32644            derived from the centroid
grid resolution        5.0 m                 derived from mean contour spacing

#1  channel_position   score 81.3/100  at 81.29545, 21.25114
    CATCHMENT   187.5 ha | relief 16.7 m | Tc 65.0 min | flow path 2,870 m
    RUNOFF      CN 83.7 → 480,958 m³/yr (C = 0.195), design 75 % = 309,828 m³
    POND        4.5 m deep, 141 × 141 m → 81,682 m³ gross, 73,514 m³ live
                binding constraint: practical_excavation_depth
```

Every number above is derived from the uploaded file and its location. Nothing
about this particular map is in the code.

---

## Why it generalises

A contour KML is a container, not a schema, and the one thing that must never be
assumed is **where the elevation lives**. The bundled sample stores it in
`<Placemark><name>` with 2-D coordinates; other exports use the z ordinate, or
`ExtendedData`, or only the folder name. The parser tries all four in priority
order and **reports which one succeeded**.

The test suite emits the *same synthetic terrain* four times, elevation stored a
different way each time, and asserts an identical catchment area — through the
HTTP API, not just the parser.

Derived from the input, never assumed: contour interval, extent, working UTM
zone, grid resolution, and the monsoon window.

---

## Architecture in one idea

**Elevation is an abstract source, not a fixed dataset.** A contour upload and a
remote DEM tile are interchangeable implementations of one protocol, both
yielding a metric DEM raster:

```
contour KML/KMZ ─┐
                 ├─► DemGrid ─► fill depressions ─► D8 flow ─► accumulation
remote DEM COG ──┘              (Priority-Flood+ε)      │
                                                        ├─► catchment delineation
                                                        ├─► pond siting
                                                        └─► runoff & pond design
```

Everything below `DemGrid` is written once and never learns where the elevation
came from. Adding a terrain input costs one adapter, not a second pipeline.

```
backend/app/
  api/v1/          HTTP only — no domain logic
  services/        domain logic — no FastAPI imports
    interpolate.py   contours → DEM (TIN, hull-clipped, de-terraced)
    hydrology.py     Priority-Flood + ε, D8, accumulation, catchment, morphometrics
    siting.py        AHP-weighted overlay, region aggregation, DBSCAN, ranking
    runoff.py        composite Curve Number, SCS-CN
    pond.py          stage–storage, prismoidal geometry, depth choice
    enrichment.py    concurrent provider fetch behind a deadline
  providers/       external adapters, each with a declared fallback
  core/            crs.py (CRSGuard), units.py, errors.py (RFC 7807), logging.py
```

That boundary is enforced: `services/` must never import FastAPI, and `api/`
must never contain domain maths.

---

## Village search

`make seed` loads the village index for a state. Two sources, because no single
open one covers both halves: **names** from the SHRUG SHRID→LGD crosswalk on
Harvard Dataverse (CC0, 596,390 villages and towns all-India with full
hierarchy), **polygons** from geoBoundaries gbOpen (ODbL, down to sub-district).

Village *geometry* is not seeded — no keyless source has it. Each village links
to its containing sub-district and `boundary_level` says so, so a 662 km² tehsil
outline can never be mistaken for a village boundary.

Search folds transliteration variance on both sides
([`core/names.py`](backend/app/core/names.py)) — the HLD's CH-24 answer:

| typed | finds | similarity |
|---|---|---|
| `kutelabhata` | `kutelabhatha` | 1.00 |
| `Kutelabhaata` | `kutelabhatha` | 1.00 |
| `कुटेलाभाठा` | `kutelabhatha` | 1.00 |
| `सिरसा` | `sirsa` | 1.00 |

Devanagari transliterates with positional schwa deletion, which is what makes
रामपुर → `rampur` while कमल stays `kamal`. Names that collide under folding but
are different places — `Balod` and `Baloda` are two Chhattisgarh sub-districts —
are matched exactly first, so folding never merges them in a join.

---

## Data sources — all keyless, no API key exists

| Layer | Source | Access |
|---|---|---|
| Elevation | your contour map, or Copernicus DEM GLO-30 | upload / public S3 COG |
| Land cover | ESA WorldCover 10 m | public S3 COG, windowed |
| Soil → Hydrologic Soil Group | SoilGrids (ISRIC) | keyless REST |
| Rainfall + ET₀ | Open-Meteo archive, ~30 yr daily | keyless REST |

Every credential in `.env.example` is **optional** and unlocks an enrichment.
With none of them set, the full analysis still runs.

### Degradation is a defined ladder, not a failure

| Tier | Available | What you get |
|---|---|---|
| `full` | terrain + soil + land cover + rainfall | everything measured |
| `no_soil_lulc` | terrain + rainfall | runoff on a stated assumed soil group |
| `terrain_only` | terrain alone | pond site, catchment area, stage–storage capacity |

Each layer is fetched independently within a 20 s budget. A provider outage drops
the tier and appears in `provider_failures` — it never fails the request.
`terrain_only` works with the network unplugged.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/analyzeContour` | Upload a contour map → sites, catchments, runoff, pond design |
| `POST` | `/api/v1/findCatchment` | Alias of the above |
| `POST` | `/api/v1/analysis` | The same analysis as a background job → `202` + `job_id` |
| `GET` | `/api/v1/analysis/{job_id}/status` | State, weighted percentage, per-step outcomes |
| `GET` | `/api/v1/analysis/{job_id}/result` | The finished document — served for `partial` too |
| `DELETE` | `/api/v1/analysis/{job_id}` | Abandon a job and drop its record |
| `POST` | `/api/v1/terrain/contour-map` | Parse and interpolate only → `dem_id` |
| `GET` | `/api/v1/terrain/contour-map/{dem_id}/contours` | Echo the parsed contours as GeoJSON |
| `POST` | `/api/v1/terrain/derivatives` | Slope + shaded relief as COG tile templates |
| `POST` | `/api/v1/terrain/contours` | Regenerate contours at any interval, with index lines |
| `POST` | `/api/v1/hydrology/streams` | Drainage network with Strahler order + drainage density |
| `POST` | `/api/v1/hydrology/catchment` | Delineate the catchment above any point |
| `POST` | `/api/v1/land/available` | Parcels a pond could be dug on, with an audit of what was excluded |
| `POST` | `/api/v1/land/cadastral` | Upload a tenure layer; reports which sites are allottable |
| `GET` | `/api/v1/suitability/weights` | The AHP weights, and the consistency audit that justifies them |
| `POST` | `/api/v1/suitability/weights/ahp` | Pairwise matrix → weights; refuses `CR ≥ 0.1` with `400` |
| `POST` | `/api/v1/suitability/analyze` | Run an analysis with your own weights |
| `GET` | `/api/v1/suitability/{job_id}/sites` | Ranked sites with per-criterion contributions |
| `GET` | `/api/v1/suitability/{job_id}/compare` | Two to five sites side by side, with the trade-offs named |
| `POST` | `/api/v1/reports/generate` | Render a finished analysis as a PDF |
| `GET` | `/api/v1/reports/{report_id}/download` | Download that PDF |
| `GET` | `/api/v1/export/{job_id}?format=geojson` | Every result layer as one GeoJSON file |
| `GET` | `/api/v1/villages/search?q=` | Fuzzy village search, Latin or Devanagari |
| `GET` | `/api/v1/villages/{village_id}` | Village metadata |
| `GET` | `/api/v1/villages/{village_id}/boundary` | Labelled polygon — says what it actually outlines |
| `GET` | `/api/v1/villages/{village_id}/imagery` | Satellite tile template + attribution |
| `GET` | `/api/v1/villages/resolve?lon=&lat=` | Reverse-geocode a point |
| `GET` | `/api/v1/health` | Liveness — never touches a backing service |
| `GET` | `/api/v1/health/ready` | Readiness, plus which optional layers are configured |
| `GET` | `/docs` · `/openapi.json` | Interactive docs, with a real captured response |

### Requirement → endpoint

Which endpoint satisfies which functional requirement, and what is honestly not
built. `[~]` means the capability is delivered and tested but folded into the
analysis document rather than given the standalone route the design catalogued.

| | Requirement | Endpoint | Status |
|---|---|---|---|
| FR-1 | Satellite imagery for a village | `/villages/{id}/imagery` | `[x]` |
| FR-2 | Contour maps | `/terrain/contours`, `/terrain/derivatives` | `[x]` |
| FR-3 | Land suitable for excavation | `/land/available` | `[x]` |
| FR-4 | Catchment area | `/hydrology/catchment` | `[x]` |
| FR-5 | Historical rainfall (≥30 y) | inside `/analyzeContour` → `environment.rainfall` | `[~]` |
| FR-6 | Runoff volume (SCS-CN) | inside `/analyzeContour` → `site.runoff` | `[~]` |
| FR-7 | Pond depth and capacity | inside `/analyzeContour` → `site.pond` | `[~]` |
| FR-8 | Overlay all results | the map UI — 11 toggleable layers | `[x]` |
| FR-9 | Ranked candidate sites | `/suitability/{job_id}/sites` | `[x]` |
| FR-10 | Save/load + export | `/export/{job_id}`, `/reports/generate` — export yes, projects no | `[~]` |
| FR-11 | Cadastral upload | `/land/cadastral` | `[x]` |
| FR-12 | Compare two sites | `/suitability/{job_id}/compare` | `[x]` |
| FR-13 | Water-balance simulation | inside `/analyzeContour` → `site.pond.water_balance` | `[~]` |
| FR-14 | Natural-language explanation | inside `/analyzeContour` → `explanation` | `[x]` |
| **FR-15** | **Accept a contour map as input** | `/terrain/contour-map` | `[x]` |
| **FR-16** | **Contour map → catchment, end to end** | `/analyzeContour` | `[x]` |

Full reference with worked `curl` calls: **[docs/API.md](docs/API.md)**.

---

## The map interface

`http://localhost:8080` — drop a KML or KMZ on the page and the analysis renders
on a map: the survey extent, the contours read out of the file, the catchment
that drains to the chosen site, and the ranked candidate sites. Click a site to
redraw its catchment.

The **drainage network** is drawn inside the recommended site's catchment, line
width scaled by Strahler order, with the density and highest order reported
alongside. It is the overlay that shows *why* a site scores what it does — a
channel-position site sits at the outlet of the network you can see.

![The drainage network inside the recommended catchment, with Strahler-weighted line widths](docs/images/ui-streams.png)

### While it runs

A cold analysis is about 24 seconds, 20 of them waiting on external providers.
The bar is weighted by each step's *measured* share of that time, so it does not
race to 57 % and then sit still — the step strip is drawn to the same weights, so
the long wait is visible before it happens.

![Analysis in progress, holding at 11 % during the provider fetch](docs/images/ui-progress.png)

### What falls, and what the ground holds

Rainfall normals with the monsoon window picked out, the runoff figures derived
from them, and the stage–storage curve for the selected site. The curve stops
where terrain stops containing the water: past that point the storage is not an
impoundment, and saying so is more useful than extending the line.

![Monthly rainfall, runoff tiles and the stage-storage curve](docs/images/ui-water-balance.png)

### Where you are allowed to dig

Buildable parcels after buildings, roads, water and steep ground are subtracted,
with an audit of which rule removed what. On this sheet the terrain does most of
the work: slope at the 5 % threshold removes 49 % of cells, land cover another
26 %, and the OSM exclusions about 24 % of what remains.

![Available-land parcels drawn over the survey extent](docs/images/ui-available-land.png)

**Click anywhere on the map** to delineate the catchment above that point, drawn
apart from the analysis' own so the two are never confused. Three clicks give
three visibly different catchments — which is how you check the flow routing is
doing something rather than taking it on trust.

![A clicked catchment in lime, distinct from the analysis' own in cyan](docs/images/ui-click-catchment.png)

**Find a village** searches the seeded index as you type, in Latin or
Devanagari, and frames the map on what it finds. The outline is drawn **dashed**
whenever it is the containing sub-district rather than the village itself —
which, absent an open source of Indian village polygons, is every time. The
panel says so in words as well. Where two villages share a name *and* a place,
the suggestion shows the Gram Panchayat that separates them.

![Village search: `kutelabhata` typed, `Kutelabhatha` found, Durg tehsil outlined dashed](docs/images/ui-village-search.png)

- **Satellite basemap by default** (Esri World Imagery), street map as the
  alternative. Siting a pond is mostly a question of what is already on the
  ground, and the catchment outline can be checked by eye against visible
  drainage.
- **Every figure is sourced.** The panel opens with which tier the answer came
  from, and names any provider that failed and why — a blank field is never left
  to look like a zero.
- **Indian conventions throughout**: lakh/crore digit grouping, ₹ costs, areas in
  hectares below a square kilometre.
- **Strings are externalised** to `frontend/src/i18n/en.json`, so Hindi or a
  regional language is a catalogue away rather than a rewrite (HLD NFR-15).

React 18 + TypeScript + Vite + MapLibre GL, served by nginx, which also
reverse-proxies the API so the browser sees a single origin.

```bash
make ui-dev        # Vite dev server on :5173, proxying to a running API
make ui            # production bundle
```

---

## Running it

**Requirements:** Docker with Compose v2, ~4 GB RAM, ~10 GB disk.

```bash
cp .env.example .env
docker compose up -d              # api + postgis + redis + titiler + frontend
curl localhost:8000/api/v1/health
```

Then open **<http://localhost:8080>** and drop a KML on the page.

If a host port is already taken, override it in `.env`
(`API_HOST_PORT`, `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `FRONTEND_HOST_PORT`).

**Analyse a map:**

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'file=@contours_1m.kml' \
  -F 'max_sites=3'
```

**Terrain only, no network:**

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'file=@contours_1m.kml' -F 'enrich=false'
```

Installation detail and troubleshooting: **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Development

```bash
make venv          # local virtualenv for the test suite
make screen        # measure candidate test villages against the siting criteria
make test          # 478 tests, offline, ~30 s
make lint          # ruff + black
make typecheck     # mypy on the domain layer
make ui-check      # tsc --noEmit on the frontend
make test-e2e      # browser test against the running stack
```

The suite runs with **no network**: the live-provider tests are marked `network`
and deselected by default (`pytest -m network` to include them). `make test-e2e`
drives a real Chrome and skips — never fails — when the stack is not up or no
browser is installed.

### Rainfall comes from two sources

Open-Meteo (ERA5-Land, 0.1°) and NASA POWER (MERRA-2, 0.5 × 0.625°), fetched
concurrently. Either alone produces a runoff estimate, so one being rate-limited
no longer drops the analysis to terrain-only — and the spread between them, about
15 % over the sample location, is reported as the uncertainty rather than hidden
behind a single number.

The two **daily** series are never averaged. SCS-CN is non-linear in daily depth
and two reanalyses put the same storm on different days, so blending them would
split one 100 mm storm into two 50 mm ones and systematically understate runoff.

### The runoff figure is cross-checked, not asserted

SCS-CN is a US model (HLD CH-15), so every runoff estimate is compared against
formulae fitted on Indian gauged catchments — Inglis & DeSouza (1929), Khosla
(1960), Barlow (1912) — with the region deciding which apply. Where they agree the
estimate is worth more; where they diverge the spread is reported as the honest
uncertainty rather than resolved by picking a favourite.

Strange (1928) is *not* implemented and says so: it is a tabulation, not a
formula, and values written from memory presented as a cross-check would lend
false confidence to the number being checked.

### How correctness is established

Verification is anchored to worked arithmetic and to surfaces whose answers are
known in closed form, not to snapshots of current behaviour.

- **Contours round-trip through the DEM.** `services.interpolate` and
  `services.contours` are inverses, so regenerating the input interval and
  comparing against the surveyed lines tests the interpolation in a way neither
  half can alone: median residual **0.109 m** on a 1 m interval.
- **`docs/HLD.md` §6.9** works the whole method through by hand. The unit tests
  reproduce it: `S = 69.98 mm`, `Ia = 20.99 mm`, `Q(60 mm) = 13.96 mm`,
  prismoidal `60 × 45 × 3.5 m → 7,647.6 m³`.
- **Synthetic DEMs with exact expected cell counts** — tilted plane, inverted
  cone, V-valley, twin basins split by a ridge. On an inverted cone every cell
  drains to the centre, so the catchment is the whole surface: a value, not a
  tolerance.
- **Property tests** — volume monotonic in depth, runoff monotonic in rainfall,
  catchment area monotonic downstream, area invariant under CRS round-trip.
- **A diagonal-weighting test that can actually fail.** On `z = -(row + 0.2·col)`
  the weighted steepest descent is due south while the *unweighted* comparison
  picks south-east, so the assertion discriminates — a naive 45° plane would pass
  either way.

Two bugs those tests caught, both of which produced plausible-looking output:
inverted neighbour-offset signs (a D8 grid that routed water backwards), and
depression filling to *exactly* the spill elevation, which left 17,924 interior
cells tied with their own outflow and fragmented drainage. Fixed by
Priority-Flood + ε (Barnes, Lehman & Mulla 2014).

---

## Documents

| | |
|---|---|
| [docs/HLD.md](docs/HLD.md) | High-level design: architecture, algorithms, worked numerical example, data-source strategy, challenges |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Phased build plan with effort estimates and a live tracker |
| [docs/API.md](docs/API.md) | API reference with worked `curl` examples |
| [docs/INSTALL.md](docs/INSTALL.md) | Installation guide |
| [docs/REPORT_CONTOUR_API.md](docs/REPORT_CONTOUR_API.md) | Report for the contour-API phase |

`python3 progress.py` prints build progress and the next startable tasks.

---

## Limitations

Stated plainly, because a design document that hides them is less useful.

- **Interpolated terrain is a model.** TIN interpolation between contours is
  exact *at* the contours and linear between them. Inside the innermost closed
  contour there are no data points, so a flat plateau appears — the response
  reports depression filling so this is visible rather than silent.
- **Land ownership is not verified.** Land cover says a parcel is *physically*
  plausible; it says nothing about who owns it. Output is "physically suitable —
  ownership to be verified against revenue records".
- **Rainfall is reanalysis, ~11–25 km.** Fine for screening. For a submitted
  scheme, cross-check against IMD's 0.25° gauge-based grid — the response carries
  this caveat in `data_caveat`.
- **No groundwater measurement.** Depth is capped by practical excavation, not by
  the water table, unless a figure is supplied. Cutting into a shallow table turns
  a storage pond into a seepage pit; the response says so.
- **SCS-CN is an empirical model.** Applied with `Ia = 0.3 S` (Indian practice,
  not the US 0.2 S), to the daily series rather than annual totals, with
  antecedent moisture classified per day.
- **Screening, not a detailed survey.** These are candidates to investigate, not
  a construction drawing.
