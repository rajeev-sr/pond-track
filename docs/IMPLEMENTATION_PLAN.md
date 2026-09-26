# Implementation Plan — AI-based Village Pond Planning System ("Contour")

> **Companion to [HLD.md](HLD.md).** The HLD is the *design*. This is the *build order, the effort
> budget, and the tracker*.

| | |
|---|---|
| **Team** | Solo |
| **Deadline** | None — sequenced by dependency and effort, not by calendar |
| **Total scope** | **~390 h** across 13 phases |
| **Submittable at** | MC (**~76 h**) for the contour-API phase · end of M7 (**~265 h**) for the full system |

**There are no dates in this document, deliberately.** Nothing here says "by Tuesday". Progress is
measured in phases completed and functional requirements verified. Work at whatever pace suits you;
the dependency graph (§6) tells you what is legal to start next, and the effort column tells you what
it will cost.

---

# 0. Status Board

> Update at the end of each working session. Everything else is reference material.

> **Do not hand-maintain this table.** Tick the `[ ]` boxes in the phase tables of §5, then run
> `python3 progress.py --update` and it rewrites the rollup from them. Run it with no flags for a
> read-only report plus the next startable tasks. Manually-kept summaries always drift out of sync
> with the detail; a derived one cannot.

## 0.1 Phase rollup

| Phase | Goal | Effort | Ring | Status | % |
|---|---|---|---|---|---|
| **M0** | Foundation — repo, Docker, DB, CI, keys | 35.3333 h | 1 | `[ ]` | 71 |
| **M1** | ★ Walking skeleton — end-to-end numbers | 20.5 h | 1 | `[x]` | 100 |
| **MC** | ★ **Contour-map catchment API — graded deliverable** | 57 h | 1 | `[x]` | 100 |
| **M2** | FR-1 + FR-2 — map, village, DEM, contours | 37.5 h | 1 | `[ ]` | 90 |
| **M3** | FR-4 — hydrology & catchment | 21.5 h | 1 | `[ ]` | 83 |
| **M4** | FR-5 + FR-6 — rainfall & runoff | 33 h | 1 | `[ ]` | 88 |
| **M5** | FR-3 + FR-7 — land availability & pond design | 25.5 h | 1 | `[ ]` | 0 |
| **M6** | FR-8 + FR-9 — orchestration & AHP suitability | 26.5 h | 1 | `[ ]` | 0 |
| **M7** | Deliverables — PDF, exports, docs, **report** | 32 h | 1 | `[ ]` | 0 |
| ══ | **↑ SUBMITTABLE HERE — ~207 h** | | | | |
| **M8** | India-native integration (ISRO Tier-1) | 30 h | 2 | `[ ]` | 0 |
| **M9** | ML suitability layer + explainability | 33.5 h | 2 | `[ ]` | 0 |
| **M10** | Full API surface (remaining 48 endpoints) | 39.5 h | 2 | `[ ]` | 0 |
| **M11** | Hardening — coverage, perf, observability | 22.5 h | 2 | `[ ]` | 0 |

## 0.2 Functional-requirement burn-down

**Legend.** `[x]` delivered · `[~]` delivered, but folded into the analysis
document rather than the dedicated endpoint the HLD catalogue lists · `[-]` not
applicable · `[ ]` not built.

Filled in during M7-9 against the running code, not from memory. Four
capabilities — rainfall statistics, runoff, pond design, soil — are computed and
tested but have no standalone route: they are returned inside
`POST /analyzeContour`, because the analysis is the unit of work and splitting
them would mean re-deriving the DEM per call. The capability is delivered; the
separate endpoint is not, and the report says so.

| FR | Requirement | Phase | Endpoint | Algorithm | Tested | On map |
|---|---|---|---|---|---|---|
| FR-1 | Satellite imagery for a village | M2 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-2 | Contour maps | M2 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-3 | Land suitable for excavation | M5 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-4 | Catchment area | M3 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-5 | Historical rainfall via public APIs | M4 | `[~]` | `[x]` | `[x]` | `[x]` |
| FR-6 | Runoff volume estimation | M4 | `[~]` | `[x]` | `[x]` | `[x]` |
| FR-7 | Pond depth + storage capacity | M5 | `[~]` | `[x]` | `[x]` | `[x]` |
| FR-8 | Overlay all results | M6 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-9 | Ranked candidate sites | M6/M9 | `[x]` | `[x]` | `[x]` | `[x]` |
| FR-10 | Save / load projects + export | M10 | `[~]` | `[~]` | `[x]` | `[-]` |
| FR-11 | Cadastral upload | M10 | `[x]` | `[x]` | `[x]` | `[ ]` |
| FR-12 | Compare two sites | M10 | `[x]` | `[x]` | `[x]` | `[ ]` |
| FR-13 | Water-balance simulation | M10 | `[~]` | `[~]` | `[~]` | `[ ]` |
| FR-14 | Natural-language explanation | M9 | `[x]` | `[x]` | `[x]` | `[ ]` |
| **FR-15** | **Accept contour map (KML/KMZ) as input** | **MC** | `[x]` | `[x]` | `[x]` | `[x]` |
| **FR-16** | **Contour map → catchment, end to end** | **MC** | `[x]` | `[x]` | `[x]` | `[x]` |

**Legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` dropped

## 0.3 Session Log

The cursor for a long solo project. **Fill in the top row before you stop working**, every time — it
is what lets you resume in two minutes instead of thirty after a gap.

| Date | Session | Stopped at | Next action | Notes / surprises |
|---|---|---|---|---|
| — | **M1 complete** — ADR-7 now demonstrated, not asserted | M1-1: `copernicus_aws.py` as a second `ElevationSource` | M2 (village search + map UI), or pause: the contour-API phase is deliverable | **478 offline + 4 network tests, all gates clean.** The protocol had one implementation, which proves nothing; now two. `test_source_interchangeability.py` runs the *same* downstream pipeline on a contour upload and on a raster source and asserts the invariants hold on both. Independent cross-validation on the sample: contour map relief **31.0 m (267–298)** against Copernicus GLO-30 **30.27 m (269–299.3)** over the same 8.5 km² — mutual corroboration that the parsing *and* the georeferencing are right. Tile naming is pinned by test so a bucket restructure fails loudly instead of silently returning empty rasters (risk R1). |
| — | **MC complete (24/24)** | MC-20..24: clean-clone check, README, demo script, OpenAPI example, phase report | Ring 1 continues at M2 (map UI) — or stop here, the contour-API phase is deliverable | **464 tests offline in 29 s, 87 % coverage, all gates clean.** Clean-clone verified: 93 files → `docker compose up -d` healthy in 1 s → migration → `./scripts/demo_contour.sh` → HTTP 200. **A `.gitignore` entry for `docs` was excluding the HLD, plan, API reference, install guide and the report from the repo** — every graded documentation deliverable — found by the clean-clone check, not by reading the file. `.github` was excluded too. Both restored, with a header naming what must never be ignored. The demo run also caught a live SoilGrids 503, so the degradation ladder is demonstrated rather than only tested. |
| — | MC-19 done: tier ladder wired in | Enrichment behind `full` / `no_soil_lulc` / `terrain_only` | MC-20/21/22/24: clean-clone check, README, demo script, phase report | **464 tests offline in 29 s**, 87 % coverage (runoff and enrichment at 100 %). Endpoint verified at tier **full** over HTTP: 6.4–11 s. Three defects fixed: the pipeline **blocked the event loop** (now off-thread), API tests silently started **hitting the network** (suite 30→90 s; now `enrich=false` by default with `network`-marked tests for the live path), and pond footprints were bounded by the *scoring cluster* instead of buildable land — `feasible` and `buildable` are now separate masks. Also: SoilGrids latency is erratic (0.9 s / 3.5 s / read-timeout minutes apart) and with retries stretched enrichment to **50 s**; a **20 s deadline** now degrades the tier instead of delaying the response. |
| — | Enrichment chain built | MC-12/16/17/18: streaming upload guard, three keyless providers, SCS-CN runoff, pond design | MC-19 tier ladder + wire into the endpoint | **Tier `full` reachable with zero credentials**: ESA WorldCover 10 m and Copernicus DEM are both public S3 range-request COGs; SoilGrids and Open-Meteo are keyless REST. Live on the sample: HSG **D** (clay 41.5 %), catchment cover 37.5 % cropland, composite **CN(II) 87.8**, rainfall **1313 mm** (CV 0.17, monsoon window *derived* Jun-Sep 88.9 %), runoff coefficient 0.256, **75 % dependable runoff 622,671 m³**. Two of my own bugs caught: `await file.read()` buffered the whole body *before* the size check (a DoS guard that did not guard), and `models=era5_land` returns 100 % nulls here while `None -> 0.0` coercion silently reported *no rain for 30 years* — now a model fallback chain plus a null-fraction guard, with the fallback recorded in `warnings`. |
| — | **`POST /analyzeContour` live** | MC-1/8/10/11/13/15 done: schemas, endpoints, orchestrator, geometry | MC-12 hardening, MC-16..19 enrichment, MC-21/22/24 report | **364 tests green, 95 % coverage.** Endpoint verified over HTTP against the real sample: **HTTP 200, 4.1 s, 264.27 ha catchment**, GeoJSON polygon, per-criterion explanation. Generalisation now proven *through the API*: same terrain with elevation in all four locations → identical catchment area. Two API bugs caught: `defusedxml` missing from the built image (requirements changed without a rebuild), and options bound to the **query string** so `curl -F max_sites=3` was silently ignored on a multipart endpoint — now explicit `Form()` fields, with a regression test on the OpenAPI schema. |
| — | MC-9 pond siting built | Region-aggregated terrain siting + ranked candidates | MC-1/MC-10/MC-11 (schemas + endpoints) | **333 tests green, 98 % coverage on siting.** Full pipeline KML→sites in **2.15 s**. Three defects found and fixed: (1) normalising over all valid cells made every candidate tie at 100/100; (2) slope taken from the *filled* surface reported 0 % at every site (real ground: 11.1 % vs 0.29 % filled); (3) point-sampling depth and accumulation at one cell missed that a depression's runoff passes through its **spill point**, not its centre — so candidates are now aggregated per **region** with a separate pour point. |
| — | Hydrology built (reuses M1-2/M1-3/M1-4) | Priority-Flood + D8 + accumulation + catchment + morphometrics | MC-9 terrain-only pond siting | **287 tests green, 96 % coverage on hydrology.** Sample → max accumulation **154,237 cells = 385.6 ha** (46 % of the 8.5 km² surface); fill 0.52 s, flow 0.23 s, catchment 0.19 s. Two real bugs caught by the analytic tests: (1) **inverted neighbour-offset signs** — `dc=+1` compared the *West* neighbour while labelling it East, producing a flow cycle; (2) filling to *exactly* the spill elevation left cells tied with their own outflow and 18,588 interior sinks — replaced with **Priority-Flood + ε** (Barnes 2014), which cut sinks to 189 (outlets only). |
| — | MC-7 built | Contour→DEM interpolation complete | M1-2/M1-3 hydrology (fill + D8 + flow acc) on the interpolated DEM | **241 tests green**. Sample → 5.0 m grid (650×527, 342 k cells) in **1.12 s**; resolution *derived* from `area / contour_length` = 14.96 m mean spacing. Relief preserved 31.0 → 30.99 m. Analytic tests pass: tilted plane interpolates linearly (15.0 m at the midpoint of the 10/20 contours), cone minimum at centre, elevation monotonic in radius, twin basins keep separate minima with a higher ridge between. |
| — | MC-2..MC-6 built | KML/KMZ parser complete + tested | MC-7 (TIN interpolation to DEM) | **205 tests green**, ruff/black/mypy clean, 90 % coverage on the parser. Sample: 1355 lines / 159,113 vertices in **0.14 s**, 0 unresolved, interval 1.0 m and UTM 44N both *derived*. All four elevation strategies verified to give bit-identical geometry. |
| — | Local-only confirmed | MC-20 changed from deploy to clean-clone verification | Start MC-2 (KML parser) | No hosting in scope; report cites the localhost URL plus run steps. |
| — | MC scope completed | MC expanded 17→24 tasks | Start MC-2 (KML parser) | Closed three audit gaps: no enrichment/runoff/pond-sizing (now MC-16..19), no repo-presentation or demo-script task (MC-21/22), no OpenAPI examples (MC-23). MC now covers every graded item — see the phase's exit-criteria table. |
| — | New requirement: contour-map API | Planning updated: HLD ADR-7, §6.10, FR-15/16; plan phase MC | Start MC-2 (KML parser) | Sample measured — 1355 lines, 32 levels, 1.0 m interval 267–298 m, elevation in `<name>`, 2D coords, 8.52 km², Chhattisgarh. Slots in as an `ElevationSource`, reusing M1/M3 hydrology unchanged. |
| — | DEM source change | Switched off OpenTopography | M1-1 against `copernicus_aws.py` | Key unobtainable → Copernicus GLO-30 AWS open bucket. Same data, no key, no quota, windowed COG reads. **Ring 1 needs zero credentials.** |
| — | M0 build | M0 complete except M0-11 / M0-15 / M0-20 | Pick the target district + 3 test villages (M0-15) | Stack verified end to end: 133 tests green, ruff/black/mypy clean, migration applied, PostGIS + pg_trgm + unaccent live, `/health/ready` = ready. Nothing committed — that is yours. |

## 0.4 Actuals vs Estimates

Record what tasks *actually* took, for the ones that diverged by more than ~50 %. This is not
bookkeeping for its own sake: after five or six entries you will know your personal estimation factor,
and can apply it to the ~330 h still ahead. Without it, every remaining estimate stays as wrong as the
first ones were.

| Task | Est | Actual | Factor | What ate the time |
|---|---|---|---|---|
| | | | | |

**Running factor:** ______ (mean actual ÷ estimate) → multiply remaining phases by this.

## 0.5 Blockers

Anything stopping progress. Mark the task `[!]` in its phase table so `progress.py` surfaces it.

| Task | Blocked since | Blocked on | Workaround being used | Resolved |
|---|---|---|---|---|
| ~~M0-11 seed villages~~ | M0 | ~~M0-15~~ | **DONE** — 7,595 admin areas + 19,715 Chhattisgarh villages seeded; 19,528 (99.05 %) carry a sub-district polygon. Village *geometry* remains outstanding as M0-11d, and is a file drop, not code | `[x]` |
| ~~M1 (all)~~ | M0 | ~~OPENTOPOGRAPHY_API_KEY~~ | **RESOLVED** — switched to the keyless Copernicus AWS bucket (Decision 12). M1 is unblocked | `[x]` |

---

# 1. How To Use This Plan

## 1.1 Effort budget and pace

| Ring | Contents | Effort | Cumulative |
|---|---|---|---|
| **Ring 1** (M0–M7 + MC) | All 8 mandatory FRs + FR-15/16 + all deliverables | ~265 h | ~265 h |
| **Ring 2** (M8–M11) | Everything else the HLD designs — ISRO sources, ML, full API, hardening | ~125 h | ~390 h |

Pick a sustainable weekly rhythm and read off the calendar rather than committing to one:

| Pace | **MC phase** (contour API, ~76 h incl. M0+M1) | Ring 1 (~265 h) | Everything (~390 h) |
|---|---|---|---|
| 8 h/week (a few evenings) | ~7 weeks | ~33 weeks | ~49 weeks |
| 15 h/week | ~4 weeks | ~18 weeks | ~26 weeks |
| 25 h/week | ~2.5 weeks | ~11 weeks | ~16 weeks |
| 40 h/week (full time) | ~1.5 weeks | ~7 weeks | ~10 weeks |

The **MC column is the one that matters right now** — it is the separately-graded phase. It assumes
M0 (done, 18 h) plus M1's hydrology (20.5 h) plus MC itself (58 h), minus the 18 h already spent.

**These are realistic, not optimistic.** An earlier revision of this plan put Ring 1 at 129 h. That
figure was wrong, and it was wrong in the way software estimates usually are — it costed the *typing*
and not the *finding out*. It has been re-estimated task by task on the assumption that this is your
first substantial geospatial build. What changed:

| What was under-costed | Was | Now | Why |
|---|---|---|---|
| DEM preprocess (M1-2) | 2 h | 4 h | Mosaic + reproject with correct elevation resampling + nodata handling + sink fill is four separate things that each fail in their own way |
| SHRUG village seed (M0-11) | 1 h | 4 h | Large download, encoding, CRS, and the Census-2011 ↔ LGD join |
| ESA WorldCover fetch (M4-7) | 2 h | 4 h | WCS `GetCoverage` is fiddly; the STAC → windowed-COG route needs learning |
| Stage–storage flood fill (M5-7) | 2.5 h | 4 h | Connected-component fill at many levels, constrained to the pour point's basin |
| Golden tests (M3-7) | 2 h | 4 h | Constructing DEMs with *analytically known* answers is the hard part, not asserting |
| PDF report (M7-1) | 3 h | 5 h | WeasyPrint + embedded static maps + charts, first time |
| Sentinel-2 NDWI (M9-2) | 4 h | 8 h | OAuth2, Process API evalscript, cloud masking, dry-season compositing |
| Bhuvan / Bhoonidhi (M8-2, M8-6) | 3 h each | 5 h each | Sparsely documented, token-based, trial and error |
| Frontend result panel (M6-12) | 3 h | 5 h | It is most of the UI, not one component |

**Nine tasks that were simply missing** have been added — including the one that matters most:
**M7-9, writing the technical report (10 h)**. It is a graded 10-mark deliverable and the previous
revision had an outline for it but no task and no hours. Also added: RFC 7807 error middleware and
structlog (M0-16), fail-fast config validation (M0-17), the frontend Dockerfile and nginx (M0-18),
the celery-worker and titiler compose services (M0-19), a **learning spike** (M0-20), the typed API
client and state management (M2-11), map legend and **data attribution** (M2-12), four Alembic
migrations for tables that later phases assume (M3-11, M4-15, M5-14), empty-state and error UX
(M6-13), `DEMO_MODE` and cache warming (M7-10), the self-hosted OpenTopoData container (M7-11), and
screenshots (M7-12).

Two remaining caveats:

- **These estimates assume continuous context.** Returning to a phase after three weeks away costs
  re-orientation that is not in the column. Finishing a phase before pausing is worth real hours.
- **M0-20 is a real task, not padding.** Three hours spent on pysheds and rasterio against a toy DEM
  before M1 will save more than three hours of confused debugging inside M1.

## 1.2 Since there is no deadline, do these three things properly

Having no deadline removes the reason most student projects end up unmaintainable. Spend that freedom
on the things that a deadline normally destroys:

1. **Write the test in the phase, not later.** §7 gives a gate per phase. A golden test written while
   the algorithm is fresh takes 30 minutes; the same test written two months later takes an afternoon
   and finds bugs you have already built on top of.
2. **Finish a phase before starting the next.** Half-finished phases are where context evaporates.
   Exit criteria exist so you can tell.
3. **Keep the Decision Log (§14) current.** Every non-obvious choice, written down when you make it,
   becomes the technical report almost for free. Reconstructing your own reasoning six weeks later is
   the single most wasteful thing on a long solo project.

## 1.3 What changed now that the deadline is gone

The previous revision of this plan was built around a 10-day crunch. Those compromises are reverted:

| Deadline-driven cut | Status now | Where |
|---|---|---|
| Cap at 24 of 72 designed endpoints | **Reverted** — full surface is Ring 2 | M10 |
| ISRO sources (Bhuvan, Bhoonidhi) demoted to stretch | **Reverted** — back to Tier-1 as HLD §4.2 designs. Approval lead time is now irrelevant | M8 |
| ML suitability layer dropped | **Reverted** — in scope with proper spatial-block validation | M9 |
| FR-10 … FR-14 dropped | **Reverted** — all in scope | M9, M10 |
| Coverage target reduced to 40 % | **Reverted** — 70 % on `services/` | M11 |
| "Solo Track" reduced scope | **Removed** — unnecessary without time pressure | — |

What is **kept** from that revision, because it was good practice rather than a concession:
the walking skeleton first (§2), the per-phase test gates (§7), the priority order (§9), the risk
register (§10), and the release checklist (§12).

---

# 2. Build Strategy — Walking Skeleton First

The most important sequencing decision, and it has nothing to do with deadlines: **in M1, before any
UI and before any feature is polished, get one hard-coded request to travel the entire pipeline and
return real numbers.**

```
  M1 walking skeleton  (one script, one endpoint, no UI, no async, no cache)

  hard-coded bbox  →  Copernicus GLO-30 COG on AWS  (no key, windowed read)
                   →  pysheds: fill → flow direction → flow accumulation
                   →  catchment for one hard-coded pour point
                   →  Open-Meteo ERA5-Land 30-yr daily rainfall
                   →  SCS-CN with a fixed CN = 78
                   →  prismoidal pond volume at d = 3.0 m
                   →  print JSON: catchment_ha, rainfall_mm, runoff_m3, capacity_m3
```

**Why this ordering wins regardless of schedule:**

- Every external dependency and every core algorithm is proven **early**, when changing your mind is
  still cheap. The things that derail projects like this — a provider that needs a key you don't have,
  a pysheds output in the wrong CRS, GDAL refusing to build — surface in the first few sessions.
- Every later phase becomes *deepening a working stage* rather than *integrating for the first time*.
  Integration is the highest-variance work in the project; do it once, early, on a tiny scope.
- You always have something runnable. On a long solo project with no deadline, the real risk is not
  running out of time — it is **losing momentum**. A pipeline that produces real numbers about a real
  village is far more motivating than four finished modules that have never met.

The anti-pattern: building FR-1 → FR-2 → FR-3 to completion in order, and first running them together
near the end.

---

# 3. Scope — Three Rings

## Ring 1 — Mandatory (M0–M7): the 24 endpoints that cover all 8 required FRs

`★` = demo-critical.

| # | Method | Endpoint | FR | Phase | Status |
|---|---|---|---|---|---|
| 1 | GET | `/api/v1/health` | — | M0 | `[ ]` |
| 2 | GET | `/api/v1/villages/search?q=` | FR-1 | M2 | `[ ]` |
| 3 | GET | `/api/v1/villages/{id}/boundary` | FR-1 | M2 | `[ ]` |
| 4 | GET | `/api/v1/villages/{id}/imagery` | FR-1 | M2 | `[ ]` |
| 4a | **POST** | **`/api/v1/terrain/contour-map`** ★★ | FR-15 | MC | `[ ]` |
| 4b | **POST** | **`/api/v1/analyzeContour`** (alias `/findCatchment`) ★★ | FR-16 | MC | `[ ]` |
| 4c | GET | `/api/v1/terrain/contour-map/{dem_id}/contours` | FR-15 | MC | `[ ]` |
| 5 | POST | `/api/v1/terrain/dem` | FR-2 | M1 | `[ ]` |
| 6 | GET | `/api/v1/terrain/dem/{id}/tiles/{z}/{x}/{y}.png` | FR-2 | M2 | `[ ]` |
| 7 | POST | `/api/v1/terrain/contours` ★ | FR-2 | M2 | `[ ]` |
| 8 | POST | `/api/v1/terrain/derivatives` | FR-2 | M2 | `[ ]` |
| 9 | POST | `/api/v1/hydrology/flow` | FR-4 | M1 | `[ ]` |
| 10 | POST | `/api/v1/hydrology/streams` | FR-4 | M3 | `[ ]` |
| 11 | POST | `/api/v1/hydrology/catchment` ★★ | FR-4 | M1/M3 | `[ ]` |
| 12 | GET | `/api/v1/rainfall/statistics` ★ | FR-5 | M4 | `[ ]` |
| 13 | GET | `/api/v1/rainfall/historical` | FR-5 | M4 | `[ ]` |
| 14 | GET | `/api/v1/land/soil` (→ HSG) | FR-6 | M4 | `[ ]` |
| 15 | POST | `/api/v1/land/available` ★ | FR-3 | M5 | `[ ]` |
| 16 | POST | `/api/v1/runoff/estimate` ★★ | FR-6 | M4 | `[ ]` |
| 17 | POST | `/api/v1/pond/stage-storage` | FR-7 | M5 | `[ ]` |
| 18 | POST | `/api/v1/pond/design` ★★ | FR-7 | M5 | `[ ]` |
| 19 | POST | `/api/v1/suitability/analyze` | FR-9 | M6 | `[ ]` |
| 20 | GET | `/api/v1/suitability/{id}/sites` ★ | FR-9 | M6 | `[ ]` |
| 21 | POST | `/api/v1/analysis` ★★ | FR-8 | M6 | `[ ]` |
| 22 | GET | `/api/v1/analysis/{job_id}/status` + `/result` ★ | FR-8 | M6 | `[ ]` |
| 23 | POST | `/api/v1/reports/generate` + download | Deliv. | M7 | `[ ]` |
| 24 | GET | `/api/v1/export/{id}?format=geojson` | Deliv. | M7 | `[ ]` |
| 25 | **Plain React state instead of TanStack Query + Zustand** (defers M2-11) | The plan specified both before the UI existed. The UI that exists has one request, one result and five booleans of view state; a query cache and a global store would be ceremony around `useState`. The moment they earn their place is M2-8 (village autocomplete: debounce, cancellation, cached results per query) and M10 (job polling), and M2-11 stays open for exactly that. The API client is hand-written and deliberately partial for the same reason — it types the fields the UI reads, so a rename in an unread corner of the response cannot break the build for no reason |
| 26 | **Satellite imagery is the default basemap, street map the alternative** (M2-7) | Siting a pond is mostly a question of what is already on the ground — an existing tank, a field boundary, a settlement edge — and none of that is on a road map. The catchment outline can be sanity-checked against visible drainage, which is the single most useful check a Panchayat engineer can make by eye |
| 27 | **`humanise()` consults a label dictionary before its generic transform** | De-underscoring covers almost every enum the API returns, so a new backend value renders sensibly with no frontend change. It also rendered `no_soil_lulc` as "No soil lulc" — the dictionary exists only for acronyms and names needing punctuation an identifier cannot carry |
| 28 | **nginx `client_max_body_size` is pinned to the API's `MAX_UPLOAD_BYTES`** | The default 1 MB rejected the 6.4 MB sample map with an HTML error page before FastAPI saw it, so the API's own RFC 7807 size response could never fire. nginx is the backstop, the API is the authority, and nginx's own refusal now returns `application/problem+json` so the UI parses one error shape |
| 29 | **The TiTiler upstream resolves lazily via a variable** | nginx resolves literal upstreams at config-parse time and refuses to start if the name is missing, so an optional service that nobody has deployed yet took the whole UI down. Behind a variable plus Docker's embedded resolver, `/tiles/` answers 502 and every other route keeps working |
| 30 | **One end-to-end test drives a real browser** (M2-16) | Every other test checks a layer in isolation, and all of them passed while the UI could not upload a file at all. A hand-rolled ~170-line CDP client keeps Playwright's browser download out of a project whose real work is hydrology; the test skips, never fails, when the stack or a browser is absent |
| 31 | **Container health probes address `127.0.0.1`, never `localhost`** | nginx's entrypoint only patches in `listen [::]:80` when `default.conf` is the file it shipped, and ours is not — so nginx binds IPv4 only while `localhost` inside the container resolves to `::1` first. The frontend reported `unhealthy` forever while serving every request correctly. The same trap applies to uvicorn, so both probes were pinned |
| 32 | **The `dev` image stage carries the same health probe as `prod`** | The `HEALTHCHECK` was declared only in `prod`, and compose builds `target: dev` — so the API container reported no health at all and `depends_on` could only wait for *started*. The frontend now waits for `service_healthy`, which means the first request a user makes cannot hit an API still importing rasterio |
| 33 | ★ **Target area: Chhattisgarh → Durg district, the ground the supplied contour map already covers** (M0-15) | The sample map's centroid reverse-geocodes to Khapri, Durg Tahsil. Sharing the ground between the graded contour phase and the village-search phase means one area is well understood rather than two being half-understood, and it makes the two elevation sources directly comparable there (contour relief 31.0 m against Copernicus GLO-30's 30.3 m over the same 8.5 km²) |
| 34 | **Test sites are screened with measurements, not local knowledge** (`scripts/screen_sites.py`) | Relief, built-up share and open-water share are read from Copernicus GLO-30 and ESA WorldCover over a 2.5 km box per candidate. It rejected Bhilai at 81.1 % built-up and Durg town at 65.7 % — a screen that cannot reject a steel city is not a screen — and confirmed Khapri (21.2 m relief, 10.9 % built-up, 8.1 % open water) and Jevra Sirsa (23.6 m, 12.9 %, 2.7 %) |
| 35 | **No coordinate is invented for a village that cannot be located** | Kutelabhata returns nothing from Nominatim across nine spellings including Devanagari, nor from an Overpass name search over the district. A guessed coordinate produces a plausible page of measurements about the wrong ground, which is worse than an admitted gap — the script prints it as unresolved and defers to the Census directory M0-11 seeds. Related: OSM holds ten *unnamed* hamlet nodes and one named village inside the surveyed area |
| 36 | ★ **SHRUG's names are open; its polygons are not — so the two halves are seeded from different sources** (M0-11) | The SHRID→LGD crosswalk on Harvard Dataverse is CC0 and carries all 596,390 Census-2011 villages and towns with full state/district/sub-district hierarchy, downloadable with no credentials. The polygons go through a form on devdatalab.org; the Dataverse mirror holds only the socioeconomic `.dta` tables and the GitHub releases carry no assets. DataMeet (HLD E3) covers nine states and Chhattisgarh is not one; geoBoundaries stops at CD Block, 7,152 units against ~600,000 villages. The name index is the half the search actually needs — terrain comes from the uploaded contour map, never from a village outline |
| 37 | **`villages.geom` is nullable and `boundary_level` states what the polygon is** (migration 0002) | With names and geometry arriving at different granularities, a row has to be able to say whether it is offering the village's own boundary or the containing sub-district's. Serving a 662 km² tehsil outline as though it were a village boundary is the one failure that would quietly corrupt every downstream area figure |
| 38 | ★ **One name fold, in Python, applied to both the stored name and the query** (`app/core/names.py`) | HLD CH-24 in code. Devanagari transliterates with positional schwa deletion — रामपुर → `rampur` while कमल → `kamal`; dropping every inherent vowel gives `kml` and keeping every one gives `ramapura`, both wrong. Verified live: `कुटेलाभाठा` scores 1.00 against the register's `kutelabhatha`, as do `kutelabhata` and `Kutelabhaata`. Re-implementing the fold as a SQL function would create a second copy free to drift from the first, which is why the seeder joins in Python |
| 39 | **The fold is right for retrieval and wrong as a join key, so exact match is tried first** | `Balod` and `Baloda` are two distinct Chhattisgarh sub-districts that both fold to `balod`. Folding them together manufactured an ambiguity the linker refused to resolve, leaving 92 real villages with no polygon that was sitting in the table the whole time. Exact name first, folded second, at each level |
| 40 | **The sub-district join falls back to `(state, sub-district)`, because districts get reorganised** | The register is Census 2011; the boundaries are 2018. Chhattisgarh split Durg into Durg, Balod and Bemetara in 2012, so the register puts Nawagarh in Durg while the boundary set puts it in Bemetara — 279 villages in the target district itself failed on a district name that had simply moved. State boundaries survive reorganisation; district boundaries do not |
| 41 | **Ambiguity is left unresolved rather than guessed** | Two Nawagarh tehsils in one state, in different districts, with the register's district name pointing at neither. 187 villages stay unlinked. A village attached to the wrong tehsil would render in the wrong part of the state, which is a worse answer than no polygon and a much harder one to notice |
| 42 | **Every response that offers a geometry or coordinate states what it is** (M2-2) | `/boundary` returns `represents`, `is_village_boundary` and a `caveat`; `focus` returns `is_centre_of` and `approximate`; `/imagery` returns `bounds_of`. Durg tehsil is 66,256 ha against a village of a few hundred — a caller who mistook one for the other would compute a catchment-to-village ratio wrong by three orders of magnitude with nothing looking broken |
| 43 | **Search reports *how* it matched, not just that it did** | `matched_by` distinguishes `exact` (the caller typed the register's spelling) from `folded` (they agreed only after transliteration folding), `prefix` and `trigram`. "We found your village" and "we found something 31 % similar" deserve different confidence from someone about to plan a pond on the answer |
| 44 | ★ **The hierarchy is not always enough to disambiguate, so the response says when it isn't** | Durg district holds **ten villages called Khapri, two of them in the same sub-district**. Name plus place genuinely cannot separate those, which is exactly why CH-24 makes the code canonical. `hierarchy_is_ambiguous` flags the colliding rows so a client knows to show the identifier instead of rendering two identical-looking options |
| 45 | **A weak filtered result is explained, not just an empty one** | Searching Raipur for a Durg village returns five vaguely similar names rather than nothing, so an empty-result-only note never fired and the caller would reasonably conclude their village is absent. The note now triggers whenever no result reaches 0.9 similarity, and names where the strong match actually is |
| 46 | **`text()` parses bind parameters inside SQL comments** | A comment explaining the NULL-cast workaround mentioned a colon-prefixed placeholder in prose; SQLAlchemy took it for a real parameter and demanded a value. Worth knowing before writing any explanatory comment in a `text()` block |
| 47 | ★ **The only open LGD code is the Gram Panchayat's, so `villages.lgd_code` stays null** (M2-2c) | HLD E2 named the village LGD code as the canonical key. The CC0 crosswalk's LGD column is the *Panchayat* code — an elected body covering a cluster of villages. Writing it into the field the design calls "the canonical village key" would make that field identify something coarser than a village, silently, somewhere nobody re-checks. `census_2011_id` and `shrid` serve as the key; a real village LGD code needs a registration (M2-2d) |
| 48 | **Village→Panchayat is a link table, not a column** | Measured over all 638,847 rows: **12,045 villages belong to two or more Panchayats** — Bambooflat is in Bambooflat-I and Bambooflat-II. A single foreign key would be wrong for 2 % of the country and look right everywhere else. Chhattisgarh happens to have zero such villages, which was verified against the source rather than assumed from the seeded count reading `0` |
| 49 | **The Panchayat is what finally disambiguates the namesakes** | Decision 44 could flag that Durg's two Khapris were indistinguishable but not resolve it. Their Panchayats are `Khapri K` and `Khapri`; Dhamdha's pair are `Khapri G` and `Khapri`. Every ambiguous row in the district now carries a distinct Panchayat, so the flag comes with an answer rather than only a warning |
| 50 | **Cross-file joins go through the Census codes, never the names** | The name register is Census 2011 and the LGD crosswalk reflects a later reorganisation, so their district and sub-district columns disagree while their numeric codes agree exactly — verified on Kutelabhatha: SHRID `11-22-409-03317-442569` against state 22, district 409, sub-district 3317, village 442569. The bare village code is *not* nationally unique (12,068 collisions), which is why the district and sub-district segments are part of the key |
| 51 | ★ **`alembic revision --autogenerate` would have dropped most of the database** (M0-7b) | `alembic check` had never been run. It proposed removing thirty-odd PostGIS TIGER tables (`county`, `edges`, `featnames`, `zip_lookup`, …) and all three trigram indexes, because `include_object` filtered three table names and nothing else. `make revision` is a documented target, so the migration was one command away from being generated and committed. Now: any reflected table the models do not declare is excluded, as are the GeoAlchemy2 spatial indexes and anything ending `_trgm` |
| 52 | **The migrations and the models had disagreed since 0001** | `created_at` was created nullable where the models declare it NOT NULL, and `unique=True, index=True` was rendered as a UNIQUE constraint plus a separate plain index rather than the single unique index SQLAlchemy expresses. Behaviourally invisible, which is why it survived — the cost was that `alembic check` reported permanent drift, so the next *real* mismatch would have been lost in the noise. Migration 0004 reconciles it rather than rewriting the committed 0001 |
| 53 | **The drift check is a test, not just a Makefile target** | `app/tests/integration/test_schema_drift.py` runs alembic's own `compare_metadata` through the same `include_object` filter, so a pass means autogenerate would produce an empty migration. The filter lives in `alembic_env_helpers.py` because `env.py` is executed as a script and cannot be imported — one copy, or the test could pass while alembic still proposed dropping half the schema |
| 54 | **Editing an already-applied migration breaks its own downgrade** | Renaming an index inside 0002/0003 left `downgrade()` trying to drop names the live database did not have, which failed mid-way and left a half-reverted schema. Nothing was deployed so a `DROP SCHEMA` and clean re-migrate was the right fix — but the general rule is that an applied migration is immutable, and a correction belongs in a new revision |
| 55 | **The village outline is dashed unless it really is a village boundary** (M2-8) | The API says `is_village_boundary: false` and the map has to say the same thing visually. A solid line around a 662 km² tehsil would read as a claim the API explicitly refuses to make, and nobody reads a caveat they have already believed the picture |
| 56 | ★ **`line-dasharray` cannot be driven by a feature property, and MapLibre says nothing when you try** | It is a `cross-faded` property whose expressions accept only `zoom`, so `["case", ["get", "is_village_boundary"], …]` is invalid. The layer simply did not render — no exception, no console output, the outline just absent. Split into two line layers distinguished by `filter`, which *is* data-driven |
| 57 | **The map forwards its own `error` event to `console.error`** | MapLibre reports style and tile problems there and nowhere else, so the invalid dasharray was invisible to the end-to-end test's "nothing throws in the console" assertion. Forwarding them means the existing assertion now covers the map as well as React — and a 404 tile stops being silent in development |
| 58 | **The search aborts the previous request on every keystroke** | Debounced at 220 ms, but debouncing alone is not enough: a slow reply for `kut` can land after a fast one for `kutela` and overwrite the better result with a worse one. The controller is aborted before each new request |
| 59 | **The Panchayat is shown in a suggestion only when the hierarchy fails** | Durg district returns ten villages called Khapri; showing the Panchayat on every row would triple the list's height for information that matters on six of them. It appears exactly where `hierarchy_is_ambiguous` is set |
| 60 | **GDAL's native `COG` driver, not `rio-cogeo`** (M2-3) | GDAL 3.9 ships the driver, so internal tiling, overviews and header ordering come from one `rasterio.open(..., driver='COG')` with no extra dependency and no two-pass write. `write_cog` then *verifies* the result has overviews and refuses a raster that would be read at full resolution for every zoom level -- a tiled GeoTIFF without them is not a COG, and the difference is invisible until a tile takes ten seconds |
| 61 | ★ **Hillshade as a dot product, not slope-and-aspect trigonometry** | The two are equivalent, but the trigonometric form needs an `atan2` whose argument order encodes a convention, and getting it wrong rotates the illumination by 90 degrees -- which renders a perfectly plausible shaded relief of the wrong terrain. The first implementation did exactly that: with light from 315 the brightest facet came out facing 225. `N.L/|N|` has one convention left and it is checkable by hand: a plane falling toward 315 reads 224, flat ground reads `254*sin(45)=180` |
| 62 | **Rasters are keyed on the elevation grid's bytes, not on a request id** | The same contour file re-uploaded reuses its rasters instead of writing a second identical copy under a new random name, and a stored analysis replays against byte-identical layers (NFR-13). Hillshade azimuth, altitude and z-factor are part of its key: two illuminations are two rasters, and serving one for the other is invisible in a tile |
| 63 | **`/analyzeContour` returns a `dem_id`** | Terrain tiles otherwise needed a second upload of the same 6 MB file, re-parsed to reach a grid the analysis already held. 56 ms against 3 s |
| 64 | **The tile store is fanned out two hex characters deep, and slope rescales to a fixed 0-15 %** | Content-addressed names all in one directory stops being workable; and rescaling slope per-raster would make a flat plateau look as varied as a hillside, when the number that matters is the fixed 8 % buildability threshold. Elevation *is* rescaled per-raster, to its own 2nd-98th percentiles -- a fixed range renders 30 m of plateau relief as one flat colour |
| 65 | **The terrain layers are off by default, which is the opposite of how it looks** | Satellite imagery is the default basemap and already carries terrain texture; a hillshade over it desaturates the photo more than it reveals, and the composite reads muddier than either layer alone. They earn their place on the street basemap and as something consulted deliberately, so they are offered rather than imposed |
| 66 | **A layer added after the last visibility pass keeps MapLibre's default of visible** | The terrain rasters arrive in a later effect than the one applying the toggles, so slope rendered over the whole map with its checkbox cleared. `terrain` belongs in that effect's dependencies |
| 67 | **TiTiler's image listens on port 80, not 8000** | It is built on uvicorn-gunicorn, which defaults `PORT=80`, while the compose mapping and `TITILER_ENDPOINT` both named 8000. The container started, logged "Application startup complete", and refused every connection -- which reads as a network problem rather than a port mismatch. `PORT` is now pinned |
| 68 | **nginx's `expires` directive emits its own `Cache-Control`** | Combined with `add_header` the tile response carried two of them -- both from nginx, resolved differently by different caches. One explicit header now, with the upstream's hidden. `immutable` is honest because COG paths are content-addressed |
| 69 | ★ **Contours → DEM → contours is the strongest test of the interpolation there is** (M2-6b) | `services.interpolate` and `services.contours` are inverses, so running the real 1 m survey through both and comparing catches a systematic error in the TIN, the CRS, the affine transform or the row/column ordering — errors that no unit test on either half would see. Measured residual against the surveyed vertices: **median 0.109 m, p95 0.371 m** on a 1 m interval, about a tenth of the contour spacing |
| 70 | ★ **Agreement is tested on elevations, not on distances between lines** | A contour's horizontal position is its elevation divided by the local slope, so on nearly-flat ground a 0.1 m vertical error moves the line tens of metres sideways. A distance comparison put 8 % of regenerated vertices more than three cells from any surveyed line — almost all of them on the flats, and none of it error. Sampling the DEM *at* the surveyed vertices removes the amplification. A coarse distance check is kept at three times the mean contour spacing, which is what geometry can actually catch: a CRS or transform mix-up displaces everything by kilometres |
| 71 | **Filling nodata holes below every level does not stop contours being traced round them** | It was the obvious fix and it is wrong: the step from fill to real ground crosses *every* level on the way up, so a smooth closed line gets drawn around each hole at each level — reading as terrain, not as an artefact. Traced paths are instead split on cell validity, all four cells touching a vertex having to hold data. A contour crossing a hole survives as two runs rather than vanishing |
| 72 | **The nodata test uses an interior hole, not a clipped edge** | Cutting a west-to-east ramp down the middle makes the lowest surviving level genuinely coincide with the cut, so a boundary-hugging contour there is correct and the test proves nothing. A rectangular hole punched into a smooth ramp has no such excuse |
| 73 | **Levels snap to multiples of the interval, and are rounded at every step** | Starting at the surface minimum gives 267.31, 268.31 — correct and useless to read off a map. And repeated addition of 0.1 reaches 3.0000000000000004, which renders in a label |
| 74 | ★ **A reach is a Strahler stream, not a junction-to-junction segment** (M3-3) | Cutting at every junction chops a trunk into one segment per tributary, and the counts then stop obeying Horton's law of stream numbers — measured on the sample catchment it gave **16 order-4 "reaches" against 10 of order 3**, a bifurcation ratio of 0.62, which is impossible. A reach now runs from where it attains its order to where it loses it. Corrected: stream numbers 64/16/2/1, ratios 4.0/8.0/2.0, mean lengths 137/389/761/1775 m — both Horton laws hold |
| 75 | **The stream threshold is a contributing *area*, not a cell count** | 1 ha means the same thing at 5 m (400 cells) and at 30 m (11), so a threshold tuned on one survey transfers. 1 ha is deliberately small for village terrain: a nala draining a few hectares is exactly what a check dam sits on, and a threshold tuned for a mountain basin erases every one |
| 76 | **Drainage density is only reported when the network is restricted to a catchment** | Channel length over an arbitrary rectangle is a property of the rectangle. The field is `null` otherwise rather than carrying a number that means nothing |
| 77 | **Line width is driven by Strahler order** | The reason for computing it. A first-order headwater and a fourth-order trunk drawn identically tell the reader nothing about which one a pond can be built across; the panel also translates the number ("4 — a substantial channel") because the integer alone says little |
| 78 | **The drainage network is fetched independently of the terrain tiles** | It needs no tile server, so a missing TiTiler must not cost the channels as well. Both are optional requests after the analysis, and a failure in either leaves the analysis intact |
| 79 | **Horton's law of stream lengths cannot be tested on a hand-built network** | A toy grid's higher-order channels are one or two cells long, so mean length by order is noise. The synthetic golden tests assert the laws that *do* hold at that scale (numbers decrease, orders promote only on equal confluence); the length law is asserted on the real survey, where mean lengths run 137/389/761/1775 m |
| 80 | ★ **The pour-point snap searches a circle, not the square window a slice gives you** | The displacement is reported next to the radius that was asked for, and a square window of N cells reaches N·√2 into the corners — so a point came back as having moved 175 m under a 150 m radius. A contract the caller cannot reason about is worse than a coarser search: the window is now masked to a true circular radius, and `moved_m ≤ search_radius_m` holds |
| 81 | **`search_radius_m` is reported as the radius actually searched** | The search works in whole cells, so 150 m at 5 m resolution is exactly 30 cells and the effective radius is 150.0 m — but at 30 m resolution it would round to 5 cells and 150 m. Echoing the request rather than the effective value would misreport it by up to half a cell |
| 82 | **`hit_the_search_limit` is reported, because it usually is** | All three test clicks moved the full 150 m, meaning the search ran out of room rather than finding the channel. The answer still stands but deserves less confidence, and saying so is the difference between a nudge and a relocation |
| 83 | **A coordinate outside the working UTM zone projects to infinity** | `rowcol` raises `OverflowError` flooring it, not the `IndexError` the guard caught, so a click at (0, 0) returned 500 instead of 422. Checking the projected result is finite covers that and every other way a projection can fail to produce a usable number |
| 84 | **A clicked catchment is styled and toggled apart from the analysis'** | Lime and dashed against cyan and solid. One is a ranked recommendation with runoff and pond sizing behind it; the other is the user poking at the terrain. Drawing them alike would invite the second to be read as the first |
| 85 | **An out-of-area click clears the exploration rather than raising the error overlay** | Clicking outside the survey is an ordinary answer to an ordinary question, not a failure of the analysis — taking over the error surface would suggest the whole result had gone wrong |
| 86 | ★ **Breaching exists because filling destroys what the system is looking for** (M3-2) | A pond goes in a depression, and `fill_depressions` raises every depression to its spill level. The measurements already worked around it — slope and depth are taken on the original surface — but the *routing* still ran over a raised plateau, so accumulation spread across a filled hollow instead of converging into it. Breaching carves an outlet through the barrier instead: on the sample survey **209 of 313 depressions** were resolved with **941 cells carved, a maximum cut of 0.78 m and 487 m³ moved**. Those are interpolation artefacts where two contour lines nearly touched, and a filled surface hides every one |
| 87 | **The breach is bounded in depth and length, and refuses rather than trenches** | An unbounded least-cost search always finds *some* path. Carving four metres across half a survey to drain a closed basin invents topography rather than revealing it, so a depression whose outlet costs more than 2 m of cut or 40 cells of path is left for the fill pass. The bounded form of Lindsay (2016) |
| 88 | **The realised cut is checked, not just the planned cost** | The search bounds how far each cell sits above the pit; the carved channel then descends by an epsilon per cell so D8 has an unambiguous direction along it. On a 28-cell path that turned a 2.000 m budget into a **2.028 m cut** — small, and still a limit the caller was told would hold. The carve is now planned in full and abandoned if its deepest cell exceeds the limit |
| 89 | **Breaching reduces filling, it does not replace it** | The fill pass runs afterwards regardless, so the surface is always fully routable — a depression the breach could not resolve still has to go somewhere. A test asserts every interior cell has an outflow direction after breaching |
| 90 | **Flatness is measured as the steepest descent to any of eight neighbours, at its true distance** | Treating a diagonal step as orthogonal overstates the gradient by 41 %, so a surface sitting at the threshold would be misclassified. A cell with no lower neighbour counts as flat, which is right: it is a pit or a plateau and D8 has nothing to work with either way |
| 91 | **`auto` breaches above 15 % flat and fills below it** | That is where the filled surface stops describing the terrain and starts describing the fill. Below it the simpler pass is also the safer one. The sample survey is 8.4 % flat, so `auto` fills it — and the report says which was chosen and why, because a catchment drawn over a heavily conditioned surface deserves less confidence than one over terrain that drained on its own |
| 92 | ★ **Two independent counts of the same graph agree** (M3-10) | `flow_accumulation` counts contributing cells by topological sweep; `delineate_catchment` counts them by reverse traversal from the outlet. Different algorithms over the same graph, so agreement is evidence about both — and a mis-signed neighbour offset breaks them differently. Verified the test has teeth: perturbing the accumulation by 5 % makes it fail |
| 93 | **Catchment area is asserted monotonic downstream, which no single value can check** | Stepping one cell downhill can add contributing area but never remove it. One wrong area looks exactly like a correct one; the violation only shows as a broken *relationship*, which is why this is a property test rather than a value test |
| 94 | **A `pytest.skip` inside a Hypothesis test aborts the whole property run** | The first version skipped on its first awkward example and so quietly stopped testing anything — 1 passed, 1 skipped, and nothing exercised. It now tries twelve start cells and uses `assume`, and the statistics confirm 12 and 8 real examples with zero filtered |
| 95 | **`catchments` and `streams` land as a migration with nothing writing to them yet** (M3-11) | The contour endpoints work with no database at all — a design decision, not an omission — so wiring persistence into them would either make PostGIS a requirement for analysing a KML file or add a conditional path through the one place that most needs to stay simple. M6 orchestrates persisted analyses; a schema is cheaper waiting than added under a running feature |
| 96 | **The stored catchment records how it was conditioned, and the stored stream records its threshold** | Both are the difference between a number and an interpretable number. A catchment delineated over a heavily filled surface deserves less confidence than one over terrain that drained on its own, and six months later nobody remembers which it was. The stream threshold decides everything downstream: the same terrain gives 720 reaches at 0.5 ha and 18 at 25 ha |
| 97 | **AOI auto-expansion (M3-5) is blocked on the remote-DEM path, not deferred by choice** | Expanding the area and recomputing assumes the DEM can be re-fetched over a larger extent. That holds for Copernicus, where the buffer is a request parameter; the contour path's extent is the surveyed sheet and there is no more of it to ask for. Expanding it would mean extrapolating past the survey and presenting the result as measurement — so the contour path reports `touches_survey_edge` instead, stating that the area is a lower bound |
| 98 | ★ **SCS-CN is cross-checked against formulae fitted on Indian gauged catchments** (M4-11) | HLD CH-15: SCS-CN is a US model, and a single storm dropping a third of the annual total behaves nothing like the temperate rainfall its curve numbers were fitted to. The Indian corrections are already applied (`Ia = 0.3S`, AMC, daily series), but a corrected US model is still a US model. Inglis-DeSouza, Khosla and Barlow are old, coarse and regional — and that is the point: they were fitted on the monsoon, on rivers a few hundred kilometres from where this runs. Where they agree the estimate is worth more; where they diverge the spread is the honest uncertainty |
| 99 | ★ **Strange (1928) is deliberately not implemented, and says so** | It is a *tabulation* — runoff as a percentage of monsoon rainfall for Good/Average/Bad catchment character — not a closed-form expression, and the table has to come from Strange or a text that reproduces it. Writing values down from memory and presenting them as a cross-check would be worse than having none: it would lend false confidence to the figure it was checking. `strange()` reports what it needs instead of returning a number (M4-11b) |
| 100 | **Khosla's estimate is reported but excluded from the comparison range** | Its loss term is `0.48 × T`, about 14 mm for a 30 °C month, against monsoon rainfall of 300–400 mm — while actual monthly evapotranspiration in central India runs 100–200 mm. So it returns a coefficient of 0.88, which no rural catchment has. The figure is still shown, because the formula is what the method *is*; it is kept out of the range rather than dragging it upward |
| 101 | **Agreement is a tolerance band, not strict containment** | With one comparable method the "range" is a single point and nothing lands inside it — strict containment reported disagreement for a figure 1 % away from the only number available to compare against. A quarter is not arbitrary either: these are regional fits from the 1910s–1930s applied outside their own catchments, so agreement to within a quarter is as much as either family can claim |
| 102 | **Every method reports itself whether it applied or not** | "No cross-check was available" and "the cross-check agreed" are very different statements about the same number, so a method that cannot run says why rather than being silently absent from the response |
| 103 | **The wiring test uses a synthetic rainfall series, not the live provider** | Open-Meteo's daily request limit is real and gets hit — as it did here. A test that silently stops exercising the thing it is named after because an upstream service is throttling is worse than no test |
| 104 | ★ **The two daily rainfall series are never averaged** (M4-2) | SCS-CN is non-linear in daily depth — runoff from 100 mm in one day far exceeds runoff from 50 mm on each of two — and two reanalyses put the same storm on slightly different days. Averaging them turns one 100 mm storm into two 50 mm ones and *systematically understates* runoff, while producing a smoother, better-behaved-looking series. So the daily series always comes from a single source and the ensemble is used only for the annual statistics and the uncertainty band. It is the same trap as running SCS-CN on annual totals (HLD §6.9 measures that at C = 0.907 against the correct 0.393), reached from another direction |
| 105 | ★ **A second rainfall source is what keeps the analysis alive** (M4-1) | Open-Meteo enforces a daily request limit and it does get hit — repeatedly, during this work. Before NASA POWER existed that dropped the whole analysis to `terrain_only`: no runoff, no pond volume, no cross-check. Now POWER answers, the tier reaches `full`, and the response names which source supplied the series. Verified live with Open-Meteo returning 429 |
| 106 | **The primary source is chosen by resolution, deterministically — not by which replied first** | The daily series decides the runoff, so the choice cannot depend on network timing. Open-Meteo's ERA5-Land is 0.1° against POWER's 0.5 × 0.625° (~55 × 60 km, which can span several districts), so it leads when both answer |
| 107 | **NASA POWER reports missing values as −999.0, not null** | Summed naively that is not a gap in the record, it is a year with minus three hundred metres of rainfall. Its values are also keyed by `YYYYMMDD` in an object rather than parallel to a time array, so a missing day is an absent key rather than a hole to align |
| 108 | **A missing temperature is carried as NaN, never as zero** | 0 °C is a plausible-looking value that would halve Khosla's loss term for that month. Monthly means filter the NaNs rather than using `.mean()`, because one missing day would otherwise make the month NaN, then Khosla's loss NaN, then the runoff NaN — three steps from where the gap actually was |
| 109 | **Rainfall statistics moved to `providers/rainfall/base.py`** | Two sources now derive the same design figures — annual totals, CV, Weibull dependable rainfall, monthly normals, the derived monsoon window. Duplicating fifty lines of that per provider would guarantee they drift, and a 75 %-dependable rainfall that means something slightly different depending on which service answered is worse than having only one service. Provenance became a field on `RainfallStats` for the same reason: a shared `as_dict()` reaching for one provider's module constant would label every source as that provider |
| 110 | **My own e2e test broke on brittle string surgery, not a product fault** | It found the tier by splitting the whole page text on its first em-dash. That worked until the layer panel gained hints like " — search for a village", which sit *above* the results in the DOM: the split then cut long before the banner, the skip never fired, and the test demanded a failure notice on a run where nothing had failed. It now reads the `.tier` element directly |
| 111 | ★ **The rainfall cache is keyed on the source's grid cell, never the coordinate asked for** (M4-5) | This is the whole design, and getting it wrong makes the cache useless rather than merely imperfect. ERA5-Land is a 0.1° reanalysis, so every point inside one cell receives the *same* series — keying on exact lon/lat would store a fresh copy per query and never hit: two clicks 200 m apart would each pull and persist 11,000 rows of identical data. Verified: a second request returns in **0.00 s against 1.76 s**, and a point 300 m away hits the same cell |
| 112 | **Each source is quantised to its own grid, and POWER's is asymmetric** | MERRA-2 is 0.5° north–south and 0.625° east–west. One figure for both would either split cells that share a series or merge cells that do not. An unregistered source falls back to the coarsest grid, because over-coarse is the safe direction to be wrong: it serves a slightly displaced series — and reports how displaced — rather than never hitting |
| 113 | **The cell offset is disclosed in the series' own warnings** | At POWER's resolution a cached series can describe a point **27.9 km** from the one asked about. That is a property of the product, not of the cache, but the cache is where it becomes invisible — so the warning names the cell and the distance |
| 114 | **Row per (source, cell, day), with a unique constraint** | Row-per-day so a cache can be *extended* when a later year becomes available rather than invalidated whole. The unique index is what makes it a cache rather than an append log: without it a re-fetch adds a second copy of every day and reads start double-counting rainfall — inflating every runoff figure while looking entirely normal. A test writes twice and asserts the total is unchanged |
| 115 | **Below 95 % coverage a cached range is a miss, not a partial answer** | The annual totals and the coefficient of variation are derived from these days, so handing back a sparse series would quietly change the dependable rainfall the pond is sized on. Not 100 % either: a source can legitimately lack days — POWER's fill values, a reanalysis gap — and demanding every date would never hit for a series that will never be complete |
| 116 | **Every cache operation degrades to a no-op without a database** | The contour endpoints work with no database at all, a documented decision. A cache that could fail an analysis would be a worse feature than no cache |
| 117 | **`COALESCE` on the upsert, so one source cannot erase another's extras** | POWER carries temperature and no reference ET; Open-Meteo the reverse. Both write the same cell and day, and a plain `SET` would null whichever arrived first |

## Ring 2 — Complete the design (M8–M11)

> **OUT OF SCOPE — closed by decision.** Ring 1 is complete and submittable, and
> Ring 2 would add scope rather than quality:
>
> * **M8** is entirely Bhuvan / Bhoonidhi / IMD integration, and this project is
>   keyless by decision — the credentials were removed, not merely left unset.
> * **M9** is the ML layer. It needs MGNREGA training labels from Bhuvan, and
>   without ground-truth pond outcomes a model would only learn its own labels.
>   The "AI" here is the AHP engine with an auditable Consistency Ratio.
> * **M10** delivered the three requirements that mattered (FR-11 cadastral,
>   FR-12 comparison, FR-13 water balance). The remainder is auth, projects CRUD
>   and endpoint surface the assignment does not ask for.
> * **M11** is hardening for a deployment that will not happen — this runs
>   locally by design.
>
> The phases below are left in place as the design record. They are not work in
> progress.


The remaining 48 endpoints of HLD §5.2, the ISRO Tier-1 sources of HLD §4.2, the ML layer of
HLD §6.5.4, FR-10 through FR-14, and the full quality bar. **This is what makes the implementation
match the design document** rather than a subset of it.

## Ring 3 — Research extensions (only if you want to keep going)

Not in the effort budget; noted so the ideas are not lost. Sentinel-2 U-Net water-body segmentation ·
multi-pond cascade optimisation over a whole watershed · groundwater-recharge estimation from CGWB
time series · sediment-yield modelling for desilting schedules · offline-first PWA for field use ·
Hindi + one regional language UI (HLD NFR-15).

---

# 4. Repository Structure

Create this in M0. The structure itself is graded (code quality, 15 marks) — a flat `app.py` loses
those marks however well it works. With no deadline there is no excuse for getting this wrong.

```
contour/
├── docker-compose.yml
├── .env.example                 # every key documented, no secrets committed
├── README.md                    # → the Installation Guide deliverable
├── Makefile                     # make up / test / lint / seed / demo
├── docs/
│   ├── HLD.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── API.md                   # OpenAPI export + hand-written examples
│   ├── INSTALL.md
│   └── TECHNICAL_REPORT.md
├── backend/
│   ├── Dockerfile               # FROM ghcr.io/osgeo/gdal:ubuntu-small-*
│   ├── pyproject.toml
│   ├── requirements.lock
│   ├── alembic/
│   └── app/
│       ├── main.py              # FastAPI app factory only
│       ├── config.py            # pydantic-settings, 12-factor
│       ├── api/v1/              # villages terrain hydrology rainfall land
│       │                        # runoff pond suitability analysis reports
│       ├── schemas/             # Pydantic request/response models
│       ├── services/            # ← DOMAIN LOGIC — no FastAPI imports in here
│       │   ├── terrain.py  hydrology.py  rainfall.py  land.py
│       │   └── runoff.py   pond.py       suitability.py
│       ├── providers/           # ← EXTERNAL ADAPTERS (HLD ADR-4)
│       │   ├── base.py          #   Protocol + fallback-chain runner
│       │   ├── elevation/       #   copernicus_aws  aws_terrain  opentopodata  bhoonidhi
│       │   ├── rainfall/        #   open_meteo  nasa_power  imd_gridded  chirps
│       │   ├── landcover/       #   worldcover  bhuvan
│       │   ├── soil/            #   soilgrids
│       │   └── vector/          #   overpass  nominatim  shrug
│       ├── core/                # crs.py (CRSGuard)  cache.py  errors.py  logging.py
│       ├── db/                  # models.py  session.py  repositories/
│       ├── workers/             # celery_app.py  tasks.py
│       └── tests/
│           ├── unit/            # scs_cn  prismoidal  ahp  crs
│           ├── golden/          # synthetic cone / V-valley DEM fixtures
│           ├── property/        # hypothesis invariants
│           ├── integration/
│           └── cassettes/       # VCR.py recorded external responses
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── api/client.ts        # typed fetch layer
│       ├── components/          # VillageSearch MapView LayerPanel ResultPanel
│       │                        # RainfallChart StageStorageChart SiteCard ProgressBar
│       ├── hooks/               # useAnalysis  useLayers
│       ├── i18n/                # en.json (hi.json later — HLD NFR-15)
│       └── fixtures/            # analysis-result.sample.json ← the API contract
└── data/
    ├── seed/                    # shrug_villages.geojson, lgd_villages.csv
    └── cache/                   # DEM COGs, warmed demo cache (gitignored)
```

**Hard rule:** `services/` must never import FastAPI, and `api/` must never contain domain maths.
That single boundary is what makes the code reviewable and the unit tests possible.

---

# 5. Phase Plans

`↳` marks a sub-task or dependency. Effort in hours.

## M0 — Foundation · 27.5 h

**Goal:** `docker compose up` gives a green `/health`, and every API key is in hand or applied for.
**Exit criteria:** a commit on `main` that runs from a fresh clone in one command; CI green.

| ID | Task | Effort | Status |
|---|---|---|---|
| M0-1 | Register optional API keys — **none of them block any phase** | 0.5 h | `[x]` |
| M0-1a | ↳ ~~OpenTopography key~~ — **no longer needed**, see the box below | 10 m | `[-]` |
| M0-1b | ↳ data.gov.in API key (instant) — official IMD district figures in M4 | 10 m | `[-]` |
| M0-1c | ↳ Copernicus Data Space account (OAuth2) — Sentinel-2 NDWI labels in M9 | 10 m | `[-]` |
| M0-1d | ↳ **Bhuvan account request** — needed for M8 | 10 m | `[-]` |
| M0-1e | ↳ **Bhoonidhi API access email** (`bhoonidhi@nrsc.gov.in`) — needed for M8 | 10 m | `[-]` |
| M0-1n | ↳ **All four dropped, by decision.** The project is keyless by design. No code ever read these keys; every capability they gated already had an open equivalent in production (WorldCover for Bhuvan LULC, GLO-30 + the uploaded sheet for CartoDEM, Open-Meteo + NASA POWER for IMD). The empty config fields were removed — five `missing: KEY` lines in `/health/ready` described features that did not exist. | — | `[-]` |
| M0-2 | `.gitignore`, first commit, push to a remote | 0.5 h | `[~]` |
| M0-3 | Repo tree per §4 with empty modules | 1 h | `[x]` |
| M0-4 | `backend/Dockerfile` on `ghcr.io/osgeo/gdal:ubuntu-small` | 2 h | `[x]` |
| M0-5 | `docker-compose.yml`: api, postgis, redis | 2 h | `[x]` |
| M0-6 | FastAPI app factory + `/health` + settings from env | 1 h | `[x]` |
| M0-7 | Alembic init + first migration (villages, dem_assets, analyses) | 2 h | `[x]` |
| M0-8 | `pyproject.toml`: ruff + black + mypy + pytest config | 0.5 h | `[x]` |
| M0-9 | GitHub Actions: lint + mypy + pytest on push | 0.5 h | `[x]` |
| M0-10 | `core/crs.py` — `CRSGuard` + `utm_epsg_for(lon)` **+ its unit test** | 1.5 h | `[x]` |
| M0-11 | Seed village index + admin boundaries for **Chhattisgarh** into PostGIS | 4 h | `[x]` |
| M0-11b | ↳ `core/names.py` — transliteration fold + Devanagari, the CH-24 answer | 2.5 h | `[x]` |
| M0-11c | ↳ migration 0002: `admin_areas`, nullable village `geom`, `boundary_level` | 1 h | `[x]` |
| M0-11d | ↳ SHRUG polygons: drop into `data/seed/` and re-seed at `boundary_level='village'` | 2 h | `[ ]` |
| M0-12 | `Makefile`: up, down, test, lint, seed, logs | 0.5 h | `[x]` |
| M0-13 | `.env.example` with every key documented | 0.5 h | `[x]` |
| M0-14 | **Verify dev prerequisites** (box below) | 0.5 h | `[x]` |
| M0-15 | **Select and record test villages** — Chhattisgarh/Durg chosen, 2 of 3 screened (below) | 1 h | `[~]` |
| M0-16 | `core/errors.py` **RFC 7807 problem-details middleware** + structlog JSON logging with request ids | 2 h | `[x]` |
| M0-17 | **Fail-fast config validation** on startup — missing key → clear error, not a 500 later | 0.5 h | `[x]` |
| M0-18 | `frontend/Dockerfile` + **nginx reverse proxy** config + CORS (HLD §9) | 2 h | `[x]` |
| M0-19 | Add **celery-worker** and **titiler** services to compose (wired in M2/M6) | 1 h | `[x]` |
| M0-20 | ★ **Learning spike** — pysheds + rasterio on a toy DEM before M1 | 3 h | `[ ]` |

> **★ No API key is required to build or run Ring 1.** The DEM — the one genuinely load-bearing
> external dataset — comes from the **Copernicus GLO-30 AWS Open Data bucket**: no key, no
> registration, no quota (HLD §4.2 A1). Verified working: a 1°×1° COG at 1 arcsec, windowed to a
> 5×5 km AOI in **101 KB**, reporting 494.6 m at Bhopal against 495.0 m from two independent point
> APIs. OpenTopography served the *same* COP30 data behind a key, so it is now optional (A1b) and
> nothing depends on it.
>
> Every remaining credential gates an **enrichment**, never the core pipeline:
> `DATA_GOV_IN_API_KEY` → official IMD district rainfall · `COPERNICUS_*` → Sentinel-2 NDWI labels
> for M9 · `BHUVAN_TOKEN` / `BHOONIDHI_API_KEY` → the India-native layers in M8. With none of them
> set, all eight mandatory FRs still work end to end.
>
> **M0-1d/1e — apply now even though M8 is far off.** ISRO approvals take time, and applying early
> costs ten minutes. By the time you reach M8 the accounts will be live. This is the one place where
> having no deadline is a pure advantage over the earlier plan: the India-native sources the HLD
> designs around become genuinely available.
>
> **M0-14 — dev prerequisites.** Docker + Compose v2; **≥ 8 GB RAM** (a 25 km² AOI with six float32
> derivatives will OOM below this — HLD §9.1); **≥ 10 GB free disk** for DEM COGs; Python 3.11 for
> local tooling only, since all geo work happens inside the container.
>
> **M0-15 — test village selection.** This choice quietly determines whether everything downstream
> works.
> - **Relief ≥ 20 m across the AOI** — flat terrain breaks D8 flow routing (HLD CH-2)
> - **A visible existing pond or tank nearby** — free validation, and a strong viva answer
> - **Not coastal, not urban** — avoids no-data DEM cells and an exclusion mask that erases every parcel
> - Two different districts, so you can show the tool is not tuned to one place

### Selected: Chhattisgarh → Durg district

The area is the one the supplied `contours_1m.kml` actually covers: its centroid
reverse-geocodes to **Khapri, Durg Tahsil, Durg, Chhattisgarh**. That is a
useful coincidence rather than a convenience — the graded contour-map phase and
the village-search phase then exercise the same ground, and the two independent
elevation sources can be compared over it (contour relief 31.0 m against
Copernicus GLO-30's 30.3 m across the same 8.5 km²).

Every figure below is measured, not assumed: `scripts/screen_sites.py` samples
Copernicus GLO-30 and ESA WorldCover over a 2.5 km box at each location and
applies the criteria above. Re-run it with `python scripts/screen_sites.py`.

| Site | Position | Relief | Built-up | Open water | Verdict |
|---|---|---|---|---|---|
| **Khapri** (the sample map) | 21.25170, 81.29703 | 21.2 m | 10.9 % | 8.14 % | **suitable** |
| **Jevra Sirsa** | 21.24645, 81.30610 | 23.6 m | 12.9 % | 2.67 % | **suitable** |
| Bhilai *(reference)* | 21.21207, 81.37328 | 22.6 m | **81.1 %** | 1.47 % | rejected — urban |
| Durg town *(reference)* | 21.19830, 81.40079 | 27.4 m | **65.7 %** | 0.16 % | rejected — urban |

Two findings worth recording rather than smoothing over:

1. **Bhilai fails the non-urban screen at 81.1 % built-up**, and Durg town at
   65.7 %. Both were measured deliberately, as references: a screen that cannot
   reject a steel city is not a screen. They stay useful as *negative* test
   cases — the exclusion mask should erase essentially every parcel there, and
   an `UnanswerableProblem` is the correct response, not a low-scoring site.
2. **Kutelabhata is resolved — by the register, not by OpenStreetMap.** Nominatim
   returned nothing for nine spellings including Devanagari (कुटेलाभाठा), and an
   Overpass name search across the district found no matching `place` node. The
   Census 2011 register seeded in M0-11 has it immediately:

   | | |
   |---|---|
   | Census spelling | **kutelabhatha** — with the `h` |
   | SHRID | `11-22-409-03317-442569` |
   | Hierarchy | Durg sub-district, Durg district, Chhattisgarh |

   Two things follow. The village code **442569** sits directly beside Khapri's
   **442570**, and consecutive Census codes are geographic neighbours — so
   Kutelabhatha adjoins the ground the sample contour map covers, and Khapri
   already passed the screen. And the name fold built for CH-24 matches the
   spelling anyone would actually type: `kutelabhata`, `Kutelabhaata` and
   `कुटेलाभाठा` all score **1.00** against the register's `kutelabhatha`.

   What is still missing is a *coordinate*, because the open register carries
   names and codes but no geometry (Decision 36). It cannot be screened on
   relief and land cover until either SHRUG's polygons are dropped in (M0-11d)
   or the point is surveyed. No coordinate has been invented for it.

   The third recorded name, **Sirsa Khurd** (`...-442629`), is in the same
   sub-district — almost certainly the "Sisra" that was recorded.

That second point was the case for M0-11 in miniature, and M0-11 has now settled
it: OpenStreetMap carries **ten unnamed `place=hamlet` nodes and exactly one
named village** inside the surveyed area, while the Census register carries 83
named villages for Durg sub-district alone. OSM is not the village register for
rural India, and the plan was right not to treat it as one.

**Third site still to choose.** Two suitable villages are recorded; the criteria
ask for three, in two districts. Once SHRUG is seeded, `screen_sites.py` can be
pointed at candidates in a neighbouring district (Rajnandgaon or Bemetara) to
satisfy the "not tuned to one place" requirement:

```bash
python scripts/screen_sites.py 81.03,21.10 81.53,21.72   # lon,lat pairs
```

---

## M1 — ★ Walking Skeleton · 20 h

> **Mostly delivered by phase MC, and in a better form.** MC needed the same
> hydrology, so it built it properly rather than as a throwaway skeleton: real
> providers instead of hard-coded constants, a real endpoint instead of a script.
> Tasks below are marked `[x]` where MC delivered them and `[-]` where MC made
> them unnecessary. **M1-1 is the one genuinely outstanding item** — and it is the
> task that turns ADR-7 from a claim into a demonstration, because a protocol with
> one implementation proves nothing.

**Goal:** real numbers for a real village, end to end, in one script.
**Exit criteria:** printed JSON with a plausible catchment area, rainfall, runoff and capacity, all
four hand-checked. Tag `v0.1-skeleton`.

| ID | Task | Effort | Status |
|---|---|---|---|
| M1-1 | `providers/elevation/copernicus_aws.py` — bbox → tile list → windowed COG read → mosaic | 2.5 h | `[x]` |
| M1-2 | `services/interpolate.py` + `hydrology.py`: reproject to UTM, Priority-Flood + ε  **[delivered by MC-7 / MC-9]** | 4 h | `[x]` |
| M1-3 | `services/hydrology.py`: D8 flow direction → flow accumulation  **[delivered by MC-9]** | 2 h | `[x]` |
| M1-4 | ↳ catchment delineation + snapping + morphometrics  **[delivered by MC-9]** | 3 h | `[x]` |
| M1-5 | `providers/rainfall/open_meteo.py` — 30-yr daily + ET₀  **[delivered by MC-16]** | 1.5 h | `[x]` |
| M1-6 | `services/runoff.py`: SCS-CN, composite CN, Ia = 0.3S, daily→annual  **[delivered by MC-17]** | 2 h | `[x]` |
| M1-7 | `services/pond.py`: stage–storage + prismoidal + depth choice  **[delivered by MC-18]** | 1 h | `[x]` |
| M1-8 | ~~`scripts/skeleton.py`~~ — superseded: `/analyzeContour` is the working end-to-end path | 1 h | `[-]` |
| M1-9 | ~~`POST /api/v1/analysis`~~ — superseded by `POST /api/v1/analyzeContour` (MC-11) | 1 h | `[-]` |
| M1-10 | **Hand-check every number against HLD §6.9**  **[done: unit tests reproduce it]** | 2 h | `[x]` |
| M1-11 | ~~frontend fixture~~ — superseded: `demo_output/analysis.json` + the OpenAPI example (MC-23) | 0.5 h | `[-]` |

> **M1-10 is the highest-value hour in the project.** HLD §6.9 is a fully worked example with verified
> arithmetic — run the same inputs and confirm you reproduce it. Silent unit errors (mm vs m, ha vs m²,
> degrees vs metres) are the top failure mode here (HLD CH-10), and they compound: every phase after
> this builds on these numbers. If your runoff coefficient exceeds ~0.6 for a rural catchment, you
> applied SCS-CN to annual rainfall instead of the daily series.
>
> **M1-11** makes the skeleton's real output the **API contract**, so frontend work in M2 is never
> blocked on a backend endpoint. If a field name later changes, update the fixture in the same commit.

---

## MC — ★ Contour-Map Catchment API · 58 h · **graded deliverable, current priority**

**Why this phase exists.** A separate assignment phase requires a route that accepts a contour map
(KML/KMZ), analyses the terrain, identifies a pond location and returns catchment information as JSON —
demonstrated on a supplied sample. Graded on: *working endpoint · code extensibility to future phases ·
report documentation · catchment identification and estimation*.

**Why it slots in here rather than being bolted on.** Per **HLD ADR-7** a contour map is simply another
`ElevationSource`. This phase builds **one new adapter** and **one new endpoint**, and *reuses* the
hydrology from M1/M3 unchanged. It also pulls a terrain-only slice of M6's siting forward. That reuse
is exactly what the "extensibility" criterion asks to see.

**Sample input, measured** (`contours_1m.kml` — do not encode any of this in code):
1,355 LineStrings · 159,113 vertices · 32 levels · **1.0 m interval, 267–298 m** · elevation in
`<Placemark><name>`, coordinates 2D · extent 3.24 × 2.63 km ≈ 8.52 km² · centroid 81.2966 °E 21.2519 °N.

**Exit criteria — every graded item, explicitly:**

| Graded requirement | Satisfied by | Evidence |
|---|---|---|
| Working API endpoint | MC-11, MC-20 | **runs locally**: `docker compose up` → `http://localhost:8000/api/v1/analyzeContour`, verified from a clean clone; `/docs` self-demonstrating (MC-23) |
| Accepts contour map as upload | MC-2…MC-5, MC-10 | multipart KML **and** KMZ, 4 elevation strategies |
| Analyses contour/terrain | MC-7 | TIN interpolation → DEM → sink fill → D8 → flow accumulation |
| Identifies pond location | MC-9 | scored siting + DBSCAN + NMS, weights from the AHP vector |
| Estimates catchment area | reuses M1-4 / M3-1 / M3-4 | polygon + area in ha/km² + morphometrics |
| Returns structured JSON | MC-1, MC-19 | typed Pydantic response, `analysis_tier`, provenance |
| Demonstrated on the sample | MC-15, MC-22 | integration test + one-command curl script |
| **No hard-coding; generalises** | **MC-13, MC-14** | synthetic KMLs with *analytic* answers; 4-strategy matrix test |
| Code extensibility | MC-8 (ADR-7) | one `ElevationSource` protocol; hydrology reused unmodified |
| Report documentation | MC-21, MC-24 | repo README + phase report with API docs |
| *Beyond the minimum* | MC-16…MC-18 | rainfall, runoff volume and pond capacity — the actual pond-planning answer |

| ID | Task | Effort | Status |
|---|---|---|---|
| MC-1 | `schemas/contour.py` — request/response models for the new route | 1 h | `[x]` |
| MC-2 | ★ `providers/elevation/contour_kml.py` — **KML/KMZ parser** | 4 h | `[x]` |
| MC-3 | ↳ elevation resolution: z-ordinate → `ExtendedData` → `<name>` → folder name, **strategy recorded** | 2 h | `[x]` |
| MC-4 | ↳ KMZ (zip) handling, namespace-agnostic parse, MultiGeometry | 1.5 h | `[x]` |
| MC-5 | ↳ validation with **specific** failure reasons (levels < 2, too few lines, bad coords, unresolved > 10 %) | 1.5 h | `[x]` |
| MC-6 | ↳ derive interval / extent / UTM zone / resolution **from the data** | 1.5 h | `[x]` |
| MC-7 | ★ `services/interpolate.py` — densify → TIN (`LinearNDInterpolator`) → hull clip → de-terrace → COG | 5 h | `[x]` |
| MC-8 | `ElevationSource` protocol; make `copernicus_aws` and `contour_kml` interchangeable (**ADR-7**) | 2 h | `[x]` |
| MC-9 | Terrain-only pond siting: flow-acc + depression depth + slope + concavity → DBSCAN → best cell | 4 h | `[x]` |
| MC-10 | `POST /terrain/contour-map` (upload → `dem_id`) + `GET …/contours` GeoJSON echo | 2 h | `[x]` |
| MC-11 | ★ `POST /analyzeContour` (+ `/findCatchment` alias) — one-shot orchestration | 2.5 h | `[x]` |
| MC-12 | Upload hardening: 50 MB cap, zip-bomb guard, XML entity-expansion (billion-laughs) defence | 1.5 h | `[x]` |
| MC-13 | ★★ **Generalisation test**: synthetic KMLs (cone, tilted plane, twin basins) with *known* answers | 3 h | `[x]` |
| MC-14 | ↳ elevation-strategy matrix test: same terrain expressed 4 different ways → same result | 1.5 h | `[x]` |
| MC-15 | Integration test on the supplied sample; snapshot the response as a fixture | 1.5 h | `[x]` |
| MC-16 | ★ **Location-derived enrichment** — AOI centroid → rainfall (Open-Meteo) + soil HSG (SoilGrids) + LULC (WorldCover), all keyless | 4 h | `[x]` |
| MC-17 | ↳ **Composite CN + SCS-CN runoff** for the delineated catchment (reuses M4-9/M4-10) | 3 h | `[x]` |
| MC-18 | ↳ **Pond depth + storage capacity** from the DEM stage–storage curve (reuses M5-7/M5-9) | 3 h | `[x]` |
| MC-19 | ↳ **3-tier degradation ladder** `full` / `no_soil_lulc` / `terrain_only` + `layers_used[]` (HLD §6.10.5) | 2 h | `[x]` |
| MC-20 | ★ **Local-run verification** — clean clone → `docker compose up` → sample analysed, on a machine with nothing pre-installed | 2 h | `[x]` |
| MC-21 | ★ **Repo presentation** — public README: what it does, quickstart, architecture diagram, endpoint table | 2 h | `[x]` |
| MC-22 | ★ **Reproducible demo** — `scripts/demo_contour.sh` (curl + jq) and a Postman/Bruno collection | 1.5 h | `[x]` |
| MC-23 | OpenAPI polish — request/response `examples` on the new routes so `/docs` is self-demonstrating | 1 h | `[x]` |
| MC-24 | **Write the phase report** `docs/REPORT_CONTOUR_API.md` — repo link, local URL + run steps, approach, demo, API docs | 4 h | `[x]` |

> **MC-13/MC-14 are what the "extensibility" and "no hard-coding" criteria are actually graded on.**
> A synthetic contour KML of an inverted cone has an *analytically known* catchment — the whole surface
> drains to the centre — so the test proves the pipeline derives its answer from the input. MC-14 goes
> further: the same synthetic terrain emitted four times, with the elevation in the z-ordinate, in
> `ExtendedData`, in `<name>` and in the folder name, must yield the **same** catchment. That is a much
> stronger claim than "it works on the sample".
>
> **Reused unchanged from other phases** — the extensibility argument in one line: `core/crs.py`,
> `core/units.py` (M0) · sink filling and reprojection (M1-2) · D8 flow direction and flow accumulation
> (M1-3) · catchment delineation, snapping, morphometrics (M1-4, M3-1, M3-4) · COG writing (M2-3).
> MC adds an input adapter and an endpoint; it does not fork the pipeline.
>
> **This API is never deployed — it runs locally, by design.** There is no hosting target, free tier or
> tunnel in scope. Where the brief asks for a "working API route URL", the answer is
> `http://localhost:8000/api/v1/analyzeContour` **plus one-command run instructions an evaluator can
> reproduce**: `cp .env.example .env && docker compose up`, then `scripts/demo_contour.sh` (MC-22).
> MC-20 substitutes a **clean-clone bring-up check** for a deployment smoke test — arguably a stronger
> guarantee, since it proves the whole stack reproduces from source rather than that one server happens
> to be running.
>
> **MC returns the complete pond-planning answer, not a terrain-only subset.** A contour map carries no
> rainfall, soil or land-cover attribute — but it carries its own **position**, and every remaining
> layer is reachable from a coordinate with no credential (HLD §6.10.4): Open-Meteo ERA5-Land rainfall,
> SoilGrids → Hydrologic Soil Group, ESA WorldCover LULC, Overpass for existing water bodies. So the
> response includes composite Curve Number, SCS-CN runoff volume, and recommended pond depth and
> capacity. The brief asks for *"the catchment information required for pond planning"* — that is the
> whole of it, not just an area.
>
> **Degradation is a defined ladder, not a compromise** (HLD §6.10.5): `full` → `no_soil_lulc` →
> `terrain_only`, with `analysis_tier` and `layers_used[]` in every response. Tier 3 is the offline
> floor and still answers the graded question from the contour map alone — so the endpoint works with
> no network at all.

---

## M2 — FR-1 + FR-2: Map, Village, DEM Tiles, Contours · 23.5 h

**Exit criteria:** type a village name → imagery loads → toggle contours → they are labelled and
follow the terrain. FR-1 and FR-2 both `[x]`.

**Reached so far:** the contour-upload path is complete end to end — drop a KML on the page, watch
the analysis run, and read the catchment, runoff and pond sizing off the map and the result panel,
with a browser test that proves it (M2-16). What remains is the *village-search* entry point
(M2-1, M2-2, M2-8), which is blocked on M0-15, and the raster derivative layers (M2-3, M2-4,
M2-9b). The UI is already useful without them; they add a second way in, not a second answer.

| ID | Task | Effort | Status |
|---|---|---|---|
| M2-1 | `GET /villages/search` — `pg_trgm` fuzzy over the shared name fold, Devanagari input | 2.5 h | `[x]` |
| M2-2 | `GET /villages/{id}` + `/boundary` + `/imagery` (Esri tile template) | 1 h | `[x]` |
| M2-2b | `GET /villages/resolve?lon=&lat=` — reverse geocode to sub-district | 0.5 h | `[x]` |
| M2-2c | ↳ **Gram Panchayats** from the CC0 crosswalk: 11,532 seeded, many-to-many link, Census-2001 codes | 3 h | `[x]` |
| M2-2d | ↳ village-level LGD codes still outstanding — needs an `lgdirectory.gov.in` registration (HLD E2) | 2 h | `[ ]` |
| M0-7b | **Schema-drift guard**: `alembic check` clean, `include_object` hardened, migration 0004 reconciles 0001 with the models, drift test added | 2.5 h | `[x]` |
| M2-3 | Write DEM + derivatives as **COG** (GDAL native driver); TiTiler in compose, health-checked | 3 h | `[x]` |
| M2-4 | `POST /terrain/derivatives` — Horn slope + hillshade, content-addressed | 2 h | `[x]` |
| M2-5 | `POST /terrain/contours` — marching squares → GeoJSON, Douglas–Peucker | 3 h | `[x]` |
| M2-6 | ↳ index contours (every 5th) + `elevation_m` attribute for labelling | 1 h | `[x]` |
| M2-6b | ↳ **round-trip golden test**: survey → DEM → contours, residual median 0.109 m | 1.5 h | `[x]` |
| M2-7 | React + Vite + MapLibre shell, Esri World Imagery basemap | 3 h | `[x]` |
| M2-8 | `VillageSearch` autocomplete → fit-to-bounds + labelled boundary outline | 2 h | `[x]` |
| M2-9 | `LayerPanel` toggles: basemap / contours / catchment / sites / survey extent | 2 h | `[x]` |
| M2-9b | ↳ Slope and Shaded-relief map layers, off by default | 0.5 h | `[x]` |
| M2-10 | Externalise UI strings to `i18n/en.json` from the start (HLD NFR-15) | 0.5 h | `[x]` |
| M2-11 | Frontend: TanStack Query + Zustand + **typed API client generated from OpenAPI** | 2 h | `[ ]` |
| M2-12 | **Map legend** + **data attribution** for Esri / OSM / ESA / ISRO (licence requirement) | 1.5 h | `[x]` |
| M2-13 | Upload panel: drag-and-drop KML/KMZ, options, cancel via `AbortController` | 1.5 h | `[x]` |
| M2-14 | Result panel: tier banner, provenance, ranked sites, criteria breakdown | 2 h | `[x]` |
| M2-15 | Multi-stage frontend image (Vite build → nginx) + `.dockerignore` | 0.5 h | `[x]` |
| M2-16 | **End-to-end browser test**: real Chrome over CDP uploads the map, asserts the render | 2.5 h | `[x]` |

---

## M3 — FR-4: Hydrology & Catchment · 20.5 h

The 20-mark subsystem. **Do not rush this phase** — with no deadline there is no reason to.

**Exit criteria:** clicking three different points gives three plausible, visibly different catchments;
golden tests pass; one real catchment within ±15 % of a SLUSI/toposheet reference.

**Reached so far.** The first two are met and asserted, not claimed: three clicks on the bundled
survey return **7.70 ha, 1.24 km² and 1.99 km²**, and `TestClickToDelineate` in the browser suite
fails if any two agree. 99 golden tests cover the hydrology, the streams and the contour round trip.
The third criterion needs a SLUSI sheet or toposheet for the Durg area to compare against (M3-8) —
that is a document to obtain, not code to write.

**M3-5 is blocked, not skipped.** Auto-expanding the AOI and recomputing assumes the DEM can be
re-fetched over a larger area. That holds for the Copernicus path, where the buffer is a request
parameter — but the contour path's extent is the surveyed sheet, and there is no more of it to ask
for. Expanding it would mean extrapolating beyond the survey and presenting the result as
measurement. What the contour path does instead is report `touches_survey_edge`, which states that
the area is a lower bound. M3-5 becomes implementable once a catchment endpoint exists over the
remote DEM source (M1-1 territory); until then there is nothing for it to expand.

| ID | Task | Effort | Status |
|---|---|---|---|
| M3-1 | **Pour-point snapping** to max flow accumulation; returns `snapped.distance_m` | 2 h | `[x]` |
| M3-2 | Flat-terrain handling: detect flat fraction → breach vs fill | 1.5 h | `[x]` |
| M3-3 | `POST /hydrology/streams` — threshold + vectorise + Strahler order | 1.5 h | `[x]` |
| M3-4 | Morphometrics: perimeter, relief, mean slope, longest flow path, **Kirpich Tc**, form factor, drainage density | 3 h | `[x]` |
| M3-5 | AOI-boundary check → auto-expand buffer ×2 and recompute once | 1 h | `[!]` |
| M3-6 | `quality{}` block: dem_source, resolution, confidence, warnings | 0.5 h | `[x]` |
| M3-7 | ★ **Golden tests on synthetic DEMs** — 99 golden tests, hydrology + streams + round-trip | 4 h | `[x]` |
| M3-8 | Validate a real catchment against SLUSI / toposheet, ±15 % | 3 h | `[ ]` |
| M3-9 | Frontend: draw catchment + snapped point + Strahler-weighted streams | 2.5 h | `[x]` |
| M3-9b | ↳ click-to-delineate anywhere on the map + `POST /hydrology/catchment` | 1 h | `[x]` |
| M3-10 | Property test: catchment area monotonic as the pour point moves downstream | 0.5 h | `[x]` |
| M3-11 | Alembic migration: `catchments`, `streams` tables + GIST indexes | 1 h | `[x]` |

> **M3-7 is the single highest-value test in the project** — it *proves* the D8 implementation rather
> than merely exercising it, and it is the best possible answer to *"how do you know your hydrology is
> correct?"*
>
> **Use these four surfaces. Each has an exact, not approximate, expected answer:**
>
> | Synthetic DEM | `z(x, y)` | Pour point | Expected catchment | What it proves |
> |---|---|---|---|---|
> | **Tilted plane** | `z = −a·y` | a cell on the bottom edge | **exactly the column of cells above it** (`n_rows` cells) | Flow direction is correct and does not fan out |
> | **Inverted cone / bowl** | `z = r²` | the centre cell | **exactly every cell in the domain** | Convergence and accumulation are correct |
> | **V-valley tilted along its axis** | `z = s·\|x−x₀\| + t·(n−y)` | outlet on the valley floor | **the whole raster** | Two hillslopes merge into one channel |
> | **Two bowls split by a ridge** | two paraboloids + a ridge | centre of bowl A | **only bowl A's cells** | The divide is respected — no leakage across the ridge |
>
> An **upright** cone is the wrong test: flow diverges radially, so the catchment of a flank cell is a
> thin ragged strip whose area has no clean closed form. It is still worth asserting as a *negative*
> case — a flank cell must have a **small** catchment — but the four surfaces above are where the
> exact assertions live. Expect **exact cell counts**, not "within 5 %", for cases 1, 2 and 4.

---

## M4 — FR-5 + FR-6: Rainfall & Runoff · 31.5 h

**Exit criteria:** 30 years of real rainfall for a test village; a composite CN with a visible
LULC×HSG breakdown; runoff coefficient in a plausible 0.15–0.45 band.

**Mostly delivered by phase MC**, which needed the whole runoff chain to answer the contour-map
assignment: SoilGrids→HSG, WorldCover, zonal LULC×HSG, composite CN with AMC, SCS-CN on the daily
series, dependable-rainfall statistics, the derived monsoon window, and concurrent enrichment are all
in place and tested against HLD §6.9. All three exit criteria are met on the bundled sample: 30
complete years (1996–2025), CN 87.7 from a visible cropland/HSG-D breakdown, and C = 0.254.

What M4 adds beyond MC is the *cross-checking* (M4-11) and a second rainfall source with an
ensemble (M4-1, M4-2, M4-5, M4-15).

| ID | Task | Effort | Status |
|---|---|---|---|
| M4-1 | `providers/rainfall/nasa_power.py` (second source, supplies `T2M`) | 1 h | `[x]` |
| M4-2 | Ensemble: median across sources, inter-source σ as uncertainty | 1 h | `[x]` |
| M4-3 | `GET /rainfall/statistics`: mean, CV, **50/75/90 % dependable** (Weibull) | 2.5 h | `[x]` |
| M4-4 | Monthly normals + **region-aware monsoon window** (SW vs NE) | 1.5 h | `[x]` |
| M4-5 | Rainfall cache table + upsert; never re-fetch `(cell, source, date)` | 1.5 h | `[x]` |
| M4-6 | `providers/soil/soilgrids.py` → USDA texture → **HSG A/B/C/D** | 2 h | `[x]` |
| M4-7 | `providers/landcover/worldcover.py` → LULC raster for the AOI | 4 h | `[x]` |
| M4-8 | Zonal stats over the catchment: (LULC × HSG) area table | 3 h | `[x]` |
| M4-9 | **Composite CN** from the Indian CN table + AMC I/II/III adjustment | 3 h | `[x]` |
| M4-10 | SCS-CN on the **daily** series → monthly + annual + 75 %-dependable design year | 2 h | `[x]` |
| M4-11 | Indian cross-checks: Inglis-DeSouza / Khosla / Barlow + Rational peak, by region | 3 h | `[x]` |
| M4-11b | ↳ **Strange (1928) needs its published table** — a tabulation, not a formula | 1 h | `[!]` |
| M4-11c | ↳ mean monthly temperature from the rainfall source, to evaluate Khosla | 0.5 h | `[x]` |
| M4-12 | Unit tests: reproduce HLD §6.9; `Q=0` when `P ≤ Ia`; Weibull; HSG boundaries | 1.5 h | `[x]` |
| M4-13 | Frontend: rainfall bar chart + runoff stat cards | 2.5 h | `[x]` |
| M4-14 | ★ **Fetch rainfall / soil / LULC / OSM concurrently** (`asyncio.gather`) — see §1.4 | 2 h | `[x]` |
| M4-15 | Alembic migration: `rainfall_cache` with `(cell,source,date)` unique index | 1 h | `[x]` |

---

## M5 — FR-3 + FR-7: Land Availability & Pond Design · 25.5 h

**Exit criteria:** parcels render; selecting one yields depth, dimensions, capacity and cost that
reproduce HLD §6.9 Step 6 by hand.

| ID | Task | Effort | Status |
|---|---|---|---|
| M5-1 | `providers/vector/overpass.py` — buildings, roads, water, landuse | 2.5 h | `[x]` |
| M5-2 | Exclusion mask: buffers (bldg 50 m, road 20 m, water 100 m) + slope > 5 % | 3 h | `[x]` |
| M5-3 | Inclusion mask from WorldCover bare / sparse / grass / shrub | 1 h | `[x]` |
| M5-4 | **OpenCV** morphological open/close + `connectedComponentsWithStats` | 1.5 h | `[x]` |
| M5-5 | Parcel vectorise + attributes (area, slope, LULC, HSG, distances) | 1.5 h | `[x]` |
| M5-6 | `POST /land/available` returning a parcel FeatureCollection | 1 h | `[x]` |
| M5-7 | **Stage–storage–area curve** by flood-fill from the site point | 4 h | `[x]` |
| M5-8 | Prismoidal geometry + side-slope closure constraint | 1 h | `[x]` |
| M5-9 | **Depth optimiser**: bounded search, `V ≤ 0.30 × runoff`, d ∈ [2.5, 4.5] | 2.5 h | `[x]` |
| M5-10 | Cost estimate + spillway width + silt dead storage + freeboard | 1.5 h | `[x]` |
| M5-11 | ★ Report **which constraint binds** (parcel / depth / runoff / budget) | 1 h | `[x]` |
| M5-12 | Property tests: `V(d)` strictly increasing; closure rejection | 1 h | `[x]` |
| M5-13 | Frontend: parcels + pond footprint + stage–storage chart | 3 h | `[x]` |
| M5-14 | Alembic migration: `candidate_sites`, `pond_designs`, `runoffs` | 1 h | `[x]` |
| M5-1b | ↳ **OSM window cache** (disk, content-addressed) — public Overpass returned 504/502/429 and a clean 2.3 s answer for the same query minutes apart | 1.5 h | `[x]` |

> **M5-11 comes straight out of HLD §6.9 Step 7.** In the worked example the pond captures only 1.42 %
> of its catchment's yield — the binding constraint is the 2,700 m² parcel, not water availability.
> Reporting *which* constraint binds is what makes the output a planning recommendation instead of a
> calculator result. It is roughly an hour of work and it is the most useful sentence the tool emits.

---

## M6 — FR-8 + FR-9: Orchestration & AHP Suitability · 26.5 h

**Exit criteria:** one click runs the whole pipeline with a live progress bar, paints every layer, and
produces ranked sites with per-criterion breakdowns. Tag `v0.5-must-set`.

| ID | Task | Effort | Status |
|---|---|---|---|
| M6-1 | Celery + Redis wired; move `/analysis` to `202 + job_id` | 3 h | `[x]` |
| M6-2 | Task chain with per-step progress writes to Redis | 2 h | `[x]` |
| M6-3 | `GET /analysis/{id}/status` (`progress_pct`, `current_step`, `steps[]`) | 1 h | `[x]` |
| M6-4 | Job state machine incl. **`PARTIAL`** on optional-step failure (HLD §3.7) | 1.5 h | `[x]` |
| M6-5 | Result persistence (`analyses.result` JSONB) + `GET /result` | 1.5 h | `[x]` |
| M6-6 | Criterion normalisation (fuzzy membership) for the 9 AHP criteria | 3 h | `[x]` |
| M6-7 | AHP weights: eigenvector + **CI/CR check**, reject `CR ≥ 0.1` | 1.5 h | `[x]` |
| M6-8 | Weighted overlay → suitability raster + hard-constraint mask | 1.5 h | `[x]` |
| M6-9 | DBSCAN → representative points → NMS at 300 m → Top-N | 2.5 h | `[x]` |
| M6-10 | Per-criterion contribution breakdown in the site response | 1 h | `[x]` |
| M6-11a | `POST /suitability/weights/ahp` + `GET /suitability/weights` (matrix → eigenvector + CR; refuses `CR ≥ 0.1`) | 1 h | `[x]` |
| M6-11b | `POST /suitability/analyze` + `GET /suitability/{id}/sites` — accepts a caller's own weight vector *or* pairwise matrix, refused up front if incoherent | 1 h | `[x]` |
| M6-12 | Frontend: progress bar + all layers + `ResultPanel` + `SiteCard` list | 5 h | `[x]` |
| M6-13 | **Empty-state and error UX**: no sites found, provider failed, `PARTIAL` banner, no-data point | 2 h | `[x]` |

> **`PARTIAL` (M6-4) is what makes the system usable in practice.** If SoilGrids is unreachable you
> still have terrain, rainfall and a catchment — fall back to a default HSG, flag `data_quality`, and
> return a usable answer rather than failing the whole analysis (HLD NFR-5).

---

## M7 — Deliverables · 32 h

**Ring 1 completes here — the project is submittable.**
**Exit criteria:** a fresh clone runs in one command following `INSTALL.md` literally; a PDF report
downloads with real maps and non-placeholder numbers; **`TECHNICAL_REPORT.md` is complete including its
limitations section**; all five assignment deliverables are present. Tag `v1.0-ring1`.

> **M7 is the largest Ring 1 phase (32 h) and most of it is writing, not coding.** That is not a
> defect in the plan — documentation is 10 marks, the install guide and API docs are graded
> deliverables, and the technical report alone is a realistic 10 h. The temptation on a project with
> no deadline is to keep building and leave this phase perpetually "next". Resist it: M7 is what turns
> a working program into a submittable project.

| ID | Task | Effort | Status |
|---|---|---|---|
| M7-1 | Jinja2 + WeasyPrint PDF: maps, charts, methodology, assumptions | 5 h | `[x]` |
| M7-2 | Static map figure via matplotlib for the PDF — **contextily dropped**: it fetches basemap tiles, and a report generator that fails offline is worse than one drawn on white | 2 h | `[x]` |
| M7-3 | `POST /reports/generate` + `GET /{id}/download` | 1 h | `[x]` |
| M7-4 | `GET /export/{id}?format=geojson` (bundle all result layers) | 1.5 h | `[x]` |
| M7-5 | **`docs/API.md`** — OpenAPI export + curl examples for the ★ endpoints | 3 h | `[x]` |
| M7-5b | ↳ **API-doc drift test** — 9 of 24 routes had gone undocumented and nothing failed; the live OpenAPI schema is now the source of truth | 1 h | `[x]` |
| M7-6 | **`docs/INSTALL.md`** — clone → `.env` → `make up` → seed → open | 1.5 h | `[x]` |
| M7-7 | ★ Verify the install guide **on a fresh clone in a clean directory** | 1 h | `[x]` |
| M7-8 | `README.md`: architecture diagram, screenshots, FR→endpoint table | 2 h | `[x]` |
| M7-8b | ↳ **README endpoint-table drift guard** — 15 of 27 routes were missing; a README table that omits half the API reads as complete | 0.5 h | `[x]` |
| M7-9 | ★★ **Write `docs/TECHNICAL_REPORT.md`** — the graded 10-mark deliverable (outline §11.1) | 10 h | `[x]` |
| M7-10 | `DEMO_MODE=true` + `make demo` **cache warming** for the three test villages | 2 h | `[x]` |
| M7-10b | ↳ **Soil + land-cover disk cache** — `DEMO_MODE` was declared, echoed by `/health` and documented in INSTALL.md, but nothing in the code read it | 1.5 h | `[x]` |
| M7-11 | Self-hosted **OpenTopoData** container — unlimited local DEM, offline demo insurance | 1.5 h | `[-]` |
| M7-11n | ↳ **Deferred, with reason.** Both stated benefits have gone: there is no DEM-acquisition endpoint, and `providers/elevation/copernicus_aws.py` has no production consumer — every import from that package is `DemGrid` on the contour path. Offline demo insurance is delivered by `DEMO_MODE` + `make demo-warm` (M7-10). Building it now would add a compose service nothing calls. Revisit alongside a `POST /terrain/dem` endpoint. | — | `[-]` |
| M7-12 | Screenshots + screen recording of the full flow | 1.5 h | `[~]` |
| M7-12n | ↳ **Screenshots done** (6 current ones in `docs/images/`, referenced from the README) and the **flow rehearsed end to end** — §12.1 corrected against the running app, which revealed the old script did not match it. The **recording itself is yours to make**: it needs your narration, and a video is not something the build can produce. | — | `[~]` |

> **M7-7 is the task that actually earns the mark.** An installation guide nobody has followed from
> scratch is always wrong somewhere. Clone into `/tmp`, follow your own document literally, fix what
> breaks.

---

## M8 — India-Native Integration (ISRO Tier-1) · 30 h · Ring 2

**Why this phase exists:** HLD §4.2 designs the system around India-native authoritative sources, and
three requirements have **no usable global equivalent** — land category (Bhuvan *wasteland* is the
class India actually allots pond land from), existing structures (MGNREGA/Amrit Sarovar geotagged
assets), and water-table depth (CGWB). Ring 1 substitutes global proxies. This phase makes the
implementation match the design.

**Exit criteria:** the wasteland layer drives land category; CartoDEM cross-checks COP30; IMD gridded
is the rainfall anchor; every ISRO adapter degrades cleanly when its token is absent.

| ID | Task | Effort | Status |
|---|---|---|---|
| M8-1 | `BhuvanTokenService` — refresh on schedule + on 401, Redis-held, stampede-safe | 2.5 h | `[ ]` |
| M8-2 | `providers/landcover/bhuvan.py` — LULC 1:50k + **Wasteland** WMS/WCS | 5 h | `[ ]` |
| M8-3 | **State-parameterised land-category vocabulary** (gairan/gochar/shamlat/poramboke → internal taxonomy) | 2.5 h | `[ ]` |
| M8-4 | `providers/vector/bhuvan_mgnrega.py` — geotagged water-harvesting assets | 2.5 h | `[ ]` |
| M8-5 | ↳ use as an **exclusion layer** (don't duplicate an existing pond) | 1 h | `[ ]` |
| M8-6 | `providers/elevation/bhoonidhi.py` — STAC search → CartoDEM 30 m | 5 h | `[ ]` |
| M8-7 | ↳ **DEM cross-check report**: COP30 vs CartoDEM delta as stated uncertainty | 1.5 h | `[ ]` |
| M8-8 | `providers/rainfall/imd_gridded.py` via `imdlib` — harvest 30 yr into PostGIS | 4 h | `[ ]` |
| M8-9 | ↳ make IMD the **ensemble anchor**; bias-correct ERA5-Land against it | 1.5 h | `[ ]` |
| M8-10 | CGWB groundwater ingest → `water_table_depth` per district | 2 h | `[ ]` |
| M8-11 | ↳ wire into the M5-9 depth optimiser as a hard constraint | 1 h | `[ ]` |
| M8-12 | `NotConfigured` degradation tests for every ISRO adapter | 1.5 h | `[ ]` |

> **M8-11 closes a real gap.** The HLD constrains `d_max ≤ water_table − 1 m`, but in Ring 1 that
> value is operator-supplied because CGWB access is `MANUAL`. Cutting into a shallow water table turns
> a storage pond into a seepage pit, so this is a correctness issue, not a nicety.

---

## M9 — ML Suitability Layer & Explainability · 33.5 h · Ring 2

**Exit criteria:** ROC-AUC **≥ 0.75** under **spatial block** cross-validation (see the realism note
below); SHAP explanations render per site; fusion falls back to α = 1.0 (pure AHP) if the model misses
the floor.

| ID | Task | Effort | Status |
|---|---|---|---|
| M9-1 | Label harvest: MGNREGA assets + Amrit Sarovar → positive points | 3 h | `[ ]` |
| M9-2 | ↳ NDWI on a dry-season Sentinel-2 composite + Otsu → persistent water bodies | 8 h | `[ ]` |
| M9-3 | ↳ filter to 0.05–10 ha (drops rivers and puddles); OSM as the last source | 1.5 h | `[ ]` |
| M9-4 | Negative sampling: ≥ 500 m from any positive, **slope/LULC-stratified** | 2 h | `[ ]` |
| M9-5 | Feature extraction at every labelled point (14 features, HLD §6.5.4) | 4 h | `[ ]` |
| M9-6 | ★ **Spatial block cross-validation** — 5 folds of contiguous 10 km blocks | 2.5 h | `[ ]` |
| M9-7 | Train RandomForest; report ROC-AUC, PR-AUC, precision@10, **and the naive k-fold AUC beside it** | 2 h | `[ ]` |
| M9-8 | ↳ per-district label-coverage check; **exclude under-labelled districts** | 1.5 h | `[ ]` |
| M9-9 | SHAP TreeExplainer → per-site waterfall | 2.5 h | `[ ]` |
| M9-10 | Fusion `S = α·S_ahp + (1−α)·S_ml`, α = 0.6; **auto-fallback to 1.0** | 1.5 h | `[ ]` |
| M9-11 | `GET /suitability/{id}/sites/{site}/explain` | 1.5 h | `[ ]` |
| M9-12 | FR-14: plain-language justification paragraph per site | 2.5 h | `[ ]` |
| M9-13 | Model card: labels, features, metrics, known biases, intended use | 1 h | `[ ]` |

> **M9-6 is not optional and not interchangeable with plain k-fold.** Nearby points are spatially
> autocorrelated, so random folds leak and produce a falsely excellent AUC — a model that looks superb
> and generalises to nothing. Report **both** numbers: the gap between naive k-fold AUC and block-CV
> AUC is itself the evidence that you handled it correctly, and it is a strong viva answer.
>
> **Realism note on the target.** An earlier revision set the bar at ROC-AUC ≥ 0.80. That is optimistic
> for weakly-supervised suitability from terrain features under *honest* spatial validation — published
> work in this area typically lands in the 0.70–0.85 band, and naive validation is what produces the
> 0.90+ figures you see reported. The bar here is therefore:
>
> | Block-CV ROC-AUC | Decision |
> |---|---|
> | **≥ 0.75** | Ship the fused score (α = 0.6). Phase passes |
> | 0.70 – 0.75 | Ship at reduced weight (α = 0.8), state the metric in the report |
> | **< 0.70** | **Fall back to α = 1.0 — pure AHP.** Phase still passes: a documented, transparent MCDA beats a weak model, and HLD §6.5.4 already specifies this degradation |
>
> Framed this way the phase cannot fail — it either adds a validated model or it produces a documented
> negative result, which is a legitimate outcome and better science than tuning until the number looks
> good.
>
> **M9-8 guards against the label bias in HLD CH-26.** OSM's rural-India water-body coverage skews
> toward well-mapped districts; training on it unchecked teaches the model "where mappers go".

---

## M10 — Full API Surface · 39.5 h · Ring 2

Brings the implementation up to HLD §5.2's 72 endpoints and delivers FR-10 through FR-13.

| ID | Task | Effort | Status |
|---|---|---|---|
| M10-1 | Auth: JWT register/login/refresh/logout + `/me`, role claims | 4 h | `[ ]` |
| M10-2 | Projects CRUD + saved sites (**FR-10**) + row-level ownership checks | 4 h | `[ ]` |
| M10-3 | **FR-11** cadastral upload — sandboxed Fiona parse, zip-bomb + traversal defence | 5 h | `[x]` |
| M10-4 | ↳ datum transform on ingest (Kalianpur → WGS 84), avoids a silent 100–400 m shift | 1.5 h | `[x]` |
| M10-5 | **FR-13** monthly water balance: inflow − evaporation − seepage → reliability | 4 h | `[x]` |
| M10-5b | ↳ `pond.side_slope_h_per_v` exposed numerically — the design parameter shipped only as the display string `"1V : 1.5H"`, so a consumer had to parse prose to get it back | — | `[x]` |
| M10-6 | **FR-12** site comparison endpoint (2–5 designs side by side) | 2 h | `[x]` |
| M10-7 | Terrain extras: elevation profile, depressions, batch elevation | 3 h | `[ ]` |
| M10-8 | Hydrology extras: batch catchment, flow-path trace, snap endpoint | 2.5 h | `[ ]` |
| M10-9 | Rainfall extras: return periods (Gumbel), IDF, sources health, evaporation | 3 h | `[ ]` |
| M10-10 | Runoff/pond extras: standalone CN, peak discharge, optimise | 2.5 h | `[ ]` |
| M10-11 | Suitability extras: criteria catalogue, AHP weights endpoint, simulate | 2.5 h | `[ ]` |
| M10-12 | Export formats: KML, Shapefile, GeoTIFF, CSV | 2 h | `[ ]` |
| M10-13 | WebSocket `/ws/analysis/{job_id}` + job cancellation | 2 h | `[ ]` |
| M10-14 | System endpoints: provider health, cache stats/invalidate | 1.5 h | `[ ]` |

---

## M11 — Hardening · 22.5 h · Ring 2

| ID | Task | Effort | Status |
|---|---|---|---|
| M11-1 | Coverage to **≥ 70 %** on `services/` | 4 h | `[ ]` |
| M11-2 | VCR.py cassettes for every provider; **zero network in CI** | 2 h | `[ ]` |
| M11-3 | Schemathesis contract tests against the OpenAPI schema | 2 h | `[ ]` |
| M11-4 | Playwright E2E: search → analyse → select → export | 3 h | `[ ]` |
| M11-5 | Circuit breakers + token-bucket rate limiters per provider | 2.5 h | `[ ]` |
| M11-6 | Request coalescing (HLD §2.5) — one upstream call serves N identical requests | 2 h | `[ ]` |
| M11-7 | structlog JSON logs with `analysis_id` correlation + secret redaction | 1.5 h | `[ ]` |
| M11-8 | Security pass: `pip-audit`, upload fuzzing, SSRF allowlist, git secret scan | 2 h | `[ ]` |
| M11-9 | Accessibility pass: axe-core clean, keyboard traversal, contrast (HLD NFR-14) | 2 h | `[ ]` |
| M11-10 | `locust` perf run against NFR-2/3 targets | 1.5 h | `[ ]` |

---

# 6. Dependency Graph

```
              (no credential required anywhere — HLD §4.2 A1 is an open bucket)
                                  │
                                  ▼
      M0 (docker, db, ci) ──▶ M1-1 DEM fetch ─────────┐
                              │                         ├─▶ M1-2 preprocess (UTM + fill)
              MC-2..MC-7 contour KML ──▶ interpolate ───┘   ← ADR-7: interchangeable
                    (needs no network at all)
                                                    │
                ┌───────────────────────────────────┼──────────────────────┐
                ▼                                   ▼                      ▼
       M2-5 contours (FR-2)              M1-3 flow dir/acc         M2-4 slope/hillshade
       M2-3 COG + TiTiler                        │                         │
                │                                ▼                         │
                │                    M1-4 / M3 catchment (FR-4)            │
                │                                │                         │
                │                                ▼                         ▼
       M1-5 rainfall ─▶ M4 stats (FR-5)   M4-8 zonal stats ◀── M4-6/7 soil + LULC
                │                                │                         │
                └────────────┬───────────────────┘                         │
                             ▼                                             ▼
                    M4-9/10 composite CN → SCS-CN runoff (FR-6)   M5-2/3 exclusion mask
                             │                                             │
                             ▼                                             ▼
                    M5-7/9 stage–storage + depth optimiser (FR-7)   M5-4/5 parcels (FR-3)
                             │                                             │
                             └──────────────────┬──────────────────────────┘
                                                ▼
                                     M6-6..11 AHP suitability (FR-9)
                                                ▼
                                     M6-1..5 async orchestration (FR-8)
                                                ▼
                                        M7 deliverables  ═══ RING 1 DONE ═══
                                                ▼
              ┌─────────────────────┬───────────┴──────────┬─────────────────┐
              ▼                     ▼                      ▼                 ▼
      M8 ISRO sources       M9 ML layer            M10 full API        M11 hardening
      (needs M0-1d/1e)      (needs M8-4 labels)    (independent)       (needs all)
```

**Critical path (Ring 1):** `M1-1 → M1-2 → M1-3 → M1-4 → M4-8 → M4-9 → M4-10 → M5-7 → M5-9 → M6 → M7`

**Ring 2 ordering note:** M9 depends on M8-4, because the MGNREGA geotagged assets *are* the ML
training labels. Doing M9 before M8 forces you back onto OSM labels with the bias problem HLD CH-26
describes. **Build M8 before M9.**

---

# 7. Test Gate Per Phase

A phase is not `[x]` until its gate passes. Writing the test *in* the phase costs ~30 min; writing it
months later costs an afternoon and finds bugs you have already built on.

| Phase | Gate | Kind |
|---|---|---|
| **M0** | `make up` → `/health` 200; CI green; `select count(*) from villages` > 0; `utm_epsg_for(77.4) == 32643` | smoke + unit |
| **M1** | Skeleton runs cold in < 3 min; **reproduces HLD §6.9**; runoff coefficient ∈ 0.15–0.45 | manual + sanity |
| **M2** | Contours non-empty, elevations monotonic across adjacent lines, no self-intersections; tile endpoint returns valid PNG | unit + smoke |
| **M3** | **Four synthetic surfaces give exact expected cell counts** (M3-7 box); upright-cone flank gives a small catchment; snapping strictly increases accumulation; real catchment ±15 % vs SLUSI | **golden + validation** |
| **M4** | Reproduces HLD §6.9 to 3 s.f.; `Q = 0` when `P ≤ Ia`; 75 % dependable < mean; composite CN within component range; HSG boundaries (clay→D, sand→A) | unit |
| **M5** | `V(d)` strictly increasing; prismoidal matches §6.9 Step 6 (7,647.6 m³); closure rejects `min(L,W) ≤ 2zd`; parcel areas sum ≤ AOI | unit + property |
| **M6** | Consistent AHP matrix → `CR < 0.1`; inconsistent matrix **rejected**; weights sum to 1.0; job reaches `DONE`; forced provider failure → `PARTIAL`, not `FAILED` | unit + integration |
| **M7** | Fresh clone → follow `INSTALL.md` **literally** → app runs; PDF opens with real maps and non-placeholder numbers | **clean-room** |
| **M8** | Every ISRO adapter with no token → `NotConfigured`, analysis still completes; COP30 vs CartoDEM delta reported | degradation |
| **M9** | **Spatial block CV** ROC-AUC ≥ 0.80; plain k-fold AUC recorded separately to show the leakage gap; α falls back to 1.0 on failure | ML validation |
| **M10** | Schemathesis green on all 72 endpoints; zip-bomb and `../` traversal both rejected | contract + security |
| **M11** | Full suite green with **no network**; `locust` meets NFR-2/3; axe-core clean | regression + perf |

**Property tests worth writing** (`hypothesis`): volume monotonic in depth · runoff monotonic in
rainfall · catchment area monotonic downstream · area invariant under CRS round-trip. These catch whole
classes of sign and unit errors that example-based tests miss.

---

# 8. Working Rhythm & Tracking

No deadline means no standups. What replaces them is a **session discipline**, because the real risk on
a long solo project is losing context between sessions, not running out of days.

**Start of each session (5 min)**
1. Run `python3 progress.py` — it prints phase progress, anything blocked or left in progress, and
   the next startable tasks. This replaces reading the plan to work out where you were.
2. Read the top row of the **Session Log (§0.3)** — your own note on where you stopped.
3. If you have been away more than a week, skim the Decision Log (§14) tail as well.

**End of each session (10 min) — non-negotiable**
1. Mark tasks `[x]` / `[~]` / `[!]` in the §5 phase tables.
2. Run `python3 progress.py --update` to refresh the §0.1 rollup.
3. **Add a Session Log row (§0.3)** — where you stopped and the single next action.
4. If a task diverged badly from its estimate, add an §0.4 row while you remember why.
5. If anything surprised you or you chose between options, add a Decision Log line (§14) **now**.
6. **Commit and push.** Never end a session with uncommitted work.
7. If stopping mid-task, also leave a `NEXT:` comment in the file you were editing.

**Keep at most two tasks `[~]` at once.** `progress.py` warns past that. Work-in-progress that spans
sessions is where context leaks on a project this long.

**Commit convention** — makes the git history itself readable evidence of process:
```
feat(hydrology): D8 flow accumulation + catchment delineation   [M3-1]
fix(crs): compute area in UTM, not degrees                      [M1-10]
test(golden): synthetic cone DEM catchment fixture              [M3-7]
docs(api): curl examples for the demo-critical endpoints        [M7-5]
```

**Branching:** solo, so work on `main` and tag milestones — `v0.1-skeleton` (M1), `v0.5-must-set`
(M6), `v1.0-ring1` (M7), `v2.0` (M11). Use a branch only for genuinely risky experiments.

---

# 9. Priority Order — What To Build Next When Unsure

Not a descope ladder any more; there is nothing to descope. This is **build order** — highest value
per hour first.

| Priority | Work | Why here |
|---|---|---|
| **0** | ★ **Phase MC** — the contour-map API | **Separately graded, with its own report.** It also forces M1's hydrology to exist, so it advances the critical path rather than diverting from it |
| 1 | Anything else on the Ring 1 critical path (§6) | Blocks everything downstream |
| 2 | M3-7 golden tests | Proves the 20-mark subsystem is actually correct |
| 3 | M1-10 hand-check against §6.9 | Every later number inherits these |
| 4 | M7-7 clean-room install verification | The deliverable nobody verifies is always broken |
| 5 | M5-11 which-constraint-binds | One hour; turns a calculator into a planning tool |
| 6 | M8 ISRO sources | Makes it genuinely an *India* system, per the design |
| 7 | M6-4 `PARTIAL` state | Difference between a demo and a tool |
| 8 | M9 ML layer with spatial block CV | The "AI" in the title, done defensibly |
| 9 | M10 remaining endpoints | Completeness against the HLD |
| 10 | M11 hardening | Best done once behaviour has stopped changing |

**Deliberately last:** UI visual polish. It is 5 marks, it is the most tempting thing to fiddle with,
and it is the easiest to do quickly at the end.

---

# 10. Risk Register

| # | Risk | Trigger to watch | Action |
|---|---|---|---|
| R1 | AWS open bucket unreachable or restructured | `/vsicurl` open fails on a tile that previously worked | Three keyless fallbacks behind the same interface (HLD §4.2 F): AWS Terrain Tiles → OpenTopoData → cached L2. **Pin the tile-naming scheme in a unit test** so a bucket layout change fails loudly instead of silently returning nothing |
| R2 | GDAL/rasterio install fails on the host | `import rasterio` errors | **Work only inside Docker.** Never `pip install gdal` on the host |
| R3 | pysheds output CRS / area wrong | M1-10 hand check disagrees with §6.9 | Assert `dem.crs.is_projected` in `CRSGuard`; verify a known polygon's area to ±0.5 % |
| R4 | Catchment absurdly small or large | M1/M3 | Snapping (M3-1) + `min_accumulation_cells` warning; confirm the pour point is on a drainage line |
| R5 | Runoff coefficient > 0.6 | M4 | SCS-CN applied to annual rainfall. Move to the daily series (M4-10) |
| R6 | Flat terrain → parallel-flow artefacts | M3, plains village | Breach instead of fill (M3-2); pick test villages with ≥ 20 m relief (M0-15) |
| R7 | ISRO approval never arrives | M8 start, still pending | M8-12 degradation path already covers it; Ring 1 substitutes remain valid; state it in the report |
| R8 | ML AUC looks suspiciously high | M9-7 | You are leaking through spatial autocorrelation. Use M9-6 block CV; record both numbers |
| R9 | Overpass timeout on a large AOI | M5-1 | Cap AOI, raise `[timeout:60]`, cache aggressively, degrade to "no infrastructure data" |
| R10 | Worker OOM on a large AOI | M4/M5 | float32 not float64; windowed `rasterio` reads; `MAX_AOI_KM2` cap; Celery memory limit |
| R11 | **Momentum loss between sessions** | Two weeks with no commit | The §8 end-of-session ritual exists for this. Finish a phase before pausing |
| R12 | **Scope creep with no deadline** | Ring 3 work before Ring 1 is done | Ring 1 first, always. §9 priority order is the tie-breaker |

> **R11 and R12 are the two real risks now.** With a deadline, the enemy is time. Without one, the
> enemies are drift and distraction — starting the interesting ML work before the mandatory pipeline
> is finished, or pausing mid-phase and losing the thread.

---

# 11. Deliverables Checklist

| Deliverable | Where | Phase | Status |
|---|---|---|---|
| **Complete source code** | repo `main`, tagged | M7 / M11 | `[x]` |
| **Installation guide** | `docs/INSTALL.md` + `README.md`, **clean-room verified** | M7-6/7 | `[x]` |
| **API documentation** | `/docs` (auto OpenAPI) + `docs/API.md` with curl examples | M7-5 | `[x]` |
| **Accessible front-end** | `frontend/`, served on `make up`; WCAG pass in M11-9 | M6 / M11 | `[ ]` |
| **Final technical report** | `docs/TECHNICAL_REPORT.md` | M7 → M11 | `[x]` |
| ★ **Phase report — contour API** | `docs/REPORT_CONTOUR_API.md` — repo link, **local route URL + exact run steps**, approach, demo on the sample, API docs | MC-24 | `[x]` |
| ★ **Public repo README** | `README.md` — what it does, quickstart, architecture, endpoint table | MC-21 | `[x]` |
| ★ **Reproducible demo** | `scripts/demo_contour.sh` + Postman/Bruno collection | MC-22 | `[x]` |

## 11.1 Technical Report Outline

Assembled from work already done, not written from scratch:

1. **Introduction & problem statement** → HLD §1
2. **System architecture** → HLD §2.2/2.3 diagrams + what changed during the build (Decision Log)
3. **Methodology** → HLD §6, anchored by the **worked example in §6.9** — the substance of the report
4. **Implementation** → repo structure, libraries, async design, provider fallback chains
5. **Results for a case-study village** → screenshots, headline numbers, all layers
6. **Validation** → golden tests, ±15 % catchment comparison, spatial block CV metrics
7. **Scope & limitations** → HLD §13, plus anything left in Ring 2/3. **Stating limits precisely reads as rigour**
8. **Future work** → whatever remains of Ring 3
9. **References** → HLD §14 (already includes the Indian standards)

---

# 12. Release Checklist

**Worked through after M7 (see the session log).** Ticked items were *verified*,
not asserted: the fresh-directory install was executed literally, `make test` was
run, the secrets scan was run, the sample PDF was generated from the running API,
and the report was cross-checked against §0.2 programmatically. The items left
open are the ones only you can do — tagging, pushing, and the screen recording.

Two defects surfaced from running the checklist rather than reading it:

* **`make test` was not hermetic.** The target's own summary says "no Docker, no
  network", but it passed directories without a marker filter, so
  `TestAgainstTheRealBucket` in `golden/` reached out to S3 and failed. Fixed by
  adding `-m "not network and not e2e"`.
* **A compose mount silently corrupted the repo.** Mounting the sample sheet to
  `/srv/contours_1m.kml` created a zero-byte `backend/contours_1m.kml` *on the
  host*, because `/srv` is itself a bind mount of `./backend` and Docker creates
  a missing file-mount target. That empty file then shadowed the real 6.7 MB
  sample for any code resolving it by walking up the tree — which is what the
  `make test` failure actually was. The mount now lands outside both bind mounts.

Run before calling any tag done — especially `v1.0-ring1`.

**Code & repo**
- `[x]` `main` builds from a **fresh clone** in one command
- `[ ]` Milestone tags present (`v0.1-skeleton`, `v0.5-must-set`, `v1.0-ring1`) — shows process
- `[x]` **No secrets committed** — `git log -p | grep -iE 'api[_-]?key|secret|password'` clean
- `[x]` `.env.example` complete; `.env` gitignored
- `[x]` `make lint` and `make test` both green
- `[x]` No dead code or stray `TODO` in `services/`

**Deliverables**
- `[x]` `INSTALL.md` verified clean-room (M7-7), not merely written
- `[x]` `API.md` covers every shipped endpoint; `/docs` loads
- `[x]` `TECHNICAL_REPORT.md` complete, **including limitations**
- `[x]` `HLD.md` + `IMPLEMENTATION_PLAN.md` in `docs/`
- `[x]` Frontend reachable on `make up` with no manual build step
- `[x]` One generated PDF committed as a sample artefact

**Demo readiness**
- `[x]` `DEMO_MODE=true` works with the **network unplugged**
- `[ ]` Cache warmed for all three test villages (M0-15)
- `[x]` Full flow rehearsed end to end
- `[~]` Screenshots / recording captured as a fallback
- `[ ]` Repo pushed to remote **and** a backup copy off-machine

**Honesty pass — do this last, deliberately**
- `[x]` Every number in the UI traces to a real computation (no leftovers from M1's hardcoded CN)
- `[x]` `[-]` items in §0.2 match what the report says was not built
- `[x]` No claim in the report that the code does not actually do

## 12.1 Demo Script (~6 minutes)

**Rehearsed against the running stack (M7-12); every step below was executed, not
assumed.** The earlier version of this script did not match the application: it
had village search leading into "Run Analysis", and toggling contours before any
analysis existed. The app is contour-map-first — the sheet is the input, and the
village index is a separate lookup — so the order here is the order that works.

| # | Action | Demonstrates |
|---|---|---|
| 1 | `make up`, open **http://localhost:8080** | One-command install |
| 2 | Type `kutelabhata` in *Find a village* → picks `Kutelabhatha, Durg` | **FR-1** + name folding across a misspelling |
| 3 | Note the outline is dashed and labelled a *sub-district* | Honesty: no keyless source has village polygons |
| 4 | Drop `contours_1m.kml` on the page, press **Analyse** | **FR-15** — the contour map is the input |
| 5 | Progress bar names each step; holds at 11 % on the provider fetch | **FR-8**, async design, weighted progress |
| 6 | Contours, catchment and ranked sites paint over imagery | **FR-2**, **FR-4** — *dwell here, 20 marks* |
| 7 | Click site #2 → its own catchment and pond redraw | **FR-4** interactivity |
| 8 | *Why it scores* → per-criterion contributions summing to the score | **FR-9**, explainability |
| 9 | Water balance: rainfall chart, runoff tiles, curve number | **FR-5**, **FR-6** |
| 10 | Stage–storage curve — note where it *stops* | **FR-7** + a real terrain limit |
| 11 | Site card: depth, capacity, **"Limited by practical excavation depth"** | **FR-7** + the planning insight |
| 12 | *Find buildable land* → parcels + "what was ruled out" | **FR-3** (on request, ~15 s cold) |
| 13 | `curl` the PDF report and the GeoJSON export | Deliverables |
| 14 | `GET /suitability/weights` → the AHP consistency audit | The "AI" is auditable, CR = 0.009 |
| 15 | Show **/docs** | API documentation deliverable |

Steps 13–14 from a terminal:

```bash
JOB=$(curl -sX POST localhost:8000/api/v1/analysis -F file=@contours_1m.kml | jq -r .job_id)
# ...poll .../status until is_terminal
curl -sX POST localhost:8000/api/v1/reports/generate -F job_id=$JOB | jq
curl -s "localhost:8000/api/v1/export/$JOB?format=geojson" -o analysis.geojson
curl -s localhost:8000/api/v1/suitability/weights | jq .audit.consistency
```

**Before demonstrating, run `make demo-warm`.** It fills the soil and land-cover
caches and then re-runs the analysis with `DEMO_MODE` forced to prove the whole
flow works without the network. SoilGrids returned HTTP 503 for every point
during one afternoon of this build; the cache is not an optimisation.

**Have ready for questions:** the golden tests (D8 and Strahler against analytic
surfaces), the contour→DEM→contour round trip (median residual 0.109 m), the
provider fallback chains, the AHP consistency ratio — and one candid sentence on
the biggest limitation: **land tenure is not modelled at all**, so a site that is
physically ideal may be privately held, and that has to be checked on the ground.

---

# 13. Environment & Keys

`.env.example` — commit this; never commit `.env`.

```
# --- Ring 1 needs NO credentials at all ---
# The DEM comes from the Copernicus GLO-30 AWS Open Data bucket: no key, no quota.

# --- Optional enrichments (absent => that layer degrades, pipeline still runs) ---
DATA_GOV_IN_API_KEY=               # official IMD district rainfall (M4)
OPENTOPOGRAPHY_API_KEY=            # optional: SRTM/NASADEM/AW3D30 variety only

# --- Needed for Ring 2 (apply in M0, use in M8/M9) ---
BHUVAN_TOKEN=                      # expires daily; BhuvanTokenService refreshes it
BHOONIDHI_API_KEY=                 # email bhoonidhi@nrsc.gov.in
COPERNICUS_CLIENT_ID=              # Sentinel-2 NDWI for ML labels (M9-2)
COPERNICUS_CLIENT_SECRET=

# --- Infrastructure ---
POSTGRES_HOST=postgis
POSTGRES_DB=contour
POSTGRES_USER=contour
POSTGRES_PASSWORD=
REDIS_URL=redis://redis:6379/0
COG_STORE_PATH=/data/cache
TITILER_ENDPOINT=http://titiler:8000

# --- Behaviour ---
DEMO_MODE=false                    # true → warmed fixtures, no network
MAX_AOI_KM2=100
DEFAULT_DEM_SOURCE=COP30
AOI_BUFFER_M=500
SUITABILITY_ALPHA=0.6              # 1.0 = pure AHP, no ML
```

---

# 14. Decision Log

Append whenever a design decision changes. This becomes §7 of the technical report, and on a long
project it is the cheapest documentation you will ever write.

| # | Decision | Reason |
|---|---|---|
| 1 | Walking skeleton before any UI | Front-load integration risk; sustain momentum (§2) |
| 2 | Ring 1 / Ring 2 split, Ring 1 = 8 mandatory FRs | Something submittable at ~207 h; full design still the goal |
| 3 | ISRO sources restored to Tier-1 (M8) | No deadline → approval lead time no longer matters |
| 4 | ML layer restored to scope (M9), with spatial block CV mandatory | Plain k-fold leaks through spatial autocorrelation |
| 5 | Build M8 before M9 | MGNREGA geotagged assets are the ML training labels |
| 6 | Report *which constraint binds* on pond sizing (M5-11) | HLD §6.9 showed the parcel binds, not water — that is the useful output |
| 7 | **Base image `python:3.12-slim` + geo wheels, not `ghcr.io/osgeo/gdal`** | rasterio/pyproj/shapely ship manylinux wheels with GDAL, PROJ and GEOS bundled, so there is no system-GDAL version to match — removing the most common build failure in projects like this. Contours therefore take the rasterio/OpenCV path (HLD §6.8 "Path 2") rather than the `gdal_contour` CLI, which the assignment's OpenCV suggestion already favours. Verified: the full requirements set resolves and pysheds 0.4 builds against numpy 2.1.3 |
| 8 | **All host port mappings are env-configurable** (`API_HOST_PORT`, `REDIS_HOST_PORT`, …) | A local Redis on 6379 blocked the first `compose up`. Editing compose to dodge a port clash is a trap; parameterising it is a one-line fix every future developer benefits from |
| 9 | `FEATURE_REQUIREMENTS` is a `ClassVar`, not a Settings field | As a pydantic field it was silently overridable from the environment, which is meaningless for a constant of the code |
| 10 | Test config points DB/Redis probes at **port 1** (closed), not `localhost:5432` | Otherwise the readiness test passes or fails depending on whether the developer happens to be running Postgres — a flaky test disguised as a working one |
| 11 | Frontend container ships in M0 with **nginx only** | Makes the HLD §9 deployment topology real and testable from phase one; the Vite build stage arrives in M2-7 |
| 13 | ★ **A contour map is an `ElevationSource`, not a second pipeline** (ADR-7, phase MC) | The contour-API assignment could have been a parallel stack. Instead the KML parser + TIN interpolation produce the same metric DEM raster the remote source produces, so sink filling, D8, flow accumulation, catchment delineation and pond siting are reused **unchanged**. This is the answer to the "code extensibility to future phases" criterion, and it is structural rather than asserted |
| 14 | Elevation is resolved by a **strategy chain**, and the winning strategy is reported | The sample stores elevation in `<Placemark><name>` with 2D coordinates, but other exports use the z-ordinate, `ExtendedData/SimpleData`, or only the folder name. Hard-coding the sample's convention would fail the "generalise to other contour maps" requirement outright; MC-14 tests all four against identical terrain |
| 17 | **Local-only: no deployment, ever** | The user's explicit decision. The "working API route URL" deliverable is satisfied by a localhost URL plus reproducible one-command run instructions, and MC-20 verifies a clean clone brings the whole stack up — which proves more than a live server would |
| 16 | ★ **A contour upload is enriched from its own location, so MC returns the full analysis** | A contour map has no rainfall/soil/LULC attribute, but it has a position — and Open-Meteo, SoilGrids, WorldCover and Overpass are all keyless from a coordinate. Treating the upload as a closed world would have shipped a catchment area where the brief asks for "catchment information required for pond planning". Degradation is an explicit 3-tier ladder (HLD §6.10.5), not the default |
| 15 | Grid resolution **derived from median contour-vertex spacing**, not fixed | Interpolating far finer than the survey's own spacing invents detail the data does not contain; far coarser discards it. Deriving it keeps the sample's numbers out of the code, and the value used is returned in the response |
| 12 | ★ **DEM primary = Copernicus GLO-30 from the AWS Open Data bucket; OpenTopography demoted to optional** | The OpenTopography key proved unobtainable. The same COP30 data is public in an S3 bucket as range-request COGs — no key, no registration, no quota — which is strictly *better* than what it replaced: windowed reads make a 5×5 km AOI cost 101 KB instead of a whole-tile download. Verified against two independent point APIs (494.6 m vs 495.0 m at Bhopal). **Consequence: Ring 1 requires no credentials at all**, the project's largest single dependency risk is gone, and CH-6 (rate limits) no longer touches the core pipeline |
| | | |

---

# 15. Useful Commands

```bash
# tracking — run at the start and end of every session
python3 progress.py              # report + next startable tasks
python3 progress.py --update     # also rewrite the §0.1 rollup from the checkboxes
python3 progress.py --next 10    # show more of the queue

make up                 # docker compose up -d --build
make logs               # tail all services
make seed               # load SHRUG villages
make test               # pytest with coverage
make lint               # ruff + black --check + mypy
make demo               # DEMO_MODE=true + warm the cache

# quick checks
curl -s localhost:8000/api/v1/health | jq
curl -s "localhost:8000/api/v1/villages/search?q=rampur" | jq '.[0:3]'
docker compose exec api python -m scripts.skeleton      # M1 walking skeleton
docker compose exec postgis psql -U contour -c 'select count(*) from villages;'
pytest backend/app/tests/golden -v                      # D8 correctness proof
pytest backend/app/tests/property -v                    # invariants
```
