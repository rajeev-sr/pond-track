# High-Level Design (HLD)
## AI-based Village Pond Planning System
### "Contour" — Geospatial Decision Support for Rainwater Harvesting Structures

| | |
|---|---|
| **Course** | Computer System Design (CSD) — Assignment 1 |
| **Document** | High-Level Design (Design document, not implementation report) |
| **Version** | 1.0 |
| **HLD Submission** | 10 August |
| **Final Submission** | 5 September |

---

# 1. Problem Statement and Objectives

## 1.1 Problem Statement

Rural India loses the majority of its monsoon rainfall as surface runoff because there are too few
structures to intercept and store it. Village-level ponds (tanks, *talab*, farm ponds, percolation
tanks) are the cheapest and most effective way to harvest this runoff, recharge groundwater, and
provide water for irrigation and livestock in the dry season.

However, **selecting *where* to build a pond is currently a manual, experience-driven decision.**
A village administrator, Gram Panchayat secretary or a Junior Engineer must simultaneously reason about:

1. **Terrain** — is the point a natural low-lying depression that water actually flows into?
2. **Catchment** — how much upstream area drains to this exact point?
3. **Rainfall** — how much rain does this catchment historically receive, and how reliable is it?
4. **Runoff** — what fraction of that rain actually becomes surface runoff (depends on soil + land cover)?
5. **Land availability** — is government/common land (gairan, gochar, shamlat, wasteland) free at that spot?
6. **Geometry & cost** — how deep and how wide should the pond be so it fills but does not over-excavate?

Doing this by hand requires survey instruments, topo-sheets, revenue records and hydrology expertise
that is not available at the village level. The result is ponds built at hydrologically wrong
locations that never fill, or ponds under-sized so they overflow and waste the runoff.

> **Core problem:** There is no accessible, low-cost tool that fuses freely-available satellite,
> elevation, land-cover, soil and rainfall data into a single, explainable recommendation of
> *where* to build a village pond and *how big* it should be.

## 1.2 Objectives

**Primary objective**
Design and develop a complete, deployable web application that ingests open geospatial and climate
data for any selected Indian village and produces a **ranked, explainable set of pond sites** with
quantitative estimates of catchment area, runoff volume, recommended depth and storage capacity —
presented on an interactive map.

**Specific objectives (SO)**

| ID | Objective | Maps to FR |
|---|---|---|
| SO-1 | Render high-resolution satellite imagery for a selected village with its administrative boundary | FR-1 |
| SO-2 | Acquire a Digital Elevation Model (DEM), derive and visualise contour lines, slope and hillshade | FR-2 |
| SO-3 | Identify parcels of land physically and legally suitable for excavation (exclusion masking) | FR-3 |
| SO-4 | Delineate the hydrological catchment (watershed) contributing runoff to any user-selected point | FR-4 |
| SO-5 | Retrieve ≥30 years of historical daily rainfall from public APIs and compute design statistics | FR-5 |
| SO-6 | Estimate annual and monthly runoff volume using the SCS-Curve Number method | FR-6 |
| SO-7 | Recommend optimal pond depth, plan dimensions and storage capacity from the DEM stage–storage curve | FR-7 |
| SO-8 | Overlay every result as toggleable map layers + charts, and export a PDF/GeoJSON report | FR-8 |
| SO-9 | Rank candidate sites using a hybrid **AHP-MCDA + Machine Learning** suitability engine (the "AI" component) | FR-3, FR-7 |
| SO-10 | Expose the whole capability as a documented, versioned REST API so it can be reused by other systems | Deliverable |

## 1.3 Scope

**In scope:** Any village in India; open/free data sources only; catchments up to ~50 km²;
single-pond and multi-pond (top-N) recommendation; browser-based UI; Dockerised deployment.

**Out of scope (v1):** Detailed structural design of bund/spillway drawings; legal land-title
verification; real-time flood forecasting; groundwater flow modelling; procurement/tendering.

## 1.4 Stakeholders

| Stakeholder | Need |
|---|---|
| Gram Panchayat / Village Administrator | Where to build, how much it will hold, a printable justification |
| Block Development Officer / Junior Engineer | Technically defensible numbers, cost estimate, export to GIS |
| MGNREGA / Watershed Programme Planner | Compare multiple candidate sites, prioritise by benefit-cost |
| NGO / Researcher | Reproducible methodology, raw data export |

---

# 2. Overall System Architecture

## 2.1 Architectural Style

A **layered, service-oriented monolith with an asynchronous worker tier** — deliberately chosen over
microservices because:

- All modules share the same heavy geospatial dependency stack (GDAL/PROJ/rasterio); splitting them
  would duplicate ~2 GB of libraries per service.
- **The bottleneck is external-API latency, not CPU.** A 25 km² village AOI at 30 m is a **167 × 167
  raster — 28 000 cells**; all six terrain derivatives together occupy well under **1 MB**, and
  numba-accelerated flow accumulation over it takes milliseconds. A cold analysis takes 60–90 s
  because it *waits on the network*: DEM download, 30 years of rainfall, LULC, soil, OSM. The fix for
  that is a **job queue plus concurrent fetching**, not service decomposition.
- A single team, a 4-week timeline, and a marks weightage on *code quality* favour clean internal
  module boundaries over network boundaries.

Internally, every domain is a separate, independently testable Python package with an explicit
interface, so extraction into a microservice later is a refactor, not a rewrite.

**Key architectural decisions (ADRs)**

| ID | Decision | Rationale |
|---|---|---|
| ADR-1 | Modular monolith + Celery workers | Shared 2 GB geo dependency stack; the work is **IO-bound aggregation**, which a queue solves without splitting services |
| ADR-2 | All long analyses are async jobs (`202 Accepted` + poll/WebSocket) | A cold analysis spends 60–90 s **waiting on external providers**; must not block HTTP, and needs progress reporting |
| ADR-3 | Rasters are **never** returned as JSON — served as COG via XYZ tiles | A 30 m DEM of one village is ~4 MB binary, ~40 MB as JSON |
| ADR-4 | Every external data source sits behind an **Adapter interface** with a fallback chain | Free APIs are rate-limited and go down; must be swappable |
| ADR-5 | Store geometry in EPSG:4326, compute all metrics in local UTM | Area/length in degrees is meaningless |
| ADR-6 | Suitability = transparent AHP score **first**, ML model as an optional overlay | Administrators must be able to justify public spending |
| **ADR-7** | ★ **Elevation is an abstract source, not a fixed dataset.** A remote DEM and a user-uploaded contour map are interchangeable implementations of one `ElevationSource` protocol, both yielding a metric DEM raster | The system must analyse *either* a village fetched from an open bucket *or* a contour map supplied as KML/KMZ, without duplicating the hydrology. Everything downstream of `ElevationSource` — sink filling, D8, flow accumulation, catchment delineation, pond siting — is written once and is input-agnostic. This is the project's principal extensibility seam (§6.10) |

## 2.2 Block Diagram — Layered Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                    LAYER 1 : PRESENTATION  (Web Browser)                     ║
║                     React 18 + TypeScript + Vite (SPA / PWA)                 ║
║  ┌───────────┐ ┌────────────┐ ┌───────────┐ ┌───────────┐ ┌──────────────┐  ║
║  │  Village  │ │ Interactive│ │   Layer   │ │  Results  │ │   Report /   │  ║
║  │  Search   │ │    Map     │ │  Control  │ │  Panel +  │ │    Export    │  ║
║  │ & Selector│ │ (MapLibre) │ │  & Legend │ │  Charts   │ │  (PDF/GeoJSON)│ ║
║  └───────────┘ └────────────┘ └───────────┘ └───────────┘ └──────────────┘  ║
╚═══════════════════════════════════╤══════════════════════════════════════════╝
              HTTPS │ REST(JSON) │ WebSocket(progress) │ XYZ raster/vector tiles
╔═══════════════════════════════════▼══════════════════════════════════════════╗
║              LAYER 2 : API GATEWAY   (Nginx  →  FastAPI / Uvicorn)           ║
║   Routing │ JWT Auth │ Pydantic Validation │ Rate-Limit │ CORS │ Error Map   ║
║   Auto-generated OpenAPI 3.1 / Swagger UI  →  satisfies "API documentation"  ║
╚═══════════════════════════════════╤══════════════════════════════════════════╝
╔═══════════════════════════════════▼══════════════════════════════════════════╗
║                     LAYER 3 : SERVICE / DOMAIN LAYER                         ║
║ ┌──────────┐┌──────────┐┌───────────┐┌──────────┐┌──────────┐┌────────────┐ ║
║ │ Village  ││ Terrain  ││ Hydrology ││ Rainfall ││   Land   ││   Runoff   │ ║
║ │ Service  ││ Service  ││  Service  ││ Service  ││ Service  ││  Service   │ ║
║ │ geocode  ││ DEM,slope││ D8 flow,  ││ 30-yr    ││ LULC,soil││  SCS-CN,   │ ║
║ │ boundary ││ contour  ││ catchment ││ stats    ││ exclusion││  Rational  │ ║
║ └──────────┘└──────────┘└───────────┘└──────────┘└──────────┘└────────────┘ ║
║ ┌────────────────────────┐┌────────────────────┐┌──────────────────────────┐ ║
║ │  Suitability Service   ││  Pond Design       ││   Report Service         │ ║
║ │  ★ AI CORE:            ││  Service           ││   PDF, GeoJSON, KML,     │ ║
║ │  AHP-MCDA + RandomForest││ stage-storage,    ││   Shapefile, CSV         │ ║
║ │  + DBSCAN clustering   ││  depth optimiser   ││                          │ ║
║ └────────────────────────┘└────────────────────┘└──────────────────────────┘ ║
║ ┌──────────────────────────────────────────────────────────────────────────┐ ║
║ │        ANALYSIS ORCHESTRATOR  (DAG: sequences the 13-step pipeline)       │ ║
║ └──────────────────────────────────────────────────────────────────────────┘ ║
╚═══════════╤══════════════════════════════════════════╤═══════════════════════╝
╔═══════════▼═════════════════════╗   ╔════════════════▼═══════════════════════╗
║  LAYER 4 : ASYNC JOB LAYER      ║   ║ LAYER 5 : EXTERNAL INTEGRATION LAYER   ║
║  Celery Workers + Redis Broker  ║   ║ Adapter │ Cache │ Retry+Backoff │      ║
║  ┌───────────────────────────┐  ║   ║ Circuit-Breaker │ Rate-Limiter        ║
║  │ T1 fetch_dem              │  ║   ║ ┌────────────┐ ┌────────────────────┐ ║
║  │ T2 preprocess_fill_sinks  │  ║   ║ │ ELEVATION  │ │     RAINFALL       │ ║
║  │ T3 flow_dir + flow_acc    │  ║   ║ │ OpenTopo-  │ │ Open-Meteo Archive │ ║
║  │ T4 delineate_catchment    │  ║   ║ │ graphy,    │ │ NASA POWER         │ ║
║  │ T5 build_availability_mask│  ║   ║ │ OpenTopo-  │ │ IMD / data.gov.in  │ ║
║  │ T6 suitability_model      │  ║   ║ │ Data, AWS  │ │ CHIRPS             │ ║
║  │ T7 runoff_scs_cn          │  ║   ║ │ Terrain    │ └────────────────────┘ ║
║  │ T8 pond_optimise          │  ║   ║ └────────────┘ ┌────────────────────┐ ║
║  │ T9 render_report_pdf      │  ║   ║ ┌────────────┐ │    LULC / SOIL     │ ║
║  └───────────────────────────┘  ║   ║ │  IMAGERY   │ │ ESA WorldCover 10m │ ║
║  Flower dashboard (monitoring)  ║   ║ │ Sentinel-2 │ │ SoilGrids (ISRIC)  │ ║
╚═══════════╤═════════════════════╝   ║ │ Esri World │ │ Bhuvan LULC (ISRO) │ ║
            │                         ║ │ Bhuvan WMS │ └────────────────────┘ ║
            │                         ║ └────────────┘ ┌────────────────────┐ ║
            │                         ║ ┌────────────┐ │  VECTOR / ADMIN    │ ║
            │                         ║ │ OSM Over-  │ │ Nominatim geocode  │ ║
            │                         ║ │ pass API   │ │ LGD / DataMeet     │ ║
            │                         ║ └────────────┘ └────────────────────┘ ║
            │                         ╚════════════════════════════════════════╝
╔═══════════▼══════════════════════════════════════════════════════════════════╗
║                    LAYER 6 : DATA / PERSISTENCE LAYER                        ║
║ ┌────────────────┐ ┌───────────────┐ ┌─────────────┐ ┌─────────────────────┐ ║
║ │ PostgreSQL 16  │ │ Object Store  │ │   Redis 7   │ │      TiTiler        │ ║
║ │  + PostGIS 3.4 │ │ (MinIO/volume)│ │ Cache +     │ │  COG raster tile    │ ║
║ │ villages,jobs, │ │ DEM, slope,   │ │ Broker +    │ │  server (XYZ/WMTS)  │ ║
║ │ catchments,    │ │ suitability   │ │ Result      │ │  + dynamic colormap │ ║
║ │ sites, designs │ │ .tif (COG)    │ │ backend     │ │                     │ ║
║ └────────────────┘ └───────────────┘ └─────────────┘ └─────────────────────┘ ║
╚══════════════════════════════════════════════════════════════════════════════╝

   CROSS-CUTTING:  Structured Logging │ Config (12-factor) │ Metrics │
                   Multi-level Cache │ Exception Middleware │ CI/CD
```

## 2.3 Simplified Block Diagram (for the handwritten copy)

```
        ┌──────────────────────────────────────────────┐
        │   FRONTEND (React + MapLibre)                │
        │   Search • Map • Layers • Charts • Report    │
        └───────────────────┬──────────────────────────┘
                            │ REST + WebSocket
        ┌───────────────────▼──────────────────────────┐
        │   API GATEWAY  (FastAPI)                     │
        │   Auth • Validation • Routing • Swagger      │
        └───────────────────┬──────────────────────────┘
                            │
   ┌────────────────────────▼─────────────────────────────┐
   │             SERVICE LAYER (8 domain services)        │
   │  Village │ Terrain │ Hydrology │ Rainfall            │
   │  Land    │ Runoff  │ Suitability(AI) │ PondDesign    │
   └───┬──────────────────────────────────────────┬───────┘
       │                                          │
┌──────▼───────────┐                    ┌─────────▼──────────────┐
│  CELERY WORKERS  │                    │  EXTERNAL API ADAPTERS │
│  (heavy raster   │                    │  Elevation │ Rainfall  │
│   computation)   │                    │  LULC/Soil │ Imagery   │
└──────┬───────────┘                    │  OSM       │ Geocoding │
       │                                └────────────────────────┘
┌──────▼────────────────────────────────────────────────────────┐
│  DATA LAYER : PostGIS  │  COG Raster Store  │  Redis Cache    │
└───────────────────────────────────────────────────────────────┘
```

## 2.4 Component Responsibility Matrix

| Component | Responsibility | Key Inputs | Key Outputs |
|---|---|---|---|
| **Village Service** | Fuzzy village-name search over the locally-seeded SHRUG/LGD tables, resolve the **LGD code as canonical key**, serve the Census-2011 boundary | village name (Latin or Devanagari), state/district | boundary GeoJSON, bbox, centroid, LGD + Census IDs |
| **Terrain Service** | DEM acquisition, mosaicking, reprojection, sink filling, contours, slope/aspect/TWI/TPI/hillshade | AOI bbox | DEM COG, contour GeoJSON, derivative rasters |
| **Hydrology Service** | Flow direction (D8/D∞), flow accumulation, stream network, pour-point snapping, watershed delineation | filled DEM, pour point | catchment polygon, area, flow-path length, streams |
| **Rainfall Service** | Ensemble 30-yr daily rainfall with **IMD gridded as the authoritative anchor** + ERA5-Land/CHIRPS for spatial detail; region-aware monsoon window (SW vs NE); normals, dependability, return periods | lat/lon, date range, state | annual/monthly series, statistics bundle, uncertainty band |
| **Land Service** | **Bhuvan wasteland/LULC** + ESA WorldCover + SoilGrids retrieval, OSM infrastructure fetch, state-parameterised land-category mapping, exclusion masking, morphological cleaning, parcel extraction | AOI, constraints, state | available-land parcels GeoJSON, HSG map, category tags |
| **Suitability Service ★** | AHP weight derivation, criterion normalisation, weighted overlay, RF probability, DBSCAN clustering, ranking, explainability | all derived layers | suitability raster + ranked site list + per-criterion breakdown |
| **Runoff Service** | Composite Curve Number, AMC adjustment, SCS-CN runoff, Rational peak flow, Strange cross-check | catchment, rainfall, LULC×HSG | annual/monthly runoff m³, runoff coefficient, peak Q |
| **Pond Design Service** | Stage–storage–area curve from DEM, depth optimisation, prismoidal geometry, water balance, cost | site, DEM, runoff | depth, dimensions, capacity, reliability, ₹ estimate |
| **Report Service** | Compose PDF with maps/charts/methodology; export GeoJSON/KML/SHP/CSV | analysis result | report file, download URL |
| **Analysis Orchestrator** | Chain the pipeline as a DAG, emit progress, handle partial failure & retries | request | job status + composite result |

## 2.5 Caching Design

Caching is not an optimisation here — it is what makes the system *usable at all* under free-tier
API quotas (CH-6). Three levels, each with a different lifetime, chosen from how often the underlying
data actually changes.

| Level | Store | Contents | TTL | Rationale |
|---|---|---|---|---|
| **L0 — in-process** | Python `lru_cache` | CN lookup tables, HSG mappings, AHP weight vectors, UTM zone lookups | process life | Pure functions of static data |
| **L1 — hot** | Redis | External API responses, job progress, suitability weights, tile metadata | **1 h** (geocode 24 h) | Absorbs repeat requests inside one session |
| **L2 — durable** | PostGIS + COG files on disk | **DEM rasters, derivatives, rainfall series, soil, LULC, catchments** | **∞ (never expires)** | A 2015 DEM tile and 1995's rainfall are immutable. Re-fetching them is pure waste |

**Cache key** = `sha256(provider ‖ operation ‖ bbox_rounded_to_4dp ‖ sorted_params ‖ dataset_version)`

Rounding the bbox to 4 decimal places (~11 m) is deliberate: two users clicking 3 m apart in the same
village must hit the same cached DEM rather than triggering two downloads.

```
Request flow, with the important case marked:

  request ─▶ L0 hit? ─yes─▶ return
               │no
               ▼
            L1 (Redis) hit? ─yes─▶ return
               │no
               ▼
            L2 (PostGIS/disk) hit? ─yes─▶ warm L1 ─▶ return   ← the common case for DEM & rainfall
               │no
               ▼
            in-flight for this key? ─yes─▶ AWAIT the existing future   ← request coalescing
               │no
               ▼
            token-bucket rate limiter for this provider
               │
               ▼
            provider fallback chain (§4.2 F)  ─▶ write L2 ─▶ write L1 ─▶ return
```

**Request coalescing** matters more than it looks. Without it, ten simultaneous clicks on one village
fire ten identical DEM downloads and instantly trip a 500/day quota. With it, one download serves all
ten. Implemented as a Redis `SETNX` lock plus an in-process future map per worker.

**Cache warming** (`make demo`) pre-populates L2 for the demo villages so a presentation never depends
on a live network (CH-23, CH-29).

**Invalidation** is deliberately minimal: L2 entries are content-addressed and immutable, so there is
nothing to invalidate. A new dataset version produces a new key rather than overwriting an old value —
which is also what makes analyses reproducible (NFR-13).

## 2.6 Security Design

| Concern | Threat | Control |
|---|---|---|
| **Authentication** | Unauthorised writes | JWT bearer, short-lived access + refresh; public read endpoints allow anonymous so the tool stays usable |
| **Authorisation** | One Panchayat editing another's project | Role claim (`viewer` / `planner` / `admin`) + row-level ownership check on every project mutation |
| **Input validation** | Malformed geometry, absurd bbox, injection | Pydantic v2 strict models on every request; `MAX_AOI_KM2` cap; geometry validated with `shapely.make_valid`; **no string interpolation into SQL** — SQLAlchemy parameter binding only |
| **File upload** (FR-11) | Zip bomb, path traversal, malicious geometry | 50 MB cap; MIME sniffing; extract to a temp dir with sanitised names, rejecting `../`; parse with **Fiona in a subprocess with a timeout**; ≤ 10 000 features; **never** invoke a shell |
| **SSRF** | `custom_dem_url` pointed at internal services | Allowlist of provider hosts; reject private/link-local IP ranges after DNS resolution; no redirects followed to a new host |
| **Secret handling** | API keys leaked into git or logs | 12-factor env only; `.env` gitignored; `.env.example` documents keys with empty values; **log redaction filter** on `api_key`, `token`, `secret`, `password` |
| **Rate limiting (ours)** | One client exhausting our upstream quotas | Per-IP token bucket at the gateway; per-provider bucket internally; `429` with `Retry-After` |
| **Denial of service** | A 10 000 km² AOI OOM-ing every worker | AOI cap; Celery per-task memory + time limits; queue depth cap returning `503` rather than accepting unbounded work |
| **Transport** | Interception | TLS terminated at nginx; HSTS; secure cookie flags |
| **Dependency risk** | Vulnerable geo dependency | `requirements.lock` pinned; `pip-audit` in CI |
| **Data sensitivity** | Cadastral uploads may contain owner names | Treated as confidential: not logged, not included in exports by default, deletable via the project delete endpoint |

**Explicitly out of scope for v1:** multi-tenancy isolation beyond row-level checks, audit-log
immutability, and encryption at rest — all noted as future work rather than silently omitted.

---

# 3. Functional Requirements and Project Workflow

## 3.1 Functional Requirements (traceable)

| FR | Requirement | Acceptance Criterion | Priority |
|---|---|---|---|
| **FR-1** | Display satellite imagery for a selected village | User types a village name → map centres on it, shows ≤1 m/px imagery + admin boundary outline | Must |
| **FR-2** | Visualise contour maps | Contours at user-chosen interval (0.5/1/2/5 m) render as labelled lines + hillshade + slope layer | Must |
| **FR-3** | Identify land suitable for pond excavation | Returns polygons of parcels ≥ min-area, slope ≤ threshold, excluding buildings/roads/water/forest, tagged by land class | Must |
| **FR-4** | Estimate the catchment area contributing runoff to a selected point | Click anywhere → snapped pour point + watershed polygon + area (ha/km²) in ≤ 20 s | Must |
| **FR-5** | Query historical rainfall via public APIs | ≥30 years of daily rainfall; annual mean, CV, 50%/75% dependable, monthly normals, return periods | Must |
| **FR-6** | Estimate runoff volume from rainfall + catchment | SCS-CN with composite CN from LULC×HSG; annual + monthly runoff in m³; runoff coefficient | Must |
| **FR-7** | Recommend pond depth and storage capacity | Depth (m), top/bottom dimensions, side slope, gross & live capacity (m³), excavation volume, cost | Must |
| **FR-8** | Overlay all results | Toggleable layers: pond site, catchment, contours, slope, suitability, streams, available land + stat cards & charts | Must |
| FR-9 | Rank multiple candidate sites automatically | Top-N sites with normalised score 0–100 and per-criterion contribution | Should |
| FR-10 | Save/load projects and export report | Persist analysis; regenerate identical result; download PDF + GeoJSON | Should |
| FR-11 | Upload custom land-ownership / cadastral layer | Accept GeoJSON/Shapefile(.zip); use as ownership criterion | Should |
| FR-12 | Compare two candidate sites side-by-side | Tabular delta of area, runoff, capacity, cost | Could |
| FR-13 | Water-balance simulation (evaporation + seepage) | Month-wise storage curve, dry-out month, reliability % | Could |
| FR-14 | Natural-language explanation of the recommendation | 1-paragraph plain-language justification per site | Could |
| **FR-15** | **Accept a contour map (KML/KMZ) as terrain input** | Upload parses to contour geometries with elevations; handles elevation in the `<name>`, in the coordinate *z*, or in `ExtendedData`; rejects malformed input with a specific reason | **Must** |
| **FR-16** | **Analyse an uploaded contour map end-to-end to catchment information** | One request returns interpolated-terrain summary, a derived pond location, its catchment polygon and area, and the runoff inputs — with **no value specific to any particular map** | **Must** |

## 3.2 Non-Functional Requirements

| NFR | Requirement | Target |
|---|---|---|
| NFR-1 Performance | Map interaction | < 100 ms pan/zoom; tiles < 300 ms |
| NFR-2 Performance | Full village analysis (≈25 km² AOI) | < 90 s cold, **< 5 s warm**. Compute is < 2 s of that — the rest is provider latency, so the target is met by **concurrent fetching + the L2 cache**, not by faster maths |
| NFR-3 Performance | Catchment delineation on cached DEM | < 20 s |
| NFR-4 Scalability | Concurrent analyses | ≥ 10 (horizontal Celery worker scaling) |
| NFR-5 Reliability | Graceful degradation on provider outage | Fallback chain; partial result with `data_quality` flags |
| NFR-6 Accuracy | Catchment area vs reference watershed | within ±15 % |
| NFR-7 Accuracy | Reported uncertainty | Every derived number carries a ± range and source provenance |
| NFR-8 Usability | Learnability | Non-GIS user completes an analysis in < 3 min without training |
| NFR-9 Security | Auth, input validation, upload sandboxing | JWT, Pydantic strict, 50 MB cap, MIME + schema validation |
| NFR-10 Maintainability | Test coverage / typing / lint | ≥ 70 % coverage, mypy strict on services, ruff clean |
| NFR-11 Portability | One-command bring-up | `docker compose up` on any Linux/macOS/WSL host |
| NFR-12 Observability | Traceability | Structured JSON logs with `analysis_id` correlation, Prometheus metrics |
| NFR-13 Reproducibility | Re-running a saved analysis | Byte-identical numeric result (params + source versions stored) |
| **NFR-14 Accessibility** | The assignment requires an *"accessible front-end for users"* — met in both senses | **WCAG 2.1 AA**: keyboard-operable map controls and layer toggles, visible focus states, ARIA labels on all controls, ≥ 4.5:1 text contrast, **no information conveyed by colour alone** (suitability classes carry a pattern and a numeric label as well as a hue), `prefers-reduced-motion` respected, screen-reader-readable text alternative for every result the map shows |
| **NFR-15 Multilingual** | Users are Gram Panchayat staff, not English-first GIS analysts | UI strings externalised to `i18n/en.json` from day one so **Hindi and one regional language** can be added without touching components; numerals and units localised; village names accepted and displayed in Devanagari (CH-24) |
| **NFR-16 Low-bandwidth** | Rural connectivity is intermittent and metered | Vector tiles over raster where possible; contour generalisation by zoom; gzip/brotli; PWA service-worker caching of the last analysis so a saved result opens offline |

## 3.3 Project Workflow — Data-Flow Diagram

```
 ┌───────────────┐
 │ Village name  │
 │  or map click │
 └───────┬───────┘
         ▼
 ┌───────────────────────┐        ┌──────────────────────────────┐
 │ STEP 1  Geocode +     │───────▶│ AOI = boundary ⊕ 500 m buffer│
 │ Boundary (Nominatim,  │        │ (buffer so catchments that   │
 │ LGD/DataMeet)         │        │  originate outside are whole)│
 └───────────────────────┘        └──────────────┬───────────────┘
                                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STEP 2  DEM ACQUISITION   (Copernicus GLO-30 COGs on AWS → windowed read) │
 │ STEP 3  PREPROCESS        (reproject → UTM │ fill/breach sinks │ smooth) │
 └──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
   ┌────────────────┬───────────────┼────────────────┬─────────────────┐
   ▼                ▼               ▼                ▼                 ▼
┌────────┐  ┌──────────────┐  ┌───────────┐  ┌─────────────┐  ┌──────────────┐
│STEP 4  │  │STEP 5        │  │STEP 6     │  │STEP 7       │  │STEP 8        │
│CONTOURS│  │SLOPE/ASPECT  │  │TWI / TPI  │  │FLOW DIR (D8)│  │DEPRESSIONS   │
│(FR-2)  │  │HILLSHADE     │  │curvature  │  │→ FLOW ACC   │  │(fill−orig)   │
└────────┘  └──────────────┘  └───────────┘  │→ STREAMS    │  └──────────────┘
                                             └──────┬──────┘
   ┌──────────────┐  ┌──────────────┐  ┌────────────┴───┐
   │ LULC (ESA    │  │ SOIL         │  │ OSM Overpass:  │
   │ WorldCover)  │  │ (SoilGrids   │  │ buildings,roads│
   │              │  │ → HSG A/B/C/D)│ │ water, canals  │
   └──────┬───────┘  └──────┬───────┘  └────────┬───────┘
          └─────────────────┼───────────────────┘
                            ▼
              ┌───────────────────────────────┐      ┌─────────────────────┐
              │ STEP 9  AVAILABLE-LAND MASK   │      │ STEP 10  RAINFALL   │
              │ exclusion + morphological     │      │ 30-yr daily → mean, │
              │ opening + connected components│      │ CV, 75% dependable, │
              │ → parcels ≥ 400 m²   (FR-3)   │      │ monthly normals(FR-5)│
              └───────────────┬───────────────┘      └──────────┬──────────┘
                              └───────────┬─────────────────────┘
                                          ▼
     ╔═══════════════════════════════════════════════════════════════════╗
     ║ STEP 11 ★ SUITABILITY ENGINE (AI CORE)                            ║
     ║   normalise criteria → AHP weights → weighted overlay             ║
     ║   → RandomForest probability → fuse → DBSCAN cluster → rank       ║
     ║   OUT: suitability raster + Top-N candidate sites + explanations  ║
     ╚═══════════════════════════════════┬═══════════════════════════════╝
                                         ▼
                        ┌────────────────────────────────┐
                        │ User selects a site (or Top-1) │
                        └────────────────┬───────────────┘
                                         ▼
     ┌───────────────────────────────────────────────────────────────────┐
     │ STEP 12  CATCHMENT DELINEATION  (snap pour point → upstream area) │
     │          → area (ha), mean slope, flow-path length, Tc    (FR-4)  │
     └───────────────────────────────────┬───────────────────────────────┘
                                         ▼
     ┌───────────────────────────────────────────────────────────────────┐
     │ STEP 13  RUNOFF  — zonal stats (LULC × HSG) → composite CN        │
     │          → SCS-CN  Q = (P−0.3S)²/(P+0.7S)  → annual & monthly m³  │
     │          → cross-check with Strange's table + Rational peak (FR-6)│
     └───────────────────────────────────┬───────────────────────────────┘
                                         ▼
     ┌───────────────────────────────────────────────────────────────────┐
     │ STEP 14  POND DESIGN — stage–storage curve from DEM               │
     │          → optimise depth d* s.t. capacity ≈ 75%-dependable runoff│
     │          → prismoidal volume, side slope, freeboard, spillway     │
     │          → water balance (evaporation + seepage) → reliability    │
     │          → excavation cost estimate                        (FR-7) │
     └───────────────────────────────────┬───────────────────────────────┘
                                         ▼
     ┌───────────────────────────────────────────────────────────────────┐
     │ STEP 15  OVERLAY & REPORT — all layers on map, charts, stat cards,│
     │          PDF / GeoJSON / KML / Shapefile export            (FR-8) │
     └───────────────────────────────────────────────────────────────────┘
```

## 3.4 Sequence Diagram — "Analyse this village" (async job)

```
User      React UI        FastAPI          Redis/Celery     Worker       External APIs    PostGIS/COG
 │           │               │                  │             │                │              │
 ├─select───▶│               │                  │             │                │              │
 │           ├─POST /analysis───────────────────▶             │                │              │
 │           │               ├─enqueue job──────▶             │                │              │
 │           │◀─202 {job_id, status_url}────────┤             │                │              │
 │           ├─WS /ws/analysis/{job_id}─────────▶             │                │              │
 │           │               │                  ├─dispatch───▶│                │              │
 │           │               │                  │             ├─GET boundary──▶│              │
 │           │◀═progress 10% "boundary"══════════════════════─┤                │              │
 │           │               │                  │             ├─GET DEM───────▶│              │
 │           │               │                  │             ├─write COG─────────────────────▶
 │           │◀═progress 30% "terrain"═══════════════════════─┤                │              │
 │           │               │                  │             ├─fill+flow+acc (CPU)           │
 │           │◀═progress 50% "hydrology"════════════════════──┤                │              │
 │           │               │                  │             ├─GET rainfall──▶│              │
 │           │               │                  │             ├─GET lulc/soil─▶│              │
 │           │◀═progress 70% "suitability"══════════════════──┤                │              │
 │           │               │                  │             ├─AHP + RF + DBSCAN             │
 │           │◀═progress 90% "pond design"══════════════════──┤                │              │
 │           │               │                  │             ├─persist results───────────────▶
 │           │◀═done {result_url}══════════════════════════───┤                │              │
 │           ├─GET /analysis/{id}/result────────▶             │                │              │
 │           │◀─200 composite JSON──────────────┤◀────────────────────────────────────────────┤
 │◀─render───┤ (map layers via XYZ tile URLs, vectors as MVT/GeoJSON)                         │
```

## 3.5 User Journey

1. **Land** → map of India, search box: *"Enter a village name"*.
2. **Search** → autocomplete list `Village, Block, District, State`. Select one.
3. **Context loads** (~3 s) → satellite imagery + boundary + rainfall summary card.
4. **Run Analysis** → progress bar with named steps; layers appear progressively.
5. **Explore** → toggle Contours / Slope / Streams / Available Land / Suitability heat-map.
6. **Recommendations** → ranked cards (Site 1 · Score 87/100). Hover highlights on map.
7. **Select a site** (recommended or manual click) → catchment polygon draws, right panel fills with
   catchment area, rainfall, runoff, depth, capacity, cost, reliability.
8. **Tune** → sliders for max depth, side slope, min parcel area → numbers recompute live.
9. **Export** → PDF report + GeoJSON bundle; save to project.

## 3.6 UI Wireframe

Single-screen layout: the map is the primary surface, controls left, results right. Nothing that
matters is behind a tab or a modal — a planner should be able to read the whole recommendation without
navigating.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  ◈ CONTOUR    [ Search village…                    🔍 ]        Rampur, Sehore, MP    │
├──────────────┬───────────────────────────────────────────────────┬───────────────────┤
│  LAYERS      │                                                   │  RESULTS          │
│              │                                                   │                   │
│ ☑ Satellite  │                                                   │ ┌───────────────┐ │
│ ☑ Boundary   │            ╱‾‾‾╲      ← contour lines             │ │ Catchment     │ │
│ ☑ Contours   │        ╱‾‾╱     ╲‾‾╲                              │ │  148.6 ha     │ │
│ ☐ Slope      │      ╱  ╱  ▓▓▓▓  ╲  ╲   ▓ = suitability heat      │ ├───────────────┤ │
│ ☐ Hillshade  │     │  │  ▓▓●▓▓▓  │  │   ● = selected site        │ │ Rainfall 75%  │ │
│ ☑ Streams    │     │  │   ▓▓▓▓   │  │                            │ │  921.5 mm     │ │
│ ☑ Avail-land │      ╲  ╲ ┄┄┄┄┄┄ ╱  ╱    ┄ = catchment outline    │ ├───────────────┤ │
│ ☑ Suitability│        ╲__╲____ ╱__╱                              │ │ Runoff        │ │
│              │            ╲___╱                                  │ │  537,634 m³   │ │
│ ─ CONTROLS ─ │                                                   │ ├───────────────┤ │
│ Interval 1 m │   ┌──────────────────────┐                        │ │ Pond depth    │ │
│ Max depth    │   │ ▂▄▆█ rainfall chart  │                        │ │  3.5 m        │ │
│   ▁▁▁●▁ 3.5m │   └──────────────────────┘                        │ ├───────────────┤ │
│ Side slope   │                                                   │ │ Capacity      │ │
│   1 : 1.5    │  ⊕ ⊖  ⟲   scale ├────┤ 500 m        © attribution │ │  7,684 m³     │ │
│              │                                                   │ └───────────────┘ │
│ [Run analysis]│                                                  │                   │
│              │                                                   │ RANKED SITES      │
│ ─ PROGRESS ─ │                                                   │ ┌───────────────┐ │
│ ▓▓▓▓▓▓▓░░ 70%│                                                   │ │ 1  Score 87   │ │
│ suitability… │                                                   │ │ 2  Score 81   │ │
│              │                                                   │ │ 3  Score 74   │ │
│              │                                                   │ └───────────────┘ │
│              │                                                   │ [Export PDF] [⬇]  │
└──────────────┴───────────────────────────────────────────────────┴───────────────────┘
```

**Interaction rules**
- Layers appear **progressively** as the pipeline completes — the user is never staring at a spinner.
- Hovering a ranked site card highlights it on the map; clicking selects it and refills the right panel.
- Every stat card is clickable and expands to show its derivation (composite CN breakdown, the
  stage–storage curve, per-criterion suitability contributions) — this is where FR-9 explainability
  and the *"maps and visualizations"* part of FR-8 actually live.
- Sliders recompute only the cheap downstream steps (depth → capacity → cost), never the whole raster
  pipeline, so tuning feels instant.

## 3.7 Analysis Job State Machine

Every long-running analysis is a job (ADR-2). Explicit states make progress reporting, retry and
cancellation well-defined rather than ad hoc.

```
                    ┌──────────┐
      POST /analysis│  QUEUED  │
      ─────────────▶│          │
                    └────┬─────┘
                         │ worker picks up
                         ▼
                    ┌──────────┐   step fails, attempt < 3    ┌───────────┐
                    │ RUNNING  │─────────────────────────────▶│ RETRYING  │
                    │          │◀─────────────────────────────│ (backoff) │
                    └──┬───┬───┘        retry succeeds        └─────┬─────┘
         all steps ok  │   │ optional step failed                   │ attempts
                       │   │ (e.g. soil provider down)              │ exhausted
                       ▼   ▼                                        ▼
              ┌──────────┐ ┌──────────────┐                  ┌──────────┐
              │   DONE   │ │   PARTIAL    │                  │  FAILED  │
              │          │ │ result + a   │                  │ RFC 7807 │
              │          │ │ warnings[]   │                  │ problem  │
              └──────────┘ └──────────────┘                  └──────────┘
                       ▲
      DELETE /analysis │                    ┌─────────────┐
      ─ ─ ─ ─ ─ ─ ─ ─ ─┴─ ─ ─ ─ ─ ─ ─ ─ ─ ▶│  CANCELLED  │
                                            └─────────────┘
```

| State | Meaning | Client behaviour |
|---|---|---|
| `QUEUED` | Accepted, awaiting a free worker | Show queue position |
| `RUNNING` | Executing; `current_step` + `progress_pct` advance | Progress bar with step names |
| `RETRYING` | A transient provider failure; exponential backoff with jitter | Keep the bar, surface the retry |
| `PARTIAL` | ★ **Core steps succeeded, an optional enrichment did not** | Render the result **with** the `warnings[]` banner |
| `DONE` | Every step succeeded | Render everything |
| `FAILED` | A core step failed after retries | Show the problem detail and what to try |
| `CANCELLED` | User abandoned it | Free the worker slot |

**`PARTIAL` is the state that makes the system usable in practice.** If SoilGrids is unreachable we
still have terrain, rainfall and a catchment; the system falls back to a default HSG, flags
`data_quality`, and returns a usable answer instead of failing the whole analysis (NFR-5). Designing
for partial success rather than all-or-nothing is the difference between a demo and a tool.

---

# 4. Proposed Technology Stack

## 4.1 Summary Table

| Layer | Technology | Justification |
|---|---|---|
| **Frontend framework** | React 18 + TypeScript + Vite | Component model fits layered map UI; TS prevents unit/CRS bugs |
| **Map engine** | **MapLibre GL JS** (fallback: Leaflet + react-leaflet) | GPU vector tiles, smooth 10k+ contour rendering, free & open |
| **Map helpers** | Turf.js, MapLibre-draw, `@mapbox/mapbox-gl-legend` | Client-side geometry ops, AOI drawing |
| **Charts** | Recharts (+ Plotly.js for stage–storage curve) | Rainfall bars, water-balance lines, hypsometric curve |
| **UI/State** | TailwindCSS + shadcn/ui, TanStack Query, Zustand | Fast, accessible; TanStack handles job polling & cache |
| **Backend framework** | **Python 3.11 + FastAPI + Uvicorn/Gunicorn** | Async IO for API fan-out; **auto OpenAPI docs = a graded deliverable**; Pydantic v2 validation |
| **Raster / geo core** | GDAL/OGR, **rasterio**, rioxarray, xarray, NumPy, SciPy | Industry standard; windowed COG reads avoid loading full rasters |
| **Vector geo** | **Shapely 2**, GeoPandas, Fiona, **pyproj** | Geometry ops, CRS transforms (incl. Kalianpur→WGS 84 datum shift for Indian cadastral uploads), zonal statistics |
| **India data access** | **`imdlib`** (IMD gridded rainfall), `owslib` (Bhuvan WMS/WCS), `pystac-client` (Bhoonidhi STAC), `requests` adapters for data.gov.in OGD | The India-native Tier-1 sources in §4.2 are reached through these; `imdlib` in particular is the only practical route to IMD's official 0.25° gridded record |
| **Hydrology** | **pysheds** (primary), RichDEM, WhiteboxTools (breach-depressions), `whitebox` py-API | Pure-Python D8/D∞ flow, flow accumulation, watershed delineation |
| **Image processing** | **OpenCV** (+ scikit-image) | *Explicitly suggested in the assignment*: contour extraction on elevation bands, morphological opening/closing on the availability mask, `connectedComponentsWithStats` for parcel extraction, water-body detection |
| **ML / AI** | scikit-learn (RandomForest, DBSCAN, StandardScaler), XGBoost, SHAP | Suitability probability, site clustering, explainability |
| **Deep learning (optional)** | PyTorch + segmentation-models-pytorch (U-Net) | Water-body / wasteland segmentation from Sentinel-2 |
| **LLM (optional, FR-14)** | Claude API (`claude-sonnet-5`) via `anthropic` SDK | Plain-language justification paragraph per site |
| **Async / queue** | **Celery 5 + Redis 7**, Flower | Long provider-bound jobs off the request thread; progress events. Independent fetches run concurrently via `asyncio.gather` inside a task |
| **Primary DB** | **PostgreSQL 16 + PostGIS 3.4** | Native geometry type, GIST index, `ST_Area(geography)`, spatial joins |
| **Cache** | Redis (L1 hot, TTL) + PostGIS/disk (L2 durable) | External API responses, DEM tiles, rainfall series |
| **Object store** | MinIO (S3-compatible) or a Docker volume | DEM & derivative **Cloud-Optimized GeoTIFFs** |
| **Raster tile server** | **TiTiler** (rio-tiler) | Serves COGs as XYZ PNG tiles with on-the-fly colormap/rescale |
| **ORM / migrations** | SQLAlchemy 2.0 + GeoAlchemy2 + Alembic | Typed models, versioned schema |
| **Report generation** | WeasyPrint (HTML→PDF) + Jinja2 + Matplotlib + contextily | Templated PDF with static map figures |
| **API docs** | FastAPI OpenAPI 3.1 + Swagger UI + Redoc; MkDocs Material | Deliverable: "API documentation" |
| **Testing** | pytest, pytest-cov, pytest-asyncio, httpx, **VCR.py/responses** (mock external APIs), Playwright (E2E) | Deterministic tests without hitting rate-limited APIs |
| **Quality** | ruff, black, mypy, pre-commit, GitHub Actions CI | Deliverable: "software design and code quality" (15 marks) |
| **Deployment** | Docker + Docker Compose, Nginx reverse proxy | One-command install → "Installation guide" deliverable |
| **Observability** | structlog (JSON), Prometheus + Grafana, Sentry | NFR-12 |

## 4.2 External Data Sources & APIs — **India-First Source Strategy**

> **Design rule.** This system is built *for India*, so for every data need an **India-native authoritative
> source is Tier-1 wherever one exists**; a global source is used only when it is finer-resolution, more
> programmatically accessible, or needed as a fallback. Several requirements (land category, existing
> structures, groundwater depth) have **no usable global equivalent** and can only be met by Indian sources.
> Every row below is wrapped in an Adapter class with caching, retry, rate-limiting and a declared fallback.

**Access-mode legend**

| Code | Meaning | Engineering consequence |
|---|---|---|
| `REST` | Documented JSON API | Call live, cache the response |
| `OGC` | WMS / WMTS / WCS service | Proxy as map tiles; token refresh if required |
| `STAC` | SpatioTemporal Asset Catalog search API | Query → asset URLs → windowed COG read |
| `PKG` | Python package wrapping a government file server | Harvest once, store in PostGIS |
| `BULK` | One-time download, seeded into PostGIS at build time | Zero runtime dependency, works offline |
| `MANUAL` | Portal download only, no API | Pre-harvest; **never** a live dependency |

### A. Elevation / DEM — FR-2, FR-4, FR-7, FR-15

> **A0 comes first by design.** Per ADR-7 the *user's own data* outranks any remote source: if a
> contour map is supplied, it is authoritative for that analysis. A0 and A1 are alternative
> implementations of the same protocol, so the pipeline below them is identical.

| # | Provider | Access | Endpoint / method | India coverage | Auth | Limits | Role |
|---|---|---|---|---|---|---|---|
| **A0** | ★ **Uploaded contour map (KML / KMZ)** | `UPLOAD` | `POST /api/v1/terrain/contour-map` (multipart). Parsed to contour LineStrings + elevations, then interpolated to a metric DEM raster (§6.10) | n/a — wherever the survey is | none | 50 MB | **TIER 0 — the user's own survey.** Typically 1 m interval from a real ground or photogrammetric survey, so **an order of magnitude better vertical resolution than any free global DEM** (1 m vs COP30's ~2 m LE90 over 30 m cells). Where a contour map exists it is the best terrain the system will ever see |
| **A1** | ★ **Copernicus DEM GLO-30 — direct from the AWS Open Data bucket** | `COG` | `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{N/S}{lat:02d}_00_{E/W}{lon:03d}_00_DEM/<same>.tif` — 1°×1° tiles, read with `rasterio` over `/vsicurl` | **Full** — global, 8°N–37°N covered | **None** | **None** | **PRIMARY.** Same COP30 data OpenTopography serves, but with **no key, no quota and no registration**. Tiles are **Cloud-Optimized GeoTIFFs supporting HTTP range requests**, so a windowed read fetches only the AOI: a 5×5 km window is **101 KB**, not the whole 3600×3600 tile. Best free vertical accuracy (~2 m LE90 vs SRTM's ±16 m). *Verified 2026-08: 1 arcsec (~30.9 m), float32, internally tiled 1024², overviews [2,4,8]; elevation at Bhopal 494.6 m against 495.0 m from two independent point APIs.* |
| A1b | **OpenTopography Global DEM** | `REST` | `GET portal.opentopography.org/API/globaldem?demtype=…&API_Key=` | Full | **Free key required** | ~500 req/day | **OPTIONAL, not on the critical path.** Useful only for DEM *variety* — SRTMGL1, NASADEM, AW3D30 for comparison studies. A1 already provides COP30 without a key, so nothing depends on this |
| **A2** | **CartoDEM 30 m via Bhoonidhi (NRSC / ISRO)** | `STAC` | `bhoonidhi.nrsc.gov.in/bhoonidhi-api/` — STAC-compliant Search API | **India only — the national DEM**, from Cartosat-1 stereo pairs | Register; API access on request (`bhoonidhi@nrsc.gov.in`) | Per-account | ★ **INDIA-NATIVE CROSS-CHECK.** Validate COP30 against the Survey-of-India-aligned national DEM and report the delta as part of the stated DEM uncertainty. Defensible in an Indian engineering review in a way a foreign DEM alone is not |
| A3 | **AWS Terrain Tiles** (Mapzen *terrarium*) | `REST` | `GET s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` → `elev = (R·256 + G + B/256) − 32768` | Full | None | **Unlimited** | **FALLBACK 1** — no key, no quota; we decode and mosaic tiles ourselves |
| A4 | **OpenTopoData** | `REST` | `GET api.opentopodata.org/v1/srtm30m?locations=lat,lon\|…` (100 pts/req) | Full | None | 1000/day, 1 req/s | **FALLBACK 2** + spot-height validation. **Self-hosted in Docker with local SRTM tiles for the demo** → unlimited and offline |
| A5 | **Open-Meteo Elevation** | `REST` | `GET api.open-meteo.com/v1/elevation?latitude=&longitude=` | Full (COP90) | None | High | Instant sanity check on a clicked point |
| A6 | **Bhuvan CartoDEM / contour WMS** | `OGC` | `bhuvan-vec2.nrsc.gov.in/bhuvan/wms` | India only | Bhuvan token (**expires daily**) | — | India-native DEM/contour reference layer in the UI |

### B. Rainfall & Climate — FR-5, FR-6, FR-13

| # | Provider | Access | Endpoint / method | Grid | Period | Auth | Role |
|---|---|---|---|---|---|---|---|
| **B1** | **IMD 0.25° gridded daily rainfall (IMD Pune)** via the `imdlib` package | `PKG` | `pip install imdlib` → `imd.get_data('rain', 1995, 2024, fn_format='yearwise')` → xarray/NetCDF | **0.25° (~27 km), rain-gauge interpolated** | **1901→present** | None | ★ **PRIMARY / AUTHORITATIVE FOR INDIA.** This is the official IMD record that Indian minor-irrigation design is expected to cite — a reanalysis product is not an acceptable substitute in a government file note. Not a REST API: `imdlib` pulls IMD's binary files, so we **harvest once and cache in PostGIS** |
| **B2** | **Open-Meteo Archive — ERA5-Land** | `REST` | `GET archive-api.open-meteo.com/v1/archive?latitude=&longitude=&start_date=&end_date=&daily=precipitation_sum,et0_fao_evapotranspiration&models=era5_land&timezone=Asia/Kolkata` | **0.1° (~11 km)** — finer than IMD | 1950→present | None | **PRIMARY for spatial detail**, and the **only source that also returns ET₀** (`et0_fao_evapotranspiration`), which the water balance in §6.7 requires. At 11 km it resolves intra-district variation that IMD's 27 km cell cannot |
| B3 | **NASA POWER** | `REST` | `GET power.larc.nasa.gov/api/temporal/daily/point?parameters=PRECTOTCORR,T2M&community=AG&latitude=&longitude=&start=&end=&format=JSON` | 0.5° × 0.625° | 1981→present | None | **INDEPENDENT CROSS-CHECK** for the ensemble; also supplies `T2M`, the mean temperature **Khosla's formula** needs (§6.6) |
| B4 | **CHIRPS v2** | `REST`/`BULK` | ClimateSERV API, or UCSB bulk | **0.05° (~5.5 km)** | 1981→present | None | **Finest gridded option**; satellite–gauge blend, performs well over monsoon India |
| B5 | **data.gov.in (OGD) — IMD district & sub-division rainfall** | `REST` | `GET api.data.gov.in/resource/{resource_id}?api-key=&format=json&filters[state]=` | District / met sub-division | varies | Free key | Official **aggregated** figures to quote in the report and sanity-check the gridded values |
| B6 | **India-WRIS / NWIC** | `MANUAL` | `indiawris.gov.in` dashboards + bulk download | Station & district | varies | Registration | Rainfall, reservoir level, river discharge. **No documented public REST API** — pre-harvest only, never a live dependency (see CH-25) |
| B7 | **IMD Evaporation Atlas / pan-evaporation normals** | `MANUAL` | IMD publication | Station | normals | — | Validates the ET₀-derived open-water evaporation; Indian values run ~1400–2600 mm/yr |

### C. Soil, Land Cover & **Land Category** — FR-3, FR-6

| # | Provider | Access | Endpoint / method | India coverage | Auth | Role |
|---|---|---|---|---|---|---|
| **C1** | **Bhuvan LULC 1:50,000 + Wasteland thematic layers (NRSC / ISRO)** | `OGC` | `bhuvan-vec2.nrsc.gov.in/bhuvan/wms` — thematic WMS (LULC, **Wasteland**, geomorphology, water bodies, 1:10k/1:50k/1:250k) | **India only** | Bhuvan token (expires daily) | ★ **PRIMARY for land *category*.** "**Wasteland**" is the actual administrative class from which land is allotted for ponds in India — **no global LULC product has an equivalent**. This is the single most India-specific layer in the system |
| **C2** | **ESA WorldCover 10 m** | `OGC`/`STAC` | `services.terrascope.be/wms/v2?LAYERS=WORLDCOVER_2021_MAP` or STAC → COG | Global incl. India | None | **PRIMARY for resolution** — 10 m vs Bhuvan's 1:50k line-work. Feeds the Curve Number and the exclusion mask. Used *with* C1, not instead of it |
| **C3** | **Bhuvan Panchayat 3.0** | `OGC`/`MANUAL` | `bhuvanpanchayat.nrsc.gov.in` | India, **village-level** | Registration | Built explicitly for **Gram Panchayat Development Plans** — thematic layers at exactly our unit of analysis, and the same audience as our users |
| C4 | **SoilGrids (ISRIC)** | `REST` | `GET rest.isric.org/soilgrids/v2.0/properties/query?lon=&lat=&property=clay&property=sand&property=silt&depth=0-5cm&value=mean` | Global, 250 m | None | **PRIMARY soil** → USDA texture triangle → **Hydrologic Soil Group A/B/C/D** → Curve Number |
| C5 | **NBSS&LUP soil map of India** (Nagpur) | `BULK` | Static shapefile, 1:250,000 | **India only** | — | India-native soil-series cross-check on the HSG assignment |
| C6 | Esri 10 m Land Cover / Dynamic World | `STAC` | Tile/STAC | Global | None | Third opinion; disagreement between C1/C2/C6 raises a `lulc_confidence` flag |

### D. Satellite Imagery — FR-1

| # | Provider | Access | Endpoint / method | India coverage | Auth | Role |
|---|---|---|---|---|---|---|
| D1 | **Esri World Imagery** | `REST` | `server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` | **Sub-metre over most of populated India** | None | **DEFAULT BASEMAP** — no key, XYZ tiles, instant |
| **D2** | **Bhuvan imagery** (LISS-III / LISS-IV / Cartosat) | `OGC` | `bhuvan-vec2.nrsc.gov.in/bhuvan/wms` | **India only** | Bhuvan token | ★ **INDIA-NATIVE BASEMAP** + all Bhuvan thematic overlays in one service |
| **D3** | **Bhoonidhi (Resourcesat-2/2A LISS-III, AWiFS)** | `STAC` | `bhoonidhi.nrsc.gov.in/bhoonidhi-api/` | **India only** | Registration | ISRO open EO data for analysis-grade work (surface-reflectance products) |
| D4 | **Copernicus Data Space (Sentinel-2)** | `REST` | `POST sh.dataspace.copernicus.eu/api/v1/process` (OAuth2 client-credentials) | Global, 10 m | Free account | **NDWI / NDVI analytic bands** → dry-season water-body detection, ML labels, validation |
| D5 | OSM raster | `REST` | `tile.openstreetmap.org/{z}/{x}/{y}.png` | Good label coverage in India | None | Reference/label basemap |

### E. Boundaries, Existing Assets & Land Records — FR-1, FR-3

| # | Provider | Access | Endpoint / method | India coverage | Auth | Role |
|---|---|---|---|---|---|---|
| **E1a** | ★ **SHRUG village and town *name* index** (SHRID→LGD crosswalk) | `BULK` | Harvard Dataverse `doi:10.7910/DVN/QVFBFT`, file `shrid_loc_names.tab`, 51 MB — direct API URL, no credentials | ★ **596,390 Census-2011 villages + towns, all-India**, with full state/district/sub-district hierarchy | **CC0 1.0**, none | ★ **PRIMARY VILLAGE IDENTITY SOURCE.** Seeded into PostGIS (M0-11): complete national coverage, offline, no rate limit, and the SHRID composes the Census-2011 codes so it serves as the canonical key CH-24 requires. **Verified downloadable and seeded** |
| **E1b** | SHRUG village *polygons* (Development Data Lab) | `MANUAL` | `devdatalab.org/shrug_download` — behind a POST form, **not a fetchable URL** | ~600,000 village boundaries | CC BY-NC-SA 4.0 (site); registration form | **Geometry only, and manual.** Verified: the Dataverse mirror holds only the socioeconomic `.dta` tables, the GitHub releases carry no assets, and the asset bucket is not listable. Drops into `data/seed/` and re-seeds at `boundary_level='village'` (M0-11d) — a file drop, no code change |
| **E2** | **LGD — Local Government Directory** | `BULK`/`REST` | `lgdirectory.gov.in` | All-India State→District→Block→Gram Panchayat→**Village** | Registration | **Village-level LGD codes need registration and are not seeded.** `villages.lgd_code` is therefore null; `census_2011_id` and `shrid` serve as the canonical key. Officials do work in LGD codes, so this remains the source to obtain |
| **E2b** | ★ **Gram Panchayat LGD codes** (same CC0 dataset as E1a) | `BULK` | Harvard Dataverse `doi:10.7910/DVN/QVFBFT`, file `All India Village to GP LGD codes.tab`, 56 MB | 638,847 rows; **93.4 % carry a GP LGD code** (98.1 % for Chhattisgarh), plus the Census-2001 village code | **CC0 1.0**, none | ★ **THE ONLY LGD CODE IN OPEN DATA — and it is the Panchayat's, not the village's.** Seeded as `gram_panchayats` + a many-to-many link, because **12,045 villages belong to two or more Panchayats**. Three reasons it earns its place: the Panchayat is the body that plans and executes MGNREGA water works, so it is the unit a proposal is addressed to; it is the only LGD code obtainable; and its names disambiguate villages the Census hierarchy cannot — Durg sub-district's two Khapris sit in `Khapri K` and `Khapri`. **Joined on the Census-2011 codes, never on names**: this file's district column reflects a later reorganisation than E1a's |
| E3 | **DataMeet Indian Village Boundaries** | `BULK` | `github.com/datameet/indian_village_boundaries` | **Nine states only** — br, ga, gj, ka, kl, mh, or, rj, sk. **Chhattisgarh is not among them**, so it cannot serve the target district (M0-15) | Open | Gap-fill for the states it does cover. Verified 2026-08: 196 MB, actively maintained |
| **E3b** | ★ **geoBoundaries gbOpen (ADM1/2/3)** | `BULK` | `geoboundaries.org/api/current/gbOpen/IND/{ADM1,ADM2,ADM3}` — direct GeoJSON, no credentials | ADM1 state (36) · ADM2 district (735) · ADM3 **sub-district (6,824)**. Stops three levels above a village | **ODbL 1.0**, none | ★ **ADMINISTRATIVE GEOMETRY, AS SEEDED.** Enough to place a village on a map, frame the view and constrain a search; explicitly *not* a village boundary, which `villages.boundary_level` records. Features carry no parent reference, so the hierarchy is rebuilt by PostGIS containment at seed time |
| **E4** | **Bhuvan MGNREGA geotagged assets** | `OGC` | Bhuvan MGNREGA thematic service | **India, village-level** — geotagged by MGNREGA Spatial Enumerators | Bhuvan token | ★ **EXISTING WATER-HARVESTING STRUCTURES.** Used three ways: (i) exclusion — don't duplicate an existing pond; (ii) validation of our recommendations; (iii) **ML training positives** (§6.5.4) — far better labels for rural India than OSM |
| **E5** | **Mission Amrit Sarovar** | `MANUAL` | `amritsarovar.gov.in` | **~70,000+ geotagged ponds** built since 2022 | — | Additional ML positives, plus direct policy alignment — this project is a planning tool *for* that mission. No API; harvest manually |
| E6 | **Overpass API (OSM)** | `REST` | `POST overpass-api.de/api/interpreter` (Overpass QL) | Roads/buildings good in India; **rural water bodies are sparsely mapped** | None | Infrastructure exclusion mask. **Deliberately *not* used for pond labels in India** — E4/E5 are the correct label source |
| E7 | **Nominatim** | `REST` | `GET nominatim.openstreetmap.org/search?q={village},{district},India&format=json` | Village *names* present; **polygons often missing in rural India** | None, **1 req/s** | Free-text name search only; the geometry always comes from E1 |
| E8 | **Bhu-Naksha / state land-record portals** | `MANUAL` | State-specific — Mahabhulekh (MH), Bhoomi (KA), Dharani (TS), … | **Fragmented per state, mostly no open API** | varies | Cadastral & government-land parcels → served by the **FR-11 upload endpoint**; see CH-3 |
| **E9** | **CGWB groundwater levels** (via India-WRIS / data.gov.in / India Data Portal) | `REST`/`MANUAL` | OGD resources; `api.data.gov.in/resource/{id}?api-key=` | ★ **22,965 observation wells, measured 4×/year since 1969** | Free key | ★ **WATER-TABLE DEPTH** — the constraint `d_max ≤ water_table_depth − 1 m` in §6.7 had no data source before this. Pre/post-monsoon levels also indicate recharge benefit |
| E10 | **SLUSI Watershed Atlas of India** | `BULK` | `slusi.dacnet.nic.in` | All-India 1:50,000 watershed delineation & coding | — | **Independent validation** of our delineated catchments (CH-20) |

**Sample Overpass QL used by the Land Service (E6):**
```
[out:json][timeout:60];
(
  way["building"]({{bbox}});
  way["highway"]({{bbox}});
  way["waterway"]({{bbox}});
  way["natural"="water"]({{bbox}});
  way["landuse"~"forest|residential|industrial|cemetery"]({{bbox}});
  relation["natural"="water"]({{bbox}});
);
out geom;
```

### F. Provider Fallback Chains

```
ElevationProvider (interface)          ── no credential is required anywhere in this chain
   ├─ 1st  CopernicusAwsCogAdapter   COP30 COG over /vsicurl — no key, no quota,  ★
   │                                 windowed reads. THE PRIMARY SOURCE.
   ├─ 2nd  AWSTerrainTilesAdapter    global merged raster from tiles, no key, unlimited
   ├─ 3rd  OpenTopoDataAdapter       points → gridded (self-host for unlimited/offline)
   ├─ 4th  OpenMeteoElevationAdapter single-point spot checks and validation
   ├─ 5th  CachedDEMRepository       previously downloaded, PostGIS bbox lookup
   ├─ opt  BhoonidhiCartoDEMAdapter  India-native cross-check (Ring 2, needs account)
   └─ opt  OpenTopographyAdapter     only for SRTM/NASADEM/AW3D30 variety (needs key)

RainfallProvider (interface)
   ├─ 1st  IMDGriddedAdapter        imdlib → authoritative Indian record  ★
   ├─ 2nd  OpenMeteoERA5LandAdapter 11 km + the ET0 the water balance needs
   ├─ 3rd  NasaPowerAdapter         independent, also supplies T2M
   ├─ 4th  ChirpsAdapter            5.5 km satellite-gauge blend
   └─ ensemble: median across available sources; inter-source σ is reported
                as the rainfall uncertainty, and IMD is the bias-correction anchor

VillageBoundaryProvider (interface)
   ├─ 1st  ShrugRepository          local PostGIS, 600k villages, offline  ★
   ├─ 2nd  DataMeetRepository       local PostGIS gap-fill
   ├─ 3rd  BhuvanPanchayatAdapter   village-level ISRO layer
   └─ 4th  NominatimAdapter         last resort; centroid + buffered AOI

LandCategoryProvider (interface)
   ├─ 1st  UploadedCadastralLayer   Panchayat's own records (FR-11)  — authoritative
   ├─ 2nd  BhuvanWastelandAdapter   ISRO wasteland class  ★ India-specific
   ├─ 3rd  ESAWorldCoverAdapter     10 m bare/sparse/grassland proxy
   └─ 4th  OSMLanduseAdapter        coarse proxy
```

### G. Alignment with Indian Water-Mission Programmes

The system is designed to plug into work that Indian administrations are *already funded to do* — this is
what makes it deployable rather than academic:

| Programme | Relevance | How the system serves it |
|---|---|---|
| **Mission Amrit Sarovar** (2022–) | Target of 75 ponds per district; ~70,000 built | Site selection and sizing for the next tranche; E5 gives existing sarovars as both exclusion and training data |
| **MGNREGA** (Schedule-I water-conservation works) | Farm ponds, check dams, percolation tanks are the largest asset class; ~50 lakh structures built 2015–2026 | Standard MGNREGA farm-pond dimensions used as design presets; E4 geotagged assets used for validation |
| **PMKSY — Watershed Development Component** | Ridge-to-valley watershed treatment planning | Our catchment delineation + suitability ranking is exactly the WDC-DPR planning input |
| **Atal Bhujal Yojana** | Groundwater recharge in stressed blocks | E9 CGWB levels flag stressed blocks; percolation-tank mode prioritises recharge over storage |
| **IMSD guidelines** (NRSA/ISRO) | The standard Indian methodology for RS/GIS-based water-harvesting site selection | Our AHP criteria set (§6.5.1) is derived from IMSD rather than invented |

---

# 5. API Design

## 5.1 Conventions

- Base URL `/api/v1` · JSON request/response · `snake_case` fields
- Geometry always **GeoJSON, EPSG:4326**; all measurements in **SI + hectares**, explicit unit suffixes
  (`area_ha`, `volume_m3`, `depth_m`, `rainfall_mm`, `cost_inr`)
- Long operations → `202 Accepted` + `{ job_id, status_url }`; poll `GET .../status` or subscribe `WS /ws/...`
- Errors → RFC 7807 Problem Details:
  ```json
  { "type": "/errors/dem-unavailable", "title": "DEM source unavailable",
    "status": 503, "detail": "All 4 elevation providers failed",
    "instance": "/api/v1/terrain/dem", "trace_id": "a1b2c3" }
  ```
- Pagination `?page=&page_size=` · Idempotency via `Idempotency-Key` header
- Auth `Authorization: Bearer <JWT>` (public read endpoints allow anonymous)
- Versioning in the path; deprecation via `Sunset` header

### HTTP status codes used

| Code | When | Body |
|---|---|---|
| `200` | Synchronous success | Result document |
| `201` | Resource created (project, uploaded parcel layer) | Created resource + `Location` |
| `202` | **Long analysis accepted** | `{job_id, status_url, websocket_url, estimated_duration_s}` |
| `204` | Cancel / delete succeeded | empty |
| `400` | Validation failure — bad geometry, AHP matrix with `CR ≥ 0.1` | Problem + `errors[]` per field |
| `401` / `403` | Missing/invalid token · authenticated but not permitted | Problem |
| `404` | Unknown `village_id`, `dem_id`, `job_id` | Problem |
| `409` | Idempotency conflict — same key, different payload | Problem + existing resource |
| `413` | Upload over 50 MB, or AOI over `MAX_AOI_KM2` | Problem + the limit |
| `422` | Semantically impossible request — pour point in the sea, no-data DEM cell, side-slope geometry cannot close | Problem + a suggested fix |
| `429` | Our rate limit tripped | Problem + `Retry-After` |
| `500` | Unexpected — logged with `trace_id` | Problem, **no stack trace to the client** |
| `503` | **All providers in a fallback chain failed**, or queue saturated | Problem + `Retry-After` |
| `504` | Upstream provider timeout | Problem naming the provider |

`422` deserves the distinction from `400`: the request is well-formed but the *world* makes it
unanswerable. That difference is what lets the UI say *"this point receives negligible runoff, try
lower in the valley"* rather than *"invalid input"*.

## 5.2 Endpoint Catalogue (72 endpoints in 10 modules)

### Module 1 — Villages & Location

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 1 | GET | `/api/v1/villages/search?q=&state=&district=&limit=` | Autocomplete village search (geocode) |
| 2 | GET | `/api/v1/villages/{village_id}` | Village metadata (LGD code, population, area) |
| 3 | GET | `/api/v1/villages/{village_id}/boundary` | Administrative boundary GeoJSON |
| 4 | POST | `/api/v1/villages/resolve` | Reverse-geocode `{lat,lon}` → village |
| 5 | GET | `/api/v1/villages/{village_id}/imagery` | Satellite tile URL template + attribution + dates **(FR-1)** |
| 6 | POST | `/api/v1/aoi/custom` | Register a user-drawn polygon as an AOI |

### Module 2 — Terrain & DEM **(FR-2)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 7 | POST | `/api/v1/terrain/dem` | Acquire/mosaic DEM for AOI → `dem_id`, stats, tile URL (async) |
| 8 | GET | `/api/v1/terrain/dem/{dem_id}` | DEM metadata: bbox, CRS, resolution, min/max/mean elevation, source |
| 9 | GET | `/api/v1/terrain/dem/{dem_id}/tiles/{z}/{x}/{y}.png` | Hillshade / elevation-ramp raster tiles |
| 10 | GET | `/api/v1/terrain/elevation?lat=&lon=` | Single point elevation (cached proxy) |
| 11 | POST | `/api/v1/terrain/elevation/batch` | Up to 1000 points in one call |
| 12 | POST | `/api/v1/terrain/contours` | Generate contours `{dem_id, interval_m, smooth, simplify_tol}` → GeoJSON **(FR-2)** |
| 13 | GET | `/api/v1/terrain/contours/{contour_id}?format=geojson\|mvt` | Fetch contours; MVT for fast map rendering |
| 14 | POST | `/api/v1/terrain/derivatives` | `{dem_id, products:[slope,aspect,hillshade,twi,tpi,curvature]}` |
| 15 | GET | `/api/v1/terrain/derivatives/{id}/tiles/{z}/{x}/{y}.png` | Derivative raster tiles |
| 16 | POST | `/api/v1/terrain/profile` | Elevation profile along a drawn line → cross-section chart |
| 17 | GET | `/api/v1/terrain/depressions?dem_id=` | Natural depressions (fill − original) with depth & volume |
| **17a** | **POST** | **`/api/v1/terrain/contour-map`** ★★ | **Upload a contour KML/KMZ → parse → interpolate → `dem_id` (FR-15)** |
| **17b** | **POST** | **`/api/v1/analyzeContour`** ★★ | **One-shot: upload a contour map → terrain + pond site + catchment JSON (FR-16).** Alias `/api/v1/findCatchment`. Composes 17a with `/hydrology/catchment` and the siting step, so the caller needs a single request |
| 17c | GET | `/api/v1/terrain/contour-map/{dem_id}/contours` | Echo the parsed contours as GeoJSON, for overlay and for verifying the parse |

### Module 3 — Hydrology **(FR-4)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 18 | POST | `/api/v1/hydrology/flow` | Fill sinks + flow direction (D8/D∞) + flow accumulation |
| 19 | POST | `/api/v1/hydrology/streams` | Stream network by accumulation threshold + Strahler order |
| 20 | POST | `/api/v1/hydrology/catchment` | **★ Delineate watershed for a pour point (core FR-4)** |
| 21 | POST | `/api/v1/hydrology/catchment/batch` | Delineate for many candidate points at once |
| 22 | GET | `/api/v1/hydrology/catchment/{catchment_id}` | Retrieve stored catchment + morphometrics |
| 23 | POST | `/api/v1/hydrology/snap` | Snap a clicked point to the nearest high-accumulation cell |
| 24 | POST | `/api/v1/hydrology/flowpath` | Trace the downstream flow path from a point |

### Module 4 — Rainfall & Climate **(FR-5)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 25 | GET | `/api/v1/rainfall/historical?lat=&lon=&start=&end=&source=&aggregate=` | Raw daily/monthly/annual series |
| 26 | GET | `/api/v1/rainfall/statistics?lat=&lon=&years=30` | **★ Design statistics bundle (core FR-5)** |
| 27 | GET | `/api/v1/rainfall/monthly-normals?lat=&lon=` | 12 monthly normals + monsoon share |
| 28 | GET | `/api/v1/rainfall/return-periods?lat=&lon=` | 1-day max rainfall for 2/5/10/25/50/100-yr return periods (Gumbel) |
| 29 | GET | `/api/v1/rainfall/idf?lat=&lon=&duration_min=` | Rainfall intensity for the Rational method |
| 30 | GET | `/api/v1/rainfall/sources` | Available providers, coverage, latency, health |
| 31 | GET | `/api/v1/climate/evaporation?lat=&lon=` | Monthly ET₀ / open-water evaporation (for water balance) |

### Module 5 — Land, LULC, Soil & Availability **(FR-3)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 32 | GET | `/api/v1/land/lulc?bbox=&source=` | Land-cover raster id + class-wise area breakdown |
| 33 | GET | `/api/v1/land/soil?lat=&lon=` | Clay/sand/silt %, USDA texture, **Hydrologic Soil Group**, infiltration rate |
| 34 | POST | `/api/v1/land/soil/zonal` | Area-weighted HSG composition of a polygon (needed for composite CN) |
| 35 | POST | `/api/v1/land/available` | **★ Available-land parcels after exclusion masking (core FR-3)** |
| 36 | POST | `/api/v1/land/parcels/upload` | Upload cadastral / government-land GeoJSON or Shapefile(.zip) **(FR-11)** |
| 37 | GET | `/api/v1/land/parcels?village_id=` | List uploaded ownership parcels |
| 38 | GET | `/api/v1/land/waterbodies?bbox=` | Existing tanks/ponds from OSM + NDWI detection |
| 39 | GET | `/api/v1/land/infrastructure?bbox=` | Buildings, roads, canals (exclusion inputs) |

### Module 6 — Suitability Engine ★ (AI core)

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 40 | GET | `/api/v1/suitability/criteria` | Criteria catalogue: default weights, normalisation curves, direction |
| 41 | POST | `/api/v1/suitability/weights/ahp` | Pairwise matrix → eigenvector weights + **consistency ratio** |
| 42 | POST | `/api/v1/suitability/analyze` | **★ Run the suitability model** (async) → `analysis_id` |
| 43 | GET | `/api/v1/suitability/{analysis_id}` | Suitability raster tile URL + class-area statistics |
| 44 | GET | `/api/v1/suitability/{analysis_id}/sites?limit=10` | **★ Top-N ranked candidate sites with scores** |
| 45 | GET | `/api/v1/suitability/{analysis_id}/sites/{site_id}/explain` | Per-criterion contribution + SHAP values **(explainability)** |
| 46 | POST | `/api/v1/suitability/simulate` | What-if: re-score with modified weights without re-running rasters |

### Module 7 — Runoff & Pond Design **(FR-6, FR-7)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 47 | POST | `/api/v1/runoff/curve-number` | Composite CN from LULC × HSG + AMC adjustment |
| 48 | POST | `/api/v1/runoff/estimate` | **★ SCS-CN runoff volume (core FR-6)** — annual, monthly, dependable |
| 49 | POST | `/api/v1/runoff/peak-discharge` | Rational-method peak flow → spillway sizing |
| 50 | POST | `/api/v1/pond/stage-storage` | Elevation ↔ area ↔ volume curve derived from the DEM |
| 51 | POST | `/api/v1/pond/design` | **★ Recommend depth, dimensions, capacity, cost (core FR-7)** |
| 52 | POST | `/api/v1/pond/water-balance` | Month-wise inflow − evaporation − seepage → reliability, dry-out month **(FR-13)** |
| 53 | POST | `/api/v1/pond/optimize` | Maximise stored volume subject to depth/area/budget constraints |
| 54 | POST | `/api/v1/pond/compare` | Side-by-side comparison of 2–5 designs **(FR-12)** |

### Module 8 — Analysis Orchestration **(FR-8)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 55 | POST | `/api/v1/analysis` | **★ One-click full pipeline** → `202 {job_id}` |
| 56 | GET | `/api/v1/analysis/{job_id}/status` | `{state, progress_pct, current_step, steps[], eta_s}` |
| 57 | GET | `/api/v1/analysis/{job_id}/result` | Full composite result document |
| 58 | GET | `/api/v1/analysis/{job_id}/overlay` | **★ Combined layer bundle for the map (core FR-8)** |
| 59 | DELETE | `/api/v1/analysis/{job_id}` | Cancel a running job |
| 60 | WS | `/ws/analysis/{job_id}` | Live progress stream |

### Module 9 — Projects, Reports & Export **(FR-10)**

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 61 | POST / GET | `/api/v1/projects` | Create / list saved projects |
| 62 | GET/PUT/DELETE | `/api/v1/projects/{id}` | Read / update / delete |
| 63 | POST | `/api/v1/projects/{id}/sites` | Bookmark a chosen site into the project |
| 64 | POST | `/api/v1/reports/generate` | Generate a PDF/DOCX technical report (async) |
| 65 | GET | `/api/v1/reports/{report_id}/download` | Download the generated report |
| 66 | GET | `/api/v1/export/{analysis_id}?format=geojson\|kml\|shp\|geotiff\|csv` | GIS-ready export |

### Module 10 — Auth, Admin & System

| # | Method | Endpoint | Purpose |
|---|---|---|---|
| 67 | POST | `/api/v1/auth/register` · `/login` · `/refresh` · `/logout` | JWT authentication |
| 68 | GET | `/api/v1/auth/me` | Current user profile & role |
| 69 | GET | `/api/v1/health` · `/health/ready` | Liveness / readiness probes |
| 70 | GET | `/api/v1/system/providers` | External-provider health, quota used, circuit-breaker state |
| 71 | GET | `/api/v1/system/cache/stats` · DELETE `/api/v1/system/cache` | Cache introspection & invalidation |
| 72 | GET | `/docs` · `/redoc` · `/openapi.json` | Auto-generated API documentation |

## 5.3 Detailed Contracts for the Five Critical Endpoints

### (a) `POST /api/v1/hydrology/catchment` — FR-4

**Request**
```json
{
  "point": { "lat": 23.2599, "lon": 77.4126 },
  "dem_id": "dem_9f3a21",
  "snap": true,
  "snap_radius_m": 60,
  "method": "d8",
  "min_accumulation_cells": 100,
  "simplify_tolerance_m": 5
}
```

**Response `200`**
```json
{
  "catchment_id": "cat_7b21ef",
  "snapped_point": { "lat": 23.25987, "lon": 77.41243, "moved_m": 27.4 },
  "geometry": { "type": "Polygon", "coordinates": [[[77.40,23.25], "..."]] },
  "metrics": {
    "area_ha": 148.6,
    "area_km2": 1.486,
    "perimeter_m": 6420,
    "elevation_min_m": 452.1,
    "elevation_max_m": 511.8,
    "relief_m": 59.7,
    "mean_slope_pct": 3.8,
    "longest_flow_path_m": 2140,
    "time_of_concentration_min": 28.3,
    "form_factor": 0.32,
    "drainage_density_km_per_km2": 1.87,
    "outlet_elevation_m": 452.1
  },
  "streams": { "type": "FeatureCollection", "features": ["..."] },
  "quality": {
    "dem_source": "COP30",
    "dem_resolution_m": 30,
    "touches_aoi_boundary": false,
    "confidence": "high",
    "warnings": []
  },
  "computed_at": "2026-08-09T10:14:22Z"
}
```

### (b) `GET /api/v1/rainfall/statistics` — FR-5

**Request** `?lat=23.2599&lon=77.4126&years=30&source=ensemble`

**Response `200`**
```json
{
  "location": { "lat": 23.2599, "lon": 77.4126 },
  "period": { "start": "1995-01-01", "end": "2024-12-31", "n_years": 30 },
  "sources_used": ["open_meteo_era5", "nasa_power"],
  "annual": {
    "mean_mm": 1146.3, "median_mm": 1102.0, "std_dev_mm": 271.4,
    "coefficient_of_variation": 0.237,
    "min_mm": 612.0, "min_year": 2002,
    "max_mm": 1789.0, "max_year": 2019,
    "dependable_50_mm": 1102.0,
    "dependable_75_mm": 921.5,
    "dependable_90_mm": 782.0,
    "trend_mm_per_decade": -18.4
  },
  "monsoon": {
    "type": "southwest",
    "months": [6,7,8,9],
    "mean_mm": 986.2,
    "share_pct": 86.0,
    "note": "Monsoon window is region-derived, not hard-coded: SW monsoon Jun–Sep for most of India, but NE (retreating) monsoon Oct–Dec dominates Tamil Nadu, coastal Andhra, Rayalaseema, south-interior Karnataka and parts of Kerala. Hard-coding Jun–Sep would mis-state the design season for the entire southeast peninsula."
  },
  "monthly_normals_mm": [11.2,9.8,7.4,4.1,12.6,148.9,372.4,341.7,123.2,28.1,12.3,6.4],
  "rainy_days_per_year": 48.3,
  "max_1day_mm": 218.6,
  "return_periods_1day_mm": { "2": 92.1, "5": 128.4, "10": 152.7, "25": 183.9, "50": 207.2, "100": 230.4 },
  "annual_series": [{ "year": 1995, "rainfall_mm": 1088.4, "rainy_days": 51 }],
  "uncertainty": { "inter_source_std_mm": 63.2, "confidence": "medium" }
}
```

### (c) `POST /api/v1/runoff/estimate` — FR-6

**Request**
```json
{
  "catchment_id": "cat_7b21ef",
  "method": "scs_cn",
  "rainfall": { "source": "ensemble", "scenario": "dependable_75" },
  "amc": "II",
  "initial_abstraction_ratio": 0.3,
  "cross_check": ["strange", "rational"]
}
```

**Response `200`**
```json
{
  "runoff_id": "run_44c1a0",
  "catchment_area_ha": 148.6,
  "curve_number": {
    "composite_cn_amc2": 78.4,
    "composite_cn_amc1": 60.2,
    "composite_cn_amc3": 89.6,
    "breakdown": [
      { "lulc": "cropland",  "hsg": "C", "area_ha": 92.3, "cn": 82, "weight": 0.621 },
      { "lulc": "shrubland", "hsg": "C", "area_ha": 38.1, "cn": 74, "weight": 0.256 },
      { "lulc": "built_up",  "hsg": "C", "area_ha": 18.2, "cn": 90, "weight": 0.123 }
    ]
  },
  "potential_retention_S_mm": 69.9,
  "initial_abstraction_mm": 20.97,
  "annual": {
    "rainfall_mm": 921.5,
    "runoff_depth_mm": 361.8,
    "runoff_volume_m3": 537634,
    "runoff_coefficient": 0.393
  },
  "monthly": [
    { "month": 7, "rainfall_mm": 372.4, "runoff_mm": 178.2, "runoff_volume_m3": 264793 }
  ],
  "peak_discharge_m3s": 8.94,
  "cross_check": {
    "strange_table_m3": 498200,
    "rational_method_peak_m3s": 9.41,
    "agreement": "within 8%"
  },
  "assumptions": [
    "Ia = 0.3S (Indian practice, CWC/IMD) instead of the standard 0.2S",
    "AMC-II (average antecedent moisture) assumed",
    "CN from NRCS TR-55 table mapped onto ESA WorldCover classes"
  ]
}
```

### (d) `POST /api/v1/pond/design` — FR-7

**Request**
```json
{
  "site": { "type": "Point", "coordinates": [77.41243, 23.25987] },
  "dem_id": "dem_9f3a21",
  "catchment_id": "cat_7b21ef",
  "runoff_id": "run_44c1a0",
  "constraints": {
    "max_depth_m": 4.5, "min_depth_m": 2.5,
    "side_slope_h_to_v": 1.5,
    "shape": "rectangular",
    "freeboard_m": 0.5,
    "max_top_area_m2": 6000,
    "budget_inr": 1200000,
    "excavation_rate_inr_per_m3": 130
  },
  "objective": "maximise_reliable_storage"
}
```

**Response `200`**
```json
{
  "design_id": "pnd_e51902",
  "recommended": {
    "depth_m": 3.5,
    "freeboard_m": 0.5,
    "top_length_m": 60.0, "top_width_m": 45.0, "top_area_m2": 2700,
    "bottom_length_m": 49.5, "bottom_width_m": 34.5, "bottom_area_m2": 1707.8,
    "side_slope": "1V : 1.5H",
    "gross_capacity_m3": 7647.6,
    "live_storage_m3": 6882.8,
    "dead_storage_silt_m3": 764.8,
    "excavation_volume_m3": 7647.6,
    "embankment_volume_m3": 1830.0,
    "spillway_width_m": 4.2,
    "estimated_cost_inr": 1122284
  },
  "hydrological_check": {
    "annual_inflow_m3": 537634,
    "capacity_to_inflow_ratio": 0.0142,
    "expected_fillings_per_year": 3.2,
    "probability_of_filling_pct": 94,
    "months_water_available": 8,
    "reliability_pct": 87
  },
  "water_balance_monthly": [
    { "month": 6, "inflow_m3": 21500, "evaporation_m3": 405, "seepage_m3": 648, "storage_end_m3": 7648 }
  ],
  "stage_storage_curve": [
    { "depth_m": 0.5, "area_m2": 1810, "volume_m3": 878 },
    { "depth_m": 1.0, "area_m2": 1998, "volume_m3": 1830 },
    { "depth_m": 3.5, "area_m2": 2700, "volume_m3": 7648 }
  ],
  "recommendations": [
    "Soil HSG-C (clayey loam) → lining not required; expected seepage 8 mm/day",
    "Provide a 4.2 m waste weir on the north bund for the 25-yr storm",
    "Provide a silt trap at the inlet; desilt every 3 years"
  ],
  "warnings": ["Depth limited to 3.5 m by the 4.5 m constraint, not by terrain"]
}
```

### (e) `POST /api/v1/analysis` — the one-click pipeline, FR-8

**Request**
```json
{
  "village_id": "lgd_486213",
  "options": {
    "dem_source": "cop30",
    "contour_interval_m": 1.0,
    "rainfall_years": 30,
    "max_sites": 10,
    "suitability_weights": null,
    "include_ml_model": true,
    "generate_report": true
  }
}
```

**Response `202`**
```json
{
  "job_id": "job_c72fa1",
  "status": "queued",
  "status_url": "/api/v1/analysis/job_c72fa1/status",
  "websocket_url": "/ws/analysis/job_c72fa1",
  "estimated_duration_s": 75,
  "steps": ["boundary","dem","preprocess","contours","derivatives","hydrology",
            "lulc_soil","rainfall","availability","suitability","catchment",
            "runoff","pond_design","report"]
}
```

`GET /api/v1/analysis/job_c72fa1/status` →
```json
{
  "job_id": "job_c72fa1", "state": "running",
  "progress_pct": 55, "current_step": "suitability",
  "steps": [
    { "name": "dem", "state": "done", "duration_s": 12.4 },
    { "name": "hydrology", "state": "done", "duration_s": 18.9 },
    { "name": "suitability", "state": "running" }
  ],
  "eta_s": 32,
  "warnings": ["IMD provider unavailable — using ERA5 + NASA POWER ensemble"]
}
```

## 5.4 API Design Principles Applied

| Principle | How it is applied |
|---|---|
| Resource-oriented | Nouns (`/catchment`, `/pond/design`), verbs only for computations |
| Idempotency | Identical `(bbox, params)` hash returns the cached artefact id, no recompute |
| Async by default for heavy work | ADR-2 — no request ever exceeds a 30 s gateway timeout |
| Layer separation | Data endpoints (`/terrain/*`, `/rainfall/*`) usable standalone; `/analysis` merely orchestrates them |
| Provenance in every payload | `sources_used`, `dem_source`, `confidence`, `assumptions[]` |
| Self-documenting | FastAPI + Pydantic → OpenAPI 3.1 generated from the same models that validate |

---

# 6. Algorithms and Methodology

## 6.1 Algorithm Map

| Step | Algorithm | Reference | Library |
|---|---|---|---|
| DEM sink removal | Priority-Flood fill / Depression breaching | Wang & Liu (2006); Barnes (2014); Lindsay (2016) | pysheds / WhiteboxTools |
| Flat resolution | Gradient towards lower terrain | Garbrecht & Martz (1997) | RichDEM |
| Flow direction | **D8** (8-neighbour steepest descent) / D∞ | O'Callaghan & Mark (1984); Tarboton (1997) | pysheds |
| Flow accumulation | Topologically-ordered upstream cell count | Standard | pysheds |
| Watershed delineation | Reverse traversal of the D8 pointer grid from the pour point | Standard | pysheds |
| Contour extraction | Marching Squares / `cv2.findContours` on elevation bands | Lorensen & Cline (1987) | GDAL `gdal_contour`, OpenCV, skimage |
| Line simplification | Douglas–Peucker (zoom-dependent tolerance) | Douglas & Peucker (1973) | Shapely |
| Slope / aspect | 3×3 finite difference | Horn (1981) | NumPy / GDAL DEM |
| Wetness index | TWI = ln(α / tan β) | Beven & Kirkby (1979) | NumPy |
| Terrain position | TPI = z − mean(z in annulus) | Weiss (2001) | SciPy |
| Mask cleaning | Morphological opening/closing, connected components | — | **OpenCV** |
| Criteria weighting | **AHP** — eigenvector + Consistency Ratio | Saaty (1980) | NumPy |
| Suitability | Weighted Linear Combination on fuzzy-normalised criteria | Malczewski (1999) | NumPy |
| ML suitability | Random Forest / XGBoost with spatial block CV | Breiman (2001) | scikit-learn |
| Site clustering | DBSCAN on high-suitability pixels | Ester et al. (1996) | scikit-learn |
| Explainability | SHAP (TreeExplainer) | Lundberg & Lee (2017) | shap |
| Runoff | **SCS Curve Number** (with Ia = 0.3S) | USDA NRCS TR-55; CWC India | Custom |
| Peak flow | Rational method Q = CiA/3.6; Tc by Kirpich | Kirpich (1940) | Custom |
| Regional cross-check | Strange's runoff table | Strange (Indian practice) | Lookup |
| Rainfall dependability | Weibull plotting position P = m/(N+1) | — | NumPy |
| Extreme rainfall | Gumbel EV-I frequency analysis | Gumbel (1958) | SciPy |
| Storage capacity | DEM stage–area–volume + Prismoidal formula | — | rasterio + NumPy |
| Depth optimisation | 1-D constrained search over the stage–storage curve | — | SciPy `minimize_scalar` |
| Water balance | Monthly mass balance: S(t) = S(t−1) + I − E − Sp − O | FAO-56 | Custom |
| Water detection | NDWI = (Green − NIR)/(Green + NIR), Otsu threshold | McFeeters (1996); Otsu (1979) | rasterio + OpenCV |

## 6.2 Terrain Preprocessing (Step 3)

```
INPUT  raw DEM tiles (EPSG:4326)
1. Mosaic tiles → single raster
2. Reproject → local UTM zone   utm = 32600 + floor((lon+180)/6) + 1   [N hemisphere]
   INDIA spans UTM zones 42N–47N  →  EPSG:32642 … EPSG:32647
        zone 42N  66–72°E   Gujarat, W. Rajasthan
        zone 43N  72–78°E   Maharashtra, MP, Delhi, Haryana
        zone 44N  78–84°E   UP, Telangana, Chhattisgarh, TN
        zone 45N  84–90°E   Bihar, Jharkhand, W. Bengal, Odisha
        zone 46N  90–96°E   Assam, Meghalaya
        zone 47N  96–102°E  Arunachal Pradesh
   Why: all area/length/volume maths must be in metres, never degrees.
   NATIONAL FRAME: for outputs shared with government GIS, also emit EPSG:7755
        (WGS 84 / India NSF LCC — the National Spatial Framework). Legacy Survey of
        India / cadastral sheets use Kalianpur 1975 India zones (EPSG:24378–24382),
        so uploaded cadastral layers (FR-11) are datum-transformed on ingest rather
        than assumed to be WGS 84 — a silent ~100–400 m shift otherwise.
3. Void fill: interpolate NoData cells (inverse-distance / Delaunay)
4. Noise smoothing: 3×3 Gaussian (σ=0.6) — SRTM speckle creates false micro-basins
5. Hydrological conditioning:
     if fraction_of_flat_cells > 0.30:  breach_depressions()   # flat terrain
     else:                               fill_depressions()     # Priority-Flood
6. Save as Cloud-Optimized GeoTIFF (internal tiling 512, overviews 2/4/8/16, DEFLATE)
OUTPUT filled_dem.tif + depression_depth.tif (= filled − original)
```

`depression_depth.tif` is valuable in its own right: cells with high fill-depth are **natural
depressions** — pre-existing bowls where a pond needs the least excavation. This becomes a
suitability criterion.

## 6.3 Catchment Delineation (Step 12, FR-4)

```
INPUT  filled DEM, user pour point (lat, lon)

1. FLOW DIRECTION (D8)
   For each cell c, among 8 neighbours n:
        drop(n) = (z(c) − z(n)) / distance(c,n)        # diagonal distance = √2·cellsize
   fdir(c) = direction of max positive drop            # encoded 1,2,4,8,16,32,64,128
   if no positive drop → flat/pit → resolved in preprocessing

2. FLOW ACCUMULATION
   Topologically sort cells by the fdir DAG (Kahn / recursive with memoisation)
   acc(c) = 1 + Σ acc(u)   for every upstream u draining into c
   Complexity O(N); N = number of DEM cells

3. STREAM NETWORK
   stream = acc > T, with T = threshold_cells (default 100 cells ≈ 9 ha at 30 m)
   Vectorise → LineStrings → assign Strahler order

4. POUR-POINT SNAPPING  (critical practical step)
   Search a window of radius snap_radius_m around the click
   p* = argmax acc(c) within the window
   Report moved_m so the user sees the correction; expose snap_radius as a control

5. WATERSHED DELINEATION
   BFS/DFS upstream from p* over the reversed fdir graph
   catchment = { all cells whose flow path reaches p* }

6. VECTORISE & MEASURE
   polygonise mask → simplify (Douglas–Peucker, tol = 1 cell)
   area_m2 = count × cellsize²  (already in UTM, so this is exact)
   longest_flow_path L, relief H, mean slope S
   Kirpich:  Tc = 0.01947 · L^0.77 · S^(−0.385)      [L in m, S = H/L, Tc in minutes]

7. QUALITY CHECK
   if catchment touches the AOI edge → expand the AOI buffer and recompute (auto-retry once)
```

## 6.4 Available-Land Identification (Step 9, FR-3)

```
INPUT  AOI, LULC raster, slope raster, OSM features, (optional) uploaded ownership parcels

1. Build the EXCLUSION mask (binary raster, 1 = excluded)
     buildings        ⊕ buffer 50 m
     roads/railways   ⊕ buffer 20 m
     existing water   ⊕ buffer 100 m      (avoid duplicating an existing tank)
     forest / protected areas
     canals, power lines, burial grounds
     slope > max_slope_pct (default 5 %)   ← steep land = huge excavation cost
     LULC ∈ {built_up, permanent_water, tree_cover, snow}

2. Build the INCLUSION mask
     LULC ∈ {bare/sparse vegetation, grassland, shrubland, cropland*}
     (* cropland allowed only if flagged, since it is usually private)
     AND (if an ownership layer was uploaded) ownership ∈ {government, common, gairan, wasteland}

3. available = INCLUSION AND NOT EXCLUSION

4. MORPHOLOGICAL CLEANING  (OpenCV — this is where OpenCV genuinely earns its place)
     kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
     opened = cv2.morphologyEx(available, cv2.MORPH_OPEN,  kernel)   # remove speckle
     closed = cv2.morphologyEx(opened,    cv2.MORPH_CLOSE, kernel)   # close pinholes

5. PARCEL EXTRACTION
     n, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, 8)
     keep components with area × cellsize² ≥ min_area_m2 (default 400 m²)

6. VECTORISE + ATTRIBUTE
     for each parcel: polygonise → {area_ha, mean_slope, dominant_lulc, hsg,
                                    distance_to_road_m, distance_to_settlement_m,
                                    mean_flow_accumulation, ownership}

OUTPUT GeoJSON FeatureCollection of candidate parcels
```

## 6.5 ★ Suitability Engine — the AI Core (Step 11)

### 6.5.1 Criteria and Normalisation

| # | Criterion | Source | Direction | Fuzzy membership | Typical AHP weight |
|---|---|---|---|---|---|
| C1 | Flow accumulation (runoff contribution) | DEM | higher better | monotonic increasing (log-scaled) | 0.21 |
| C2 | Slope | DEM | lower better | trapezoidal, ideal 1–3 %, 0 penalised (poor drainage) | 0.18 |
| C3 | Depression depth (natural bowl) | fill − original | higher better | increasing, saturates at 3 m | 0.14 |
| C4 | Hydrologic Soil Group | SoilGrids | D > C > B > A | ordinal 1.0 / 0.75 / 0.4 / 0.15 | 0.13 |
| C5 | Land availability / ownership | LULC + OSM + upload | binary/graded | wasteland 1.0, grassland 0.8, cropland 0.3, else 0 | 0.12 |
| C6 | Distance to stream | derived | closer better | decreasing, 0 beyond 500 m | 0.08 |
| C7 | Distance to settlement | OSM | 200 m–2 km ideal | trapezoidal | 0.06 |
| C8 | Distance to existing water body | OSM/NDWI | farther better | increasing, saturates at 1 km | 0.05 |
| C9 | Topographic Wetness Index | DEM | higher better | increasing | 0.03 |

All criteria are rescaled to `[0, 1]` before combination — this is essential because they have
incompatible units (m, %, m³, categorical).

### 6.5.2 AHP Weight Derivation

```
1. Build the pairwise comparison matrix A (n×n), a_ij ∈ Saaty scale {1/9 … 9}, a_ji = 1/a_ij
2. Normalise each column, then w_i = row-mean of the normalised matrix
   (or principal eigenvector: A·w = λmax·w)
3. Consistency Index      CI = (λmax − n) / (n − 1)
4. Consistency Ratio      CR = CI / RI      [RI(n=9) = 1.45]
5. ACCEPT only if CR < 0.10, else return 400 and ask the expert to revise the matrix
```
This makes the weights **auditable** — a District Engineer can override them via
`POST /api/v1/suitability/weights/ahp` and the system will refuse an internally inconsistent judgement.

### 6.5.3 Weighted Overlay

```
S_ahp(x, y) = Σ_{i=1..n}  w_i · f_i( criterion_i(x, y) )        S ∈ [0, 1]

Hard constraint mask (Boolean AND) applied afterwards:
    S_final = S_ahp × mask_available × mask_slope_ok × mask_not_excluded
```

### 6.5.4 Machine-Learning Layer (the genuine "AI")

The problem: there is no labelled dataset of "good pond sites". The solution is **weak supervision**
from structures that already exist and evidently work.

```
TRAINING LABEL GENERATION  —  India-specific label sources, in priority order
  Positives:  existing, working water-harvesting structures in the same agro-climatic zone
              (a) ★ Bhuvan MGNREGA geotagged assets (E4) — farm ponds, check dams,
                    percolation tanks, geotagged on the ground by MGNREGA Spatial
                    Enumerators. Point locations + asset type + completion year.
              (b) ★ Mission Amrit Sarovar geotagged ponds (E5) — ~70,000 nationally
              (c)   NDWI on a dry-season Sentinel-2 composite + Otsu threshold
                    → persistent water bodies of 0.05–10 ha (filters rivers & puddles)
              (d)   OSM natural=water / landuse=reservoir polygons  — LAST, not first:
                    rural Indian water bodies are sparsely mapped in OSM, so using it
                    as the primary label source would bias training toward
                    well-mapped (i.e. peri-urban) areas
  Negatives:  random points in the same districts, ≥ 500 m from any positive,
              stratified so the slope/LULC distribution matches the study region
              (avoids the classifier simply learning "steep = bad")

  WHY THIS MATTERS: (a) and (b) are ground-truthed government records of structures
  that were actually built and are actually maintained. No global dataset offers an
  equivalent for India, and OSM alone would have given a biased, sparse label set.

FEATURES per point (the same 9 criteria, un-normalised + raw terrain stats)
  flow_acc, slope, tpi, twi, depression_depth, curvature, hsg, lulc_class,
  dist_stream, dist_settlement, dist_road, dist_waterbody, annual_rainfall, relief

MODEL     RandomForestClassifier(n_estimators=300, max_depth=None,
                                 class_weight="balanced", oob_score=True)
VALIDATION  SPATIAL BLOCK cross-validation (5 folds of contiguous 10 km blocks)
            — plain k-fold leaks, because nearby points are spatially autocorrelated
            and would give a falsely excellent score
METRICS     ROC-AUC, PR-AUC, precision@10; target ROC-AUC ≥ 0.80
OUTPUT      S_ml(x, y) = P(class = suitable pond site) ∈ [0, 1]

FUSION      S = α · S_ahp + (1 − α) · S_ml,     α = 0.6 by default
            α = 1.0 (pure AHP) if the ML model fails validation → always explainable
```

### 6.5.5 Candidate Site Extraction

```
1. Threshold:  high = { pixels : S ≥ 0.70 }
2. DBSCAN(eps = 3 cells, min_samples = 5) on the coordinates of `high`
     → contiguous clusters, discarding isolated single-pixel noise
3. For each cluster: representative point = the pixel of maximum S
     (not the centroid — the centroid may fall on an excluded pixel)
4. Delineate the catchment for each representative point (batch)
5. Composite ranking score (0–100):
        R = 100 · ( 0.45·S̄ + 0.30·norm(catchment_area) + 0.25·norm(storage_potential) )
6. Non-maximum suppression: drop any site within 300 m of a better-ranked site
     — prevents ten recommendations sitting on the same hillside
7. Return Top-N sorted by R, each with its per-criterion breakdown and SHAP values
```

### 6.5.6 Explainability

For every returned site, `/explain` yields:
- **Criterion table:** raw value → normalised value → weight → contribution to the score.
- **SHAP waterfall** for the ML component (which features pushed the probability up/down).
- **Plain-language sentence** (FR-14): *"Site 1 scores 87/100 mainly because it lies in a natural
  1.8 m depression (contribution +0.21) at the outlet of a 149 ha catchment, on clayey HSG-C soil
  that limits seepage. It is on government wasteland 640 m from the settlement."*

## 6.6 Runoff Estimation — SCS Curve Number (Step 13, FR-6)

**Step 1 — Hydrologic Soil Group** from SoilGrids texture:

| HSG | Texture | Infiltration | Runoff |
|---|---|---|---|
| A | Sand, loamy sand | > 7.6 mm/h | Low |
| B | Silt loam, loam | 3.8–7.6 mm/h | Moderate |
| C | Sandy clay loam | 1.3–3.8 mm/h | Moderately high |
| D | Clay, silty clay | < 1.3 mm/h | High |

**Step 2 — Composite Curve Number** (area-weighted over the catchment):

```
CN_composite = Σ (A_i · CN_i) / Σ A_i          over every (LULC × HSG) zone

Example CN(II) lookup (NRCS TR-55 mapped to ESA WorldCover classes):
                        HSG-A  HSG-B  HSG-C  HSG-D
  Cropland (row crop)     67     78     85     89
  Grassland (fair)        49     69     79     84
  Shrubland               35     56     70     77
  Tree cover              30     55     70     77
  Bare / sparse           77     86     91     94
  Built-up                77     85     90     92
```

> **India adaptation.** The raw TR-55 table is US land-use terminology. The system uses the CN values
> for **Indian land use and cover complexes** from the *Handbook of Hydrology* (Ministry of Agriculture,
> Government of India) and the **CGWB** recharge manuals, which add classes TR-55 lacks — *kharif/rabi
> cropped land, fallow, wasteland, scrub forest, degraded pasture*. The Bhuvan LULC 1:50k classes (C1)
> map onto these Indian classes **directly**, whereas ESA WorldCover classes have to be translated —
> a second reason C1 and C2 are used together rather than C2 alone.

**Step 3 — AMC adjustment** (from the 5-day antecedent rainfall):

```
AMC-I  (dry):  CN_I   = 4.2·CN_II / (10 − 0.058·CN_II)
AMC-II (avg):  CN_II  = table value
AMC-III (wet): CN_III = 23·CN_II / (10 + 0.13·CN_II)
```

**Step 4 — Runoff depth:**

```
Potential maximum retention:      S = (25400 / CN) − 254            [mm]
Initial abstraction:              Ia = λ·S,   λ = 0.3   ← Indian practice (CWC/IMD),
                                                          not the US default 0.2
                (P − Ia)²
Runoff depth:    Q = ───────────────      if P > Ia ;   Q = 0 otherwise
                (P − Ia + S)

with λ = 0.3:    Q = (P − 0.3S)² / (P + 0.7S)

Runoff volume:   V = Q/1000 × A_catchment        [m³]  (Q in mm, A in m²)
Runoff coeff:    C = Q / P
```

**Step 5 — Applied at three time scales**
- **Daily** over the 30-year record → then summed to annual (correct; applying the formula to an
  annual rainfall total directly would badly over-estimate runoff).
- **Monthly** aggregation → drives the water balance.
- **Design year:** the 75 % dependable annual rainfall → the value used for sizing.

**Step 6 — Cross-check against Indian regional empirical methods.**
SCS-CN is a US model (CH-15). For an Indian system it must be cross-checked against the empirical
rainfall–runoff relations that were **derived from Indian gauged catchments**, and the system picks the
formula whose region of derivation matches the village's state:

| Method | Derived from | Applicable region | Relation |
|---|---|---|---|
| **Strange's tables** (1928) | Border catchments of **Maharashtra & Karnataka** | Deccan plateau | Runoff as a **% of monsoon rainfall**, tabulated for *Good / Average / Bad* catchment character |
| **Inglis & DeSouza** (1929) | **53 stream-gauging sites in Western India** | Maharashtra, Western Ghats | Ghat areas: `R = 0.85 P − 30.5` · Plains: `R = (P − 17.8)·P / 254` — *R, P in cm/year* |
| **Khosla** (1960) | Indian + US catchments, monthly | General India, preliminary yield | `R_m = P_m − L_m`, `L_m = 0.48·T_m` (T_m = mean monthly temp °C, L_m ≥ 0) — needs the `T2M` from NASA POWER (B3) |
| **Barlow** (1912) | **Uttar Pradesh** catchments | Indo-Gangetic plain | `R = K·P`, K by catchment class (flat/cultivated → hilly/barren) |

```
RegionalRunoffSelector(state, agro_climatic_zone):
    Maharashtra / Karnataka / Deccan  → Strange  (primary cross-check)
    Western Ghats / Konkan            → Inglis & DeSouza (ghat variant)
    Uttar Pradesh / Bihar / Gangetic  → Barlow
    otherwise                         → Khosla (general) + Strange

Report SCS-CN as the headline value, the regional method as the cross-check.
If |SCS-CN − regional| / regional > 25 %  →  emit a warning in the response and
show both figures in the PDF, rather than silently presenting one number.
```

## 6.7 Pond Depth & Storage Capacity (Step 14, FR-7)

### Method A — DEM-derived stage–storage (preferred, terrain-accurate)

```
INPUT filled DEM, site point p, z0 = elevation(p)

for h in arange(0.25, max_depth, 0.25):          # water level above the bed
    level = z0 + h
    # flood-fill from p over cells with z < level, 8-connected
    region  = connected_flood_fill(dem, p, level)
    area(h)   = |region| × cellsize²
    volume(h) = Σ_{c ∈ region} (level − z(c)) × cellsize²

OUTPUT the stage–area–volume curve  →  returned to the client and plotted
```
This automatically accounts for the real bowl shape — a natural depression yields far more storage
per m³ excavated than a flat field, and the curve shows it.

### Method B — Prismoidal excavation geometry (for a constructed pond on flat ground)

```
Given depth d and side slope 1V : zH,
    L_bottom = L_top − 2·z·d
    W_bottom = W_top − 2·z·d
    A_top    = L_top × W_top
    A_bottom = L_bottom × W_bottom

Prismoidal formula:
    V = (d / 3) · ( A_top + A_bottom + √(A_top · A_bottom) )

Constraint (geometry must close):  min(L_top, W_top) > 2·z·d
```

### Depth optimisation

```
OBJECTIVE  maximise reliable storage per rupee, subject to:
  1. V(d) ≤ 0.30 × annual_runoff_volume        (do not starve downstream users / over-build)
  2. d ∈ [d_min, d_max]
       d_min = 2.5 m   survive Indian dry-season evaporation (1400–2600 mm/yr, IMD atlas)
       d_max = min( 4.5 m,                          practical excavation / machine reach
                    water_table_depth − 1.0 m,      ★ from CGWB pre-monsoon level (E9)
                    depth to hard rock )
     ── the water-table term is NOT optional in India: cutting into a shallow water
        table converts a storage pond into a seepage pit, and in Atal Bhujal blocks
        it can also be a regulatory problem. E9 supplies this per-district.
  3. cost(d) = V_excavation(d) × rate ≤ budget
  4. A_top ≤ available_parcel_area
  5. side slope stable for the soil type (1:1.5 clay, 1:2 sandy)
  6. freeboard 0.5 m above FSL; dead storage = 10 % for silt
  7. INDIAN DESIGN STANDARDS applied as presets and validity checks:
       IS 5477 (Pt. I–IV)  fixing the capacity of a reservoir (dead / live / flood storage)
       IS 4410, IS 7894    earthwork and small earth-dam terminology & practice
       CGWB "Master Plan for Artificial Recharge to Ground Water in India"
                           — design norms for percolation tanks and check dams
       MGNREGA Schedule-I  standard farm-pond geometries (e.g. 20×20×3 m, 30×30×3 m)
                           offered as one-click presets, since these are the shapes
                           that can actually be sanctioned and funded at village level

SOLVE      1-D constrained search over the stage–storage curve (SciPy minimize_scalar,
           bounded), evaluating a utility U(d) = reliable_storage(d) − γ·cost(d)

Deeper is preferred at equal volume: evaporation loss ∝ surface area, so a deep-narrow
pond retains water far longer than a shallow-wide one of the same capacity.
```

### Water balance (monthly, FR-13)

```
S(t) = min( S(t−1) + I(t) − E(t) − Sp(t),  Capacity )      spill the excess

I(t)  = SCS-CN runoff for month t
E(t)  = ET0(t) × k_pan (≈0.75) × A_water(S(t−1))     [open-water evaporation]
Sp(t) = seepage_rate(HSG) × A_wetted × days
        HSG-A ≈ 25 mm/day · HSG-B ≈ 15 · HSG-C ≈ 8 · HSG-D ≈ 4
Reliability = (# months with S > 0.2·Capacity) / 12 × 100
If HSG ∈ {A, B} and dry-out occurs before March → recommend lining (LDPE/bentonite)
```

### Ancillary design outputs
- **Spillway width** from the Rational-method peak discharge for a 25-year storm
  (broad-crested weir: `Q = C·L·H^1.5`, C ≈ 1.7, H = 0.3 m over the crest).
- **Cost:** `excavation_m3 × ₹/m³ + embankment + spillway + (lining if required)`.
- **Irrigation potential:** `live_storage_m3 / crop_water_requirement_m3_per_ha` → command area in ha.

## 6.8 Contour Generation (FR-2)

```
Path 1 (primary, vector-exact):
    gdal_contour -a elev -i {interval} filled_dem.tif contours.gpkg
    → Douglas–Peucker simplify per zoom level → encode as MVT tiles

Path 2 (OpenCV, as suggested by the assignment):
    for each level L in elevation range, step = interval:
        binary = (dem >= L).astype(uint8)
        cnts, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        smooth each contour with cv2.approxPolyDP(eps = 0.5·cellsize)
        transform pixel coords → world coords via the raster affine transform
    → also gives *filled elevation bands* (hypsometric tinting) for free

Index contours: every 5th line is drawn bolder and labelled.
```

## 6.9 Worked Numerical Example (end-to-end)

The single most useful thing to have memorised for a viva, and the reference the implementation is
tested against. Every number below is derived from the one above it — **this section is the arithmetic
authority for the whole document**, and the JSON examples in §5.3 are consistent with it.

**Given** — a village in Sehore district, MP; catchment delineated at the chosen pour point:

| Quantity | Value | Source |
|---|---|---|
| Catchment area `A` | 148.6 ha = 1,486,000 m² | D8 delineation (§6.3) |
| Longest flow path `L` | 2,140 m | flow-path trace |
| Relief `H` | 59.7 m | DEM max − outlet |
| 75 % dependable annual rainfall `P` | 921.5 mm | IMD + ERA5-Land ensemble (§4.2 B) |
| Composite CN(II) | 78.4 | area-weighted LULC × HSG (§6.6) |

### Step 1 — Time of concentration (Kirpich)

```
S_slope = H / L = 59.7 / 2140 = 0.0279
Tc = 0.01947 · L^0.77 · S_slope^(−0.385)
   = 0.01947 · 2140^0.77 · 0.0279^(−0.385)
   = 0.01947 · 366.5 · 3.968
   = 28.3 minutes
```

### Step 2 — Potential retention and initial abstraction

```
S  = 25400 / CN − 254 = 25400 / 78.4 − 254 = 324.0 − 254 = 69.98 ≈ 70.0 mm
Ia = 0.3 · S = 21.0 mm          ← 0.3, not 0.2: Indian practice (CWC/IMD), see CH-15
```

### Step 3 — Runoff for one storm (`P = 60 mm/day`)

```
P (60) > Ia (21.0)  ✓  so runoff occurs

     (P − Ia)²        (60 − 21.0)²        39.0²      1521
Q = ───────────── = ───────────────── = ────────── = ────── = 13.96 mm
    (P − Ia + S)     (60 − 21.0 + 70.0)    109.0      109.0

Runoff coefficient for this storm = 13.96 / 60 = 0.233
Volume = (13.96 / 1000) × 1,486,000 = 20,745 m³
```

### Step 4 — ★ The error this example exists to prevent

Apply the formula to the **annual total** instead of the daily series:

```
Q = (921.5 − 21.0)² / (921.5 − 21.0 + 70.0) = 900.5² / 970.5 = 835.6 mm
Runoff coefficient = 835.6 / 921.5 = 0.907        ✗ 91 % of all rainfall as runoff — absurd
```

Apply it **daily and then sum** (the correct method, §6.6 Step 5):

```
Σ Q_daily over the design year          = 361.8 mm
Runoff coefficient = 361.8 / 921.5      = 0.393   ✓ plausible for a rural Deccan catchment
Annual runoff volume = (361.8/1000) × 1,486,000 = 537,635 m³
```

> **The formula is convex, so it cannot be applied to aggregated rainfall.** This single mistake
> inflates runoff by 2.3× here, and it is the most common error in student implementations of
> SCS-CN. If your computed runoff coefficient exceeds ~0.6 for a rural catchment, this is the bug.

### Step 5 — Peak discharge (Rational method, for spillway sizing)

```
Q_peak = C · i · A / 3.6        [i in mm/h, A in km², Q in m³/s]
       = 0.393 × 55 × 1.486 / 3.6 = 8.92 m³/s
              (i = 55 mm/h, the 25-yr intensity for a 28.3 min duration)
```

### Step 6 — Pond geometry (`d = 3.5 m`, side slope 1V : 1.5H, top 60 × 45 m)

```
L_bottom = 60 − 2(1.5)(3.5) = 60 − 10.5 = 49.5 m
W_bottom = 45 − 2(1.5)(3.5) = 45 − 10.5 = 34.5 m
Closure check: min(60, 45) = 45 > 2zd = 10.5  ✓ geometry valid

A_top    = 60 × 45     = 2,700.00 m²
A_bottom = 49.5 × 34.5 = 1,707.75 m²
√(A_top · A_bottom)    = √4,610,925 = 2,147.31

Prismoidal formula:
V = (d/3)(A_top + A_bottom + √(A_top·A_bottom))
  = (3.5/3)(2700.00 + 1707.75 + 2147.31)
  = 1.16667 × 6555.06
  = 7,647.6 m³        ← gross capacity

Dead storage (10 % silt) = 764.8 m³
Live storage             = 6,882.8 m³
Excavation cost = 7,648 × ₹130 + 1,830 × ₹70 (bund) = ₹11,22,284
```

### Step 7 — Sanity checks, and what they reveal

| Check | Value | Reading |
|---|---|---|
| Runoff coefficient | 0.393 | ✓ plausible (0.15–0.45 for rural) |
| `V ≤ 0.30 × annual runoff`? | 7,648 ≤ 161,290 m³ | ✓ **not binding** — see below |
| Capacity ÷ annual inflow | **1.42 %** | ★ the catchment vastly out-yields the pond |
| Depth vs constraint | 3.5 m vs `d_max` 4.5 m | Limited by the **parcel**, not the terrain |

★ **The insight this example produces.** The pond captures only **1.42 %** of what its catchment
yields. The `V ≤ 0.30 × runoff` guard against over-harvesting (§6.7) is nowhere near binding — the
real constraint is the **2,700 m² available parcel**. That is an actionable planning finding, and the
system should surface it: *"this catchment can support a substantially larger structure, or several
ponds in series — the limit here is available land, not water."*

A tool that only printed "capacity = 7,648 m³" would hide that. Reporting **which constraint binds**
is what makes the output a planning recommendation rather than a calculator result.

## 6.10 Contour Map → DEM (FR-15, ADR-7)

The input for this phase is a **contour map, not a DEM**. Contours are the *output* of §6.8 and the
*input* here — so this section inverts it, producing the raster the rest of §6 already consumes.

### 6.10.1 Parsing: what varies between contour maps

KML is a container, not a schema, so the one thing that must never be assumed is *where the elevation
lives*. Real contour exports put it in at least four places, and a generalised parser tries each in
priority order and records which one it used:

| # | Elevation location | Example | Notes |
|---|---|---|---|
| 1 | Coordinate **z** ordinate | `81.2863,21.2635,277.0` | Cleanest when present; a true 3D LineString |
| 2 | `<ExtendedData><SimpleData name="…">` | `<SimpleData name="ELEV">277.0</SimpleData>` | Field name varies: `ELEV`, `elevation`, `level`, `CONTOUR`, `Z`… match case-insensitively against a candidate list |
| 3 | `<Placemark><name>` | `<name>277.0</name>` | **What the supplied sample uses.** Parse leniently: `277`, `277.0`, `277 m`, `277.0m`, `Contour 277` |
| 4 | Enclosing `<Folder><name>` | `contours_1.0m` | Last resort; also the natural place to *detect the interval* |

```
ContourParser.parse(file) -> ParsedContours
  1. sniff the container: KMZ (zip) -> extract doc.kml | KML -> use directly
  2. strip namespaces so kml/2.2, kml/2.1 and gx: variants all parse
  3. for each Placemark containing a LineString (or MultiGeometry of them):
         z = first successful strategy from the table above
         if none succeeds -> collect into `unresolved`, do not guess
  4. VALIDATE, and fail with a specific reason rather than a generic 400:
         - at least 2 distinct elevation levels, else there is no relief to analyse
         - at least ~20 usable lines, else interpolation is meaningless
         - coordinates within lon [-180,180], lat [-90,90]
         - unresolved fraction < 10 % of placemarks
  5. DERIVE, never assume (all reported back to the caller):
         interval   = mode of the sorted level differences
         extent     = union bbox of every vertex
         crs        = EPSG:4326 unless the KML declares otherwise
         utm_epsg   = utm_epsg_for(centroid_lon, centroid_lat)     # metric CRS
```

Deliberately ignored: `<Point>` label placemarks (the sample has 1355 of them, duplicating the line
elevations) and `<Style>`. The lone bounding `<Polygon>` is kept, if present, as an optional clip mask.

### 6.10.2 Interpolation to a regular grid

```
INPUT   contour LineStrings with elevations, in EPSG:4326
1. REPROJECT every vertex to the local UTM zone (ADR-5) -- interpolation and the
   resulting cell size must be in metres, never degrees.
2. DENSIFY each line: insert vertices so no segment exceeds ~ (cell_size / 2).
   Without this, long straight contour segments leave triangles that span two
   levels and the interpolated surface develops false terraces.
3. DERIVE the grid resolution from the data:
       spacing = median nearest-neighbour distance between contour vertices
       cell    = clamp(round_to_nice(spacing), 2 m, 30 m)     # default target
   Rationale: interpolating far finer than the contour spacing invents detail
   that the survey does not contain; far coarser throws the survey away.
   Overridable per request, and the value used is always reported.
4. INTERPOLATE the (x, y, z) cloud onto the grid:
       primary   scipy.interpolate.LinearNDInterpolator   (Delaunay / TIN)
       smoothing CloughTocher2DInterpolator for C1 continuity where slopes matter
       edges     NearestNDInterpolator to fill the strip outside the convex hull
5. CLIP to the contour convex hull (or the KML boundary polygon if supplied);
   everything beyond it is nodata, not extrapolation.
6. DE-TERRACE: light Gaussian blur (sigma ~ 0.75 cell). TIN interpolation between
   1 m contours produces stepped facets, and D8 on a stepped surface generates
   spurious parallel flow. This is the same conditioning as HLD 6.2 step 4.
OUTPUT  DEM as a COG in the local UTM CRS -> identical to what A1 produces,
        so sink filling, D8, flow accumulation and catchment delineation
        (6.2-6.3) run unchanged.
```

**Why linear TIN rather than IDW or kriging.** Contour vertices are *exact* known elevations lying on
lines, not scattered samples of an unknown field. A TIN honours them exactly and interpolates linearly
between adjacent contours, which is the same assumption a person reads a contour map with. IDW
produces bullseye artefacts at every vertex, and kriging's variogram assumptions are unjustifiable for
data whose spatial structure is *lines of constant value*. The known TIN weakness — flat triangles
where a contour bends back on itself, e.g. saddles and summits — is what step 6 mitigates.

### 6.10.3 Quality reporting

Interpolated terrain is a *model*, and the response says so. Every contour analysis returns:

| Field | Meaning |
|---|---|
| `elevation_source` | `"uploaded_contour_map"` — never silently mixed with a remote DEM |
| `contour_interval_m`, `levels`, `elevation_range_m` | Derived from the input, proving nothing was assumed |
| `elevation_strategy` | Which of the four strategies resolved the elevations |
| `grid_resolution_m` | Derived or caller-supplied |
| `interpolation_method`, `vertices_used`, `lines_parsed`, `lines_unresolved` |  Reproducibility |
| `hull_coverage_pct` | Fraction of the bbox actually inside the contour hull — low values warn that the catchment may extend beyond surveyed ground |
| `confidence` | `high` when relief ≥ 20 m and coverage ≥ 80 %; degraded otherwise, with the reason |

### 6.10.4 Location-derived enrichment — the contour map is not a closed world

A contour map carries no rainfall, soil or land-cover attribute. It does, however, carry something
sufficient: **its own position.** Once the contours are parsed, the AOI centroid and bounding box are
known, and every remaining layer the pond calculation needs is reachable from a coordinate **without a
credential**:

| Layer needed | Source keyed off the contour AOI | Auth |
|---|---|---|
| 30-year daily rainfall + ET₀ | Open-Meteo Archive ERA5-Land (§4.2 B2) | none |
| Independent rainfall cross-check | NASA POWER (§4.2 B3) | none |
| Soil texture → Hydrologic Soil Group | SoilGrids REST (§4.2 C4) | none |
| Land use / land cover | ESA WorldCover 10 m (§4.2 C2) | none |
| Existing water bodies, roads, buildings | Overpass API (§4.2 E6) | none |

So a contour upload yields the **complete** analysis — catchment, composite Curve Number, SCS-CN
runoff, pond depth and storage capacity — not a terrain-only subset. The uploaded survey supplies
*better* terrain than any free DEM (1 m contours vs 30 m cells); the location supplies everything else.

### 6.10.5 Pond siting and the graceful-degradation ladder (FR-16)

With the enrichment above, the full §6.5 criteria set applies unchanged. When a provider is
unreachable the analysis **degrades in defined steps** rather than failing — the `PARTIAL` state of
§3.7 — and the response states exactly which tier it reached:

| Tier | Criteria in play | `analysis_tier` | When |
|---|---|---|---|
| **1 — full** | terrain + soil (HSG) + LULC + rainfall → suitability, composite CN, SCS-CN runoff, pond sizing | `full` | all providers answered |
| 2 — hydrology | terrain + rainfall → catchment, runoff at a default CN, pond sizing with a stated CN assumption | `no_soil_lulc` | SoilGrids/WorldCover down |
| 3 — terrain | flow accumulation, depression depth, slope, plan concavity → site + catchment + stage–storage capacity | `terrain_only` | offline, or every provider down |

Tier 3 is a **floor, not the design point**. It still answers the graded question — pond location and
catchment area — from the contour map alone, which is why the endpoint works with no network at all.

```
score(cell) = w1*norm(flow_accumulation)      # receives runoff
            + w2*norm(depression_depth)       # natural bowl -> least excavation
            + w3*(1 - norm(slope))            # buildable, low earthwork
            + w4*norm(plan_concavity)         # converging ground
            + w5*norm(hsg_runoff_potential)   # tier 1 only  (clay > sand)
            + w6*norm(lulc_suitability)       # tier 1 only  (barren > cropland)
subject to hard masks: inside the contour hull, slope <= max_slope,
                       flow_accumulation >= min_cells,
                       not within buffer of an existing water body (tier 1)
then DBSCAN cluster -> representative cell of max score -> non-maximum suppression

Weights come from the same AHP vector as 6.5.2, renormalised over whichever
criteria are present -- so a tier drop changes the inputs, never the algorithm.
```

Every response carries `analysis_tier`, `layers_used[]` and `layers_unavailable[]`, so a number is
never mistaken for something it is not — but the default path is the complete one.

---

# 7. Expected Challenges and Proposed Solutions

## 7.1 Data Challenges

| # | Challenge | Impact | Proposed Solution |
|---|---|---|---|
| **CH-1** | **DEM resolution (30 m) vs pond size (20–60 m).** One SRTM pixel ≈ 900 m²; a small pond is 2–4 pixels. Vertical error is ±16 m LE90 (absolute) for SRTM. | Depth and storage estimates could be meaningless | Use **Copernicus GLO-30** (best free vertical accuracy, ~2 m LE90) rather than raw SRTM. Rely on **relative** elevation within the AOI (errors are spatially correlated, so relative differences are far more accurate than absolute heights). Apply void filling + Gaussian smoothing. Publish an explicit uncertainty band on every derived volume. Provide `POST /terrain/dem` with a `custom_dem_url` so a drone/DGPS survey can be dropped in for the final design stage. Validate against OpenTopoData spot heights. |
| **CH-2** | **Flat terrain breaks D8 flow routing** — parallel-flow artefacts, ambiguous directions. Many Indian villages sit on near-flat plains. | Wrong catchments, unusable in exactly the plains where ponds matter most | Detect the flat-cell fraction; if > 30 %, switch from *filling* to **depression breaching** (WhiteboxTools `BreachDepressionsLeastCost`) which preserves drainage lines, plus Garbrecht–Martz flat resolution. Offer **D∞** as an alternative. Warn in the response `quality.confidence = "low"` when terrain relief < 5 m. |
| **CH-3** | **No open API for government/revenue land ownership in most Indian states.** Records are digitised under DILRMP but exposed through fragmented state portals — Mahabhulekh/Bhu-Naksha (MH), Bhoomi (KA), Dharani (TS), Jamabandi (HR) — each with a different schema, most un-APIed, several CAPTCHA-protected. | FR-3 ("available land") is only half-solvable from open data | **Three tiers.** (a) Proxy the category from **Bhuvan's Wasteland thematic layer (C1)** — the ISRO product that maps precisely the class India allots for ponds — backed by ESA WorldCover *bare/sparse* and OSM `landuse`. (b) **FR-11 upload endpoint** for the Panchayat's own cadastral GeoJSON/Shapefile: the administrator already holds this, so authoritative data enters from the user rather than from a scrape. (c) On-map polygon drawing for known common land. All behind a `LandCategoryProvider` interface (§4.2 F) with a **state-parameterised category vocabulary** (CH-25), so a real state integration drops in later. Output is always labelled *"physically suitable — ownership to be verified against revenue records"*. |
| **CH-4** | **Reanalysis rainfall smooths the Indian monsoon.** ERA5 is 25 km, NASA POWER 0.5°; both under-estimate convective extremes, and neither is the record an Indian engineering review expects to see cited. | Runoff systematically biased, and the figure is not officially defensible | **IMD 0.25° gridded (B1) is the authoritative anchor**, not a cross-check — it is the gauge-based national record. ERA5-Land (11 km) and CHIRPS (5.5 km) supply spatial detail IMD's cell cannot; NASA POWER gives an independent third opinion. Report the **ensemble median with inter-source σ as an explicit uncertainty band**, bias-corrected against IMD. Size on the **75 % dependable** rainfall, not the mean — standard Indian minor-irrigation practice, conservative by construction. See CH-23 on what 27 km resolution really means for one village. |
| **CH-5** | **LULC misclassification** at 10 m; seasonal cropland/fallow confusion; cloud cover during the monsoon. | Wrong Curve Number → wrong runoff | Use **annual composites** (ESA WorldCover) rather than single-date scenes; cross-check with a second product (Esri/Dynamic World) and flag disagreement; use dry-season Sentinel-2 cloud-masked medians for water detection; allow the user to manually override a parcel's class in the UI (`PATCH` on the parcel). |
| **CH-6** | **External API rate limits & outages.** Materially reduced by the A1 decision: the *primary* DEM source is an open S3 bucket with **no key and no quota**, so the highest-volume dependency cannot be rate-limited at all. What remains: OpenTopoData 1000/day + 1 req/s, Nominatim 1 req/s absolute, Overpass fair-use. | A quota trip degrades enrichment, no longer the core pipeline | **Provider abstraction with an ordered fallback chain** (§4.2 F) + **multi-level caching**: Redis L1 (hot, TTL 1 h) → PostGIS/disk L2 (durable, DEM & rainfall effectively immutable) → cache key = SHA-256 of `(source, bbox_rounded, params)`. Client-side **request coalescing** (identical in-flight requests share one upstream call), token-bucket rate limiting per provider, exponential backoff with jitter, and a **circuit breaker** that trips a provider out after 5 consecutive failures. **Self-host OpenTopoData in Docker with local SRTM tiles for the demo** → unlimited, offline-capable. |
| **CH-7** | **Catchment truncated at the AOI boundary** — the real contributing area starts outside the village. | Under-estimated runoff, silently | Fetch the DEM with an adaptive buffer (start 500 m, scale with expected catchment size). After delineation, test whether the catchment polygon touches the DEM edge; if so, **auto-expand the buffer ×2 and recompute once**, and set `quality.touches_aoi_boundary = true` in the response if it still touches. |

## 7.2 Computational & Engineering Challenges

| # | Challenge | Impact | Proposed Solution |
|---|---|---|---|
| **CH-8** | **Long-running raster computation blocks HTTP.** Flow accumulation over a 25 km² AOI takes 15–60 s; gateways time out at 30 s. | Unusable UX, 504s | **ADR-2:** every heavy operation is a **Celery task**; the API returns `202 Accepted` immediately with a `job_id`. Progress is streamed over WebSocket and also pollable. Tasks are idempotent and keyed by a parameter hash, so a repeated request returns the cached artefact instantly. |
| **CH-9** | **Raster payload size.** A 25 km² DEM at 30 m is ~28 k pixels; as GeoJSON/JSON arrays it is tens of MB and freezes the browser. | Frontend crash | **ADR-3:** rasters are stored as **COG** and served as **XYZ PNG tiles by TiTiler** — the browser only ever fetches the 256×256 tiles it can see. Vectors go out as **MVT** with zoom-dependent Douglas–Peucker simplification. Contours below a zoom threshold are decimated (only index contours shown when zoomed out). |
| **CH-10** | **CRS errors — computing area in degrees.** A classic and silent bug: `shapely.area` on EPSG:4326 returns square degrees. | Every area, volume and cost number wrong, with no visible error | **ADR-5** enforced in code: a `CRSGuard` utility raises if a metric operation is attempted on a geographic CRS. Store in 4326, compute in auto-derived local UTM (`32600 + floor((lon+180)/6) + 1`). Unit tests assert a known polygon's area to ±0.5 %. All API fields carry explicit unit suffixes. |
| **CH-11** | **Memory blow-up — but only at high resolution.** At 30 m an AOI's rasters are < 1 MB (§9.1), so this is *not* a baseline risk; it becomes real when a 1 m drone/DGPS DEM is supplied (25 M cells, ~600 MB for six derivatives) or the AOI cap is raised. | Worker OOM in the high-resolution case | **Windowed reads** via `rasterio.windows` — never `read()` the whole band; process in 512×512 blocks. Use float32 not float64. Cap AOI at 100 km² with a clear error and a suggestion to split. Use Dask/`rioxarray` chunking for the largest cases. Set a per-task memory limit in Celery with graceful failure. |
| **CH-12** | **Pour-point snapping error.** The user clicks on a hillside, 40 m from the actual drainage line; D8 then returns a 2-cell catchment. | Catchment area off by orders of magnitude — the single most common failure mode in this class of tool | Always **snap to the maximum flow-accumulation cell** within `snap_radius_m`, return the `snapped_point` and `moved_m` so it is visible, draw both points on the map, and expose the radius as a UI slider. If `acc(p*) < min_accumulation_cells`, return a warning: *"this point receives negligible runoff"*. |
| **CH-13** | **Reproducibility** — the same request next month gives different numbers because an upstream dataset changed. | Fails NFR-13; report cannot be defended | Persist the **complete parameter set + source identifiers + dataset versions + timestamps** with every analysis row. Cached artefacts are content-addressed. Re-opening a saved project replays the stored numbers, never a fresh fetch, unless "refresh" is explicitly requested. |
| **CH-14** | **File-upload security** (FR-11 shapefile/GeoJSON) — zip bombs, path traversal in `.zip`, malicious geometry. | RCE / DoS | 50 MB cap, MIME sniffing, extract to a temp dir with sanitised names (reject `../`), open via **Fiona in a subprocess with a timeout**, validate the geometry with Shapely (`make_valid`), reject > 10 000 features, never invoke a shell. |

## 7.3 Modelling & Domain Challenges

| # | Challenge | Impact | Proposed Solution |
|---|---|---|---|
| **CH-15** | **SCS-CN is an empirical US model** calibrated on American watersheds; it under-performs for Indian monsoon regimes. | Systematic runoff bias | Use **Ia = 0.3 S** (CWC/IMD Indian practice) rather than 0.2 S; apply **AMC** adjustment from the antecedent 5-day rainfall; run the model on **daily** rainfall then aggregate (never on annual totals); **cross-check against Strange's table** (an Indian empirical method) and the Rational method, and surface the spread. Provide a sensitivity analysis (CN ±10) in the report so the reader sees the range. |
| **CH-16** | **No ground-truth labels for "good pond site"** — supervised learning has nothing to learn from. | The "AI" claim is hollow | **Weak supervision (§6.5.4):** existing tanks/ponds from OSM + NDWI-detected persistent water bodies become positives; terrain-stratified random points become negatives. Validate with **spatial block cross-validation** (plain k-fold leaks through spatial autocorrelation and gives a falsely high AUC). If ROC-AUC < 0.75, fall back to α = 1.0 (pure AHP) — the system degrades to a transparent, defensible MCDA rather than a bad model. |
| **CH-17** | **Black-box output is unusable for public spending.** An engineer must be able to justify the site in a file note. | Adoption failure | **Explainability is a first-class feature, not an add-on:** per-criterion contribution table, SHAP waterfall, every input layer visible on the map, all assumptions listed in the API response, and an auto-generated PDF that states the methodology and the uncertainty. AHP weights are user-editable with a consistency check. |
| **CH-18** | **Over-harvesting / downstream harm.** Ponds upstream can starve existing tanks and users downstream. | Real-world harm; an "optimal" recommendation that is socially wrong | Constrain capacity to **≤ 30 % of the catchment's annual yield**. Enforce a minimum inter-pond spacing (NMS at 300 m). Detect existing downstream water bodies and flag `downstream_impact` when the new pond intercepts > 20 % of their catchment. State this limitation explicitly in the report. |
| **CH-19** | **The pond dries before the dry season ends** — evaporation (~1800 mm/yr in central India) and seepage are large. | The structure "works" on paper and fails in reality | Explicit **monthly water balance** (§6.7) with ET₀ from Open-Meteo and HSG-based seepage. Enforce `depth ≥ 2.5 m` (surface area, not volume, drives evaporation, so deep-and-narrow beats shallow-and-wide). Auto-recommend **lining** when HSG ∈ {A, B}. Report `months_water_available` and `reliability_pct` as headline numbers. |
| **CH-20** | **Validation** — how do we know any of this is right? | No credibility at the demo | Multi-pronged, and **all four references are Indian**: (a) compare delineated catchments against the **SLUSI Watershed Atlas of India** 1:50,000 delineation (E10) and SoI toposheets for 5 test villages, target ±15 %; (b) run the tool on **10 existing MGNREGA / Amrit Sarovar ponds (E4, E5)** and check they score in the top suitability quartile — a strong, cheap, quantitative validation using structures that demonstrably work; (c) compare DEM-derived capacity against the recorded dimensions of those same ponds; (d) golden-file unit tests on synthetic DEMs (a cone, a V-valley) where the true catchment is analytically known. |

## 7.4 India-Specific Data & Integration Challenges

These arise **only** because the system targets India, and none of them are solved by a global data source.

| # | Challenge | Impact | Proposed Solution |
|---|---|---|---|
| **CH-21** | **ISRO portals need accounts, and Bhuvan access tokens expire daily.** Bhoonidhi API access is granted per-account on request; Bhuvan WMS/API tokens are short-lived. | The India-native layers (C1 wasteland, D2 imagery, E4 MGNREGA assets) silently 401 mid-demo | A `BhuvanTokenService` that refreshes on a schedule and on 401, holds the token in Redis with a safety margin before expiry, and serialises refreshes so concurrent workers don't stampede. **Every ISRO layer has a declared global fallback** (§4.2 F), so an expired token degrades the map rather than breaking the analysis. Register for Bhuvan + Bhoonidhi accounts in Week 1 — lead time is a schedule risk, not a technical one |
| **CH-22** | **India-WRIS / NWIC has no documented public REST API.** The portal exposes dashboards and bulk download; the JSON endpoints behind them are undocumented and unversioned. | Building on them would be brittle and could break without notice | **Do not treat India-WRIS as a live dependency.** Pre-harvest the rainfall and CGWB groundwater subsets we need into PostGIS as a one-time seed (`BULK`/`MANUAL` access mode), refreshed manually per release. Prefer `data.gov.in` OGD resources (B5, E9) where the same data is exposed through a documented, key-authenticated API |
| **CH-23** | **IMD's authoritative grid is 0.25° (~27 km).** A village occupies roughly a thousandth of one cell, so **every village in a cell receives an identical rainfall figure** — the "village-specific" rainfall is an illusion at that resolution. | Over-claiming precision; two villages 20 km apart with genuinely different rainfall get the same number | Be explicit rather than pretend: report IMD as the **authoritative district-scale** figure and ERA5-Land (11 km) / CHIRPS (5.5 km) as the **spatial-detail** figures, then present the ensemble median with the inter-source σ as a stated uncertainty band. The API response carries `grid_resolution_km` and `n_villages_in_cell` so the UI can say *"this rainfall figure is shared with 340 nearby villages"*. Design on the **75 % dependable** value, which is conservative by construction |
| **CH-24** | **Indian village-name transliteration is highly variable** — *Rampur / Rampura / Ram Pur / Rāmpur*, plus Devanagari vs Latin script and district-level duplicates (hundreds of villages named Rampur). | Users cannot find their own village; wrong village silently analysed | **LGD code is the canonical key**, never the name. Search uses PostgreSQL `pg_trgm` fuzzy matching + `unaccent` over the LGD/SHRUG name table, always disambiguated by *Block, District, State* in the result list. Support Devanagari input. Show the LGD code on the confirmation card so the user can verify against their own records |
| **CH-25** | **"Government land" is not one category in India.** It is *gairan* / *gochar* (MH), *shamlat* (PB/HR), *poramboke* (TN), *banjar* / *charagah* elsewhere — different names, different allotment rules, different custodians, all state-specific. | A single hard-coded "government land" class would be wrong in most states | Model land category as a **state-parameterised vocabulary** rather than a fixed enum: a per-state YAML maps local revenue categories → an internal `{allottable, common, protected, private}` taxonomy. The uploaded cadastral layer (FR-11) is tagged with its state so the right mapping applies. Where no mapping exists, output falls back to "physically suitable, category unverified" |
| **CH-26** | **OSM rural water-body coverage in India is sparse and geographically biased** toward peri-urban and well-surveyed districts. | Using OSM as the ML positive-label source would train the model on "places OSM mappers visit", not "places ponds work" | Invert the label priority (§6.5.4): **MGNREGA geotagged assets (E4) and Amrit Sarovar (E5) first** — ground-truthed government records of structures actually built — then NDWI-detected persistent water bodies, and OSM **last**. Check label spatial coverage per district before training and exclude districts with too few positives rather than training on a biased sample |

## 7.5 Project-Management Challenges

| # | Challenge | Solution |
|---|---|---|
| **CH-27** | 4-week window (10 Aug → 5 Sep) for a large scope | MoSCoW-prioritised backlog (§3.1); weekly vertical slices, each ending in a demoable increment; the ML layer and FR-12/13/14 are explicitly *Could*, so the Must set always ships |
| **CH-28** | Heavy GDAL/PROJ/GEOS install pain across machines | Ship a **Docker image** built from `ghcr.io/osgeo/gdal:ubuntu-small`; `docker compose up` is the whole installation guide; pin every version in `requirements.lock` |
| **CH-29** | External APIs may be down/unreachable during the lab demo | **Pre-seed the cache** for 3 demo villages; run a self-hosted OpenTopoData container; ship a `DEMO_MODE=true` flag that serves fixtures — the demo can run fully offline |
| **CH-30** | Tests hitting rate-limited external APIs in CI | Record real responses once with **VCR.py**, replay in CI; no network calls in the test suite |
| **CH-31** | **ISRO/LGD/data.gov.in account approvals have lead time** | Apply for Bhuvan, Bhoonidhi, OpenTopography and data.gov.in keys on **day 1 of Week 1**; the global fallback chain means the build is never blocked on an approval |

---

# 8. Data Model (PostGIS)

```
┌──────────────┐        ┌──────────────┐        ┌──────────────────┐
│    users     │1      *│   projects   │1      *│    analyses      │
│ id (PK)      ├────────┤ id (PK)      ├────────┤ id (PK)          │
│ email        │        │ user_id (FK) │        │ project_id (FK)  │
│ role         │        │ village_id   │        │ status           │
│ password_hash│        │ name         │        │ params    JSONB  │
└──────────────┘        │ created_at   │        │ result    JSONB  │
                        └──────┬───────┘        │ sources   JSONB  │
                               │                │ started_at       │
                        ┌──────▼───────┐        │ finished_at      │
                        │   villages   │        └────────┬─────────┘
                        │ id (PK)      │                 │
                        │ lgd_code  UQ │        ┌────────┴─────────────┐
                        │ name         │        │                      │
                        │ district     │   ┌────▼──────────┐  ┌────────▼────────┐
                        │ state        │   │  catchments   │  │ candidate_sites │
                        │ geom POLYGON │   │ id (PK)       │  │ id (PK)         │
                        │ centroid PT  │   │ analysis_id   │  │ analysis_id     │
                        │ GIST(geom)   │   │ pour_point PT │  │ geom POINT/POLY │
                        └──────────────┘   │ snapped_pt PT │  │ rank            │
                                           │ geom POLYGON  │  │ score           │
   ┌──────────────┐    ┌──────────────┐    │ area_ha       │  │ criteria JSONB  │
   │  dem_assets  │    │rainfall_cache│    │ metrics JSONB │  │ shap     JSONB  │
   │ id (PK)      │    │ id (PK)      │    │ GIST(geom)    │  └────────┬────────┘
   │ bbox POLYGON │    │ cell_lat     │    └───────┬───────┘           │
   │ source       │    │ cell_lon     │            │          ┌────────▼────────┐
   │ resolution_m │    │ source       │            │          │  pond_designs   │
   │ crs          │    │ date         │      ┌─────▼──────┐   │ id (PK)         │
   │ file_path    │    │ precip_mm    │      │  runoffs   │   │ site_id (FK)    │
   │ checksum     │    │ UQ(lat,lon,  │      │ id (PK)    │   │ depth_m         │
   │ created_at   │    │    src,date) │      │catchment_id│   │ top_area_m2     │
   │ GIST(bbox)   │    └──────────────┘      │ cn         │   │ capacity_m3     │
   └──────────────┘                          │ volume_m3  │   │ excavation_m3   │
                                             │ detail JSON│   │ cost_inr        │
   ┌──────────────┐    ┌──────────────┐      └────────────┘   │ geom POLYGON    │
   │ land_parcels │    │   reports    │                       │ balance JSONB   │
   │ id (PK)      │    │ id (PK)      │                       └─────────────────┘
   │ village_id   │    │ analysis_id  │
   │ geom POLYGON │    │ format       │      ┌──────────────────┐
   │ ownership    │    │ file_path    │      │ provider_call_log│  (observability)
   │ source       │    │ created_at   │      │ provider, status │
   │ uploaded_by  │    └──────────────┘      │ latency_ms, ts   │
   └──────────────┘                          └──────────────────┘
```

**Key indexes:** `GIST` on every geometry column; `BTREE(cell_lat, cell_lon, source, date)` on
`rainfall_cache`; `BTREE(status, created_at)` on `analyses`; `GIST(bbox)` on `dem_assets` for
"do we already have a DEM covering this AOI?" lookups.

---

# 9. Deployment Architecture

```
                        ┌─────────────────┐
                        │     Browser     │
                        └────────┬────────┘
                                 │ :443
                    ┌────────────▼─────────────┐
                    │   nginx (reverse proxy)  │
                    │   TLS, static, gzip,     │
                    │   /api → api, /tiles → titiler │
                    └──┬────────┬─────────┬────┘
        ┌──────────────┘        │         └──────────────┐
   ┌────▼──────┐         ┌──────▼──────┐          ┌──────▼──────┐
   │ frontend  │         │  api        │          │  titiler    │
   │ (static)  │         │  FastAPI    │          │  COG tiles  │
   └───────────┘         │  gunicorn   │          └──────┬──────┘
                         └──┬───────┬──┘                 │
                            │       │                    │
                  ┌─────────▼──┐ ┌──▼────────┐    ┌──────▼───────┐
                  │  redis     │ │ postgis   │    │  minio /     │
                  │ broker+    │ │ pg16      │    │  volume      │
                  │ cache      │ │           │    │  (COG store) │
                  └─────┬──────┘ └───────────┘    └──────▲───────┘
                        │                                │
                ┌───────▼─────────┐                      │
                │ celery-worker   │──────────────────────┘
                │ (×N, geo stack) │
                └───────┬─────────┘
                ┌───────▼─────────┐   ┌──────────────────┐
                │ celery-beat     │   │ opentopodata     │
                │ (cache warming) │   │ (self-hosted DEM)│
                └─────────────────┘   └──────────────────┘

  9 services in one docker-compose.yml  →  `docker compose up` = the installation guide
```

## 9.1 Resource Sizing

Sized for a 25 km² village AOI at 30 m. **Get the scale right first, because it is counter-intuitive:**

| AOI | Resolution | Raster | All 6 derivatives (float32) |
|---|---|---|---|
| 25 km² | 30 m | 167 × 167 = **28 k cells** | **0.7 MB** |
| 100 km² | 30 m | 333 × 333 = 111 k cells | 2.7 MB |
| 100 km² | 10 m (LULC) | 1000 × 1000 = 1 M cells | 24 MB |
| 25 km² | **1 m** (drone/DGPS) | 5000 × 5000 = **25 M cells** | **600 MB** |

At the 30 m resolutions this system actually uses, **the rasters are trivially small** — under a
megabyte. Worker memory is dominated by the Python + GDAL process baseline, not by the data. Raster
memory only becomes the binding constraint if a high-resolution DEM is supplied (the 1 m row), which
is exactly the case CH-11's windowed reads exist for.

| Service | CPU | RAM | Disk | Notes |
|---|---|---|---|---|
| `nginx` | 0.1 | 64 MB | — | TLS, static, gzip |
| `frontend` | — | — | 50 MB | Static build, served by nginx |
| `api` (FastAPI) | 0.5 | 512 MB | — | Async IO-bound; 2 gunicorn/uvicorn workers |
| `celery-worker` | 0.5–1 per worker | **~600 MB per worker** (GDAL/NumPy baseline; data is < 1 MB) | — | Mostly **waiting on the network**, not computing. Scale horizontally for throughput, and use concurrent fetches *within* a task for latency |
| `postgis` | 0.5 | 1 GB | 2 GB + seed | `shared_buffers=256MB`; GIST indexes |
| `redis` | 0.2 | 256 MB | — | `maxmemory-policy allkeys-lru` for L1 |
| `titiler` | 0.5 | 512 MB | — | Windowed COG reads, so memory stays flat |
| `opentopodata` (optional) | 0.2 | 256 MB | **~15 GB** | Only if self-hosting SRTM tiles for offline demo |
| **Total (dev, 1 worker)** | **~2 cores** | **~3.5 GB** | **~10 GB** | Comfortable on a 8 GB laptop; workable on 4 GB |
| **Total (demo, 2 workers)** | ~3 cores | ~4.5 GB | ~25 GB | With the local OpenTopoData DEM container |

**What actually consumes the memory:** PostGIS (`shared_buffers`), the GDAL/NumPy process baseline in
each worker, and Redis — *not* the elevation data. float32 over float64 is still the right default,
and windowed reads still matter, but both are insurance against a high-resolution DEM being supplied
later (CH-11), not a requirement of the 30 m baseline.

**Two scaling levers, and they are different:**
- **Throughput** — add Celery workers. Analyses are independent, so this scales linearly until PostGIS
  write contention appears, well beyond the 10-concurrent target in NFR-4.
- **Latency** — fetch independent providers **concurrently**. Rainfall, soil, LULC and OSM have no
  dependency on one another, so `asyncio.gather` collapses four sequential waits into one. This is the
  single biggest available win on cold-run time and it costs no extra hardware.

---

# 10. Testing & Quality Strategy

| Level | What | Tooling |
|---|---|---|
| Unit | SCS-CN maths, prismoidal volume, AHP consistency ratio, CRS conversions, CN lookup | pytest, hypothesis (property tests: volume must be monotonic in depth) |
| Golden-file | D8 flow direction & watershed on **four synthetic surfaces with exact expected cell counts** — tilted plane, inverted cone (bowl), V-valley, and twin bowls split by a ridge. An *upright* cone is used only as a negative case, since radial divergence gives its flank catchments no closed form | pytest + NumPy fixtures |
| Integration | Service ↔ PostGIS ↔ Redis; full pipeline on a small fixture AOI | pytest + testcontainers |
| Contract | Every endpoint validated against the OpenAPI schema | schemathesis |
| External | Recorded real API responses, replayed offline | VCR.py / responses |
| E2E | Search → analyse → select → export | Playwright |
| Performance | 25 km² AOI < 90 s; 10 concurrent analyses | locust |
| Domain validation | 5 reference catchments ±15 %; 10 existing ponds must rank in the top quartile | custom validation notebook |

---

# 11. Project Plan (10 August → 5 September)

> ⚠ **SUPERSEDED.** This calendar plan no longer applies — the project is **not deadline-bound**. It is
> retained as the original design-time schedule. The plan actually being executed is
> **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**, sequenced by **dependency and effort rather
> than dates**: 12 phases totalling ~244 h, split into **Ring 1** (M0–M7, ~129 h — all eight mandatory
> FRs plus every deliverable, i.e. submittable) and **Ring 2** (M8–M11, ~115 h — the ISRO Tier-1
> sources of §4.2, the ML suitability layer of §6.5.4, the full 72-endpoint surface of §5.2, and the
> complete quality bar). Integration risk is front-loaded into a walking skeleton in M1.


| Week | Dates | Deliverable | Functional Requirements |
|---|---|---|---|
| **W0** | 9–10 Aug | **HLD submission** (this document) | — |
| **W1** | 11–17 Aug | Repo scaffold, Docker Compose, PostGIS schema, village search + boundary, satellite basemap, DEM acquisition + caching, contour generation & rendering | **FR-1, FR-2** |
| **W2** | 18–24 Aug | Sink filling, D8 flow direction/accumulation, stream network, pour-point snapping, catchment delineation; rainfall adapters + statistics. **Prototype demo in lab hours** | **FR-4, FR-5** |
| **W3** | 25–31 Aug | LULC/soil integration, exclusion masking + parcel extraction, AHP suitability, RF model + DBSCAN ranking, SCS-CN runoff, stage–storage & pond design | **FR-3, FR-6, FR-7, FR-9** |
| **W4** | 1–5 Sep | Full overlay UI, charts, PDF report, GIS exports, water balance, testing to ≥70 % coverage, installation guide, API docs, technical report, **final demo** | **FR-8, FR-10, FR-13**, all deliverables |

**Risk buffer (as originally planned):** FR-12 and FR-14 were *Could*, to be dropped first if W3 slipped.
With no deadline this is moot — **every FR, including FR-10 to FR-14, is in scope** (Ring 2). The
executed build order is IMPLEMENTATION_PLAN.md §9.

---

# 12. Mapping to the Evaluation Rubric

| Criterion | Marks | How this design addresses it |
|---|---|---|
| System functionality | 35 | All 8 mandatory FRs designed end-to-end with concrete algorithms, plus 6 optional FRs; 72 documented endpoints; **India-first data strategy (§4.2, Appendix B)** so every FR is met with a source that actually covers the target country |
| Terrain and catchment analysis | 20 | §6.2–6.4: Priority-Flood conditioning, D8/D∞ flow routing, accumulation, pour-point snapping, watershed delineation, morphometrics (Tc, drainage density, form factor), TWI/TPI/depression depth; validated against the **SLUSI Watershed Atlas of India**; Indian regional runoff methods (Strange / Inglis-DeSouza / Khosla / Barlow) cross-check the SCS-CN result |
| Frontend and visualization | 5 | MapLibre vector tiles, 8 toggleable layers, stage–storage & rainfall charts, hypsometric tinting, progressive layer loading |
| Software design and code quality | 15 | Layered architecture with 6 ADRs, adapter pattern for every provider, DI, typed Pydantic contracts, 70 % test coverage, ruff/mypy/CI |
| System design and management | 15 | Async job architecture, multi-level caching, circuit breakers, fallback chains, Dockerised 9-service deployment, observability, reproducibility guarantees |
| Documentation and report | 10 | Auto-generated OpenAPI, MkDocs, installation guide via Compose, this HLD, final technical report |

## 12.1 Traceability Matrix

Every requirement traced to the objective it serves, the endpoint that exposes it, the algorithm that
computes it, the test that proves it, and the build phase that delivers it. **A requirement with no
test row is a requirement nobody can claim is done.**

| FR | Obj. | Primary endpoint | Algorithm (§) | Verifying test | Phase |
|---|---|---|---|---|---|
| **FR-1** Satellite imagery | SO-1 | `GET /villages/{id}/imagery`<br>`GET /villages/search` | Geocode + `pg_trgm` fuzzy match | Search returns the seeded village; tile URL returns valid PNG | P2 |
| **FR-2** Contour maps | SO-2 | `POST /terrain/contours`<br>`POST /terrain/derivatives` | §6.8 marching squares / `gdal_contour`; §6.2 Horn slope | Contours non-empty, elevations monotonic across adjacent lines, no self-intersection | P2 |
| **FR-3** Land available for excavation | SO-3 | `POST /land/available` | §6.4 exclusion masking + OpenCV morphology + connected components | Parcel areas sum ≤ AOI; every parcel slope ≤ threshold; excluded classes absent | P5 |
| **FR-4** Catchment area ★ | SO-4 | `POST /hydrology/catchment` | §6.3 D8 flow dir → accumulation → upstream traversal; Kirpich Tc | **Golden test: synthetic cone within 5 % of analytic**; V-valley outlet correct; real catchment ±15 % vs SLUSI | P1/P3 |
| **FR-5** Historical rainfall | SO-5 | `GET /rainfall/statistics` | §4.2 B ensemble median; Weibull `m/(N+1)` dependability; Gumbel EV-I | 75 % dependable < mean < max; 30 years returned; ensemble σ reported | P4 |
| **FR-6** Runoff volume ★ | SO-6 | `POST /runoff/estimate` | §6.6 SCS-CN with Ia = 0.3S, AMC adjustment, daily→annual; Strange cross-check | **§6.9 worked example reproduced to 3 s.f.**; `Q = 0` when `P ≤ Ia`; composite CN within component range | P4 |
| **FR-7** Depth + capacity ★ | SO-7 | `POST /pond/design`<br>`POST /pond/stage-storage` | §6.7 DEM stage–storage flood-fill; prismoidal formula; bounded depth optimiser | `V(d)` strictly increasing; prismoidal matches §6.9 hand calc; side-slope closure rejected when `min(L,W) ≤ 2zd` | P5 |
| **FR-8** Overlay all results | SO-8 | `POST /analysis` + `/status` + `/result` | §3.7 job state machine; Celery task chain | Job reaches `DONE`; result round-trips through PostGIS; all layers render | P6 |
| **FR-9** Ranked sites | SO-9 | `GET /suitability/{id}/sites` | §6.5 AHP eigenvector + CR check, weighted overlay, DBSCAN, NMS | Consistent matrix → `CR < 0.1`; inconsistent matrix **rejected**; weights sum to 1.0 | P6 |
| **FR-10** Save / export | SO-8 | `POST /reports/generate` | Jinja2 + WeasyPrint | PDF opens, contains real maps and non-placeholder numbers | P7 |
| **FR-11** Cadastral upload | SO-3 | `POST /land/parcels/upload` | §2.6 sandboxed Fiona parse | Zip-bomb and `../` traversal both rejected | *deferred* |
| **FR-12/13/14** | — | — | §6.7 water balance; LLM narration | — | *deferred* |
| — Reusable API | SO-10 | `GET /openapi.json`, `/docs` | FastAPI + Pydantic | Schemathesis contract test against the schema | P7 |

**Cross-cutting requirements**

| NFR | Verified by |
|---|---|
| NFR-2/3 performance | `locust` run: 25 km² AOI < 90 s cold, catchment < 20 s warm |
| NFR-5 graceful degradation | Provider forced to fail → job reaches `PARTIAL` with `warnings[]`, not `FAILED` |
| NFR-6 accuracy | ±15 % catchment comparison against SLUSI for 5 villages |
| NFR-9 security | Upload fuzzing; `pip-audit` in CI; secret-scan on the git history |
| NFR-10 maintainability | Coverage gate in CI; `mypy --strict` on `services/` |
| NFR-13 reproducibility | Re-run a stored analysis → byte-identical numeric result |
| NFR-14 accessibility | axe-core audit clean; full keyboard traversal; contrast ≥ 4.5:1 |

---

# 13. Assumptions and Limitations

**Assumptions**
1. **Scope is India.** Every Tier-1 source in §4.2 is either India-native or verified for full Indian coverage (8°N–37°N, 68°E–97°E). Porting to another country would require replacing the Tier-1 layer, not the architecture.
2. The **AWS Open Data** buckets (Copernicus DEM GLO-30, Mapzen terrain tiles) remain publicly readable without credentials — this is the system's only hard external dependency, and it requires no account. Free APIs (Open-Meteo, NASA POWER, Overpass, SoilGrids) remain available under their current terms; ISRO portals (Bhuvan, Bhoonidhi) continue to grant free accounts for non-commercial use.
   **No API key is required to run the mandatory pipeline.** Every credential in `.env` is optional and gates an enrichment, not a core requirement.
3. 30 m DEM (COP30 / CartoDEM) is adequate for *screening* candidate sites; a detailed survey precedes construction.
4. IMD's 0.25° gridded record is the accepted authority for rainfall in Indian minor-irrigation design, with reanalysis products used for spatial detail only.
5. SHRUG / Census-2011 village geometries are approximately correct and LGD codes are stable enough to serve as the canonical key.
6. Bhuvan's *wasteland* class is a reasonable proxy for allottable land pending verification against state revenue records.
7. Catchments of interest are ≤ 50 km² (small watersheds — where SCS-CN is valid).
8. The user has internet access on first analysis; results are cached thereafter.

**Stated limitations (to be repeated in the final report)**
1. **Land-ownership data is not authoritative** — output is "physically suitable", ownership must be verified against revenue records.
2. **Not a substitute for a detailed survey** — recommendations are for screening and prioritisation.
3. **No groundwater modelling** — recharge benefit is not quantified in v1.
4. **No sediment-yield modelling** — desilting frequency is a rule-of-thumb, not a computed value.
5. **Socio-economic and legal factors** (encroachment, disputes, community consent) are outside the model.

---

# 14. References

1. USDA NRCS, *Urban Hydrology for Small Watersheds*, TR-55, 1986.
2. O'Callaghan, J.F. & Mark, D.M., "The extraction of drainage networks from digital elevation data", *CVGIP*, 1984.
3. Tarboton, D.G., "A new method for the determination of flow directions and upslope areas", *Water Resources Research*, 1997.
4. Wang, L. & Liu, H., "An efficient method for identifying and filling surface depressions", *IJGIS*, 2006.
5. Saaty, T.L., *The Analytic Hierarchy Process*, McGraw-Hill, 1980.
6. Beven, K.J. & Kirkby, M.J., "A physically based variable contributing area model", *Hydrological Sciences Bulletin*, 1979.
7. FAO, *Water Harvesting: A Manual for the Design and Construction of Water Harvesting Schemes*, 1991.
8. Integrated Mission for Sustainable Development (IMSD) Technical Guidelines, NRSA/ISRO, 1995.
9. Central Water Commission (India), *Guidelines for Design of Minor Irrigation Tanks*.
10. Horn, B.K.P., "Hill shading and the reflectance map", *Proc. IEEE*, 1981.
11. Lundberg, S. & Lee, S., "A Unified Approach to Interpreting Model Predictions" (SHAP), NeurIPS, 2017.
12. McFeeters, S.K., "The use of NDWI in the delineation of open water features", *IJRS*, 1996.

**Indian standards, methods and data sources**

13. Bureau of Indian Standards, **IS 5477 (Parts I–IV)** — *Methods for Fixing the Capacity of Reservoirs* (dead, live and flood storage).
14. Bureau of Indian Standards, **IS 4410** (terminology) and **IS 7894** — *Code of Practice for Stability Analysis of Earth Dams*.
15. Central Ground Water Board, **Master Plan for Artificial Recharge to Ground Water in India** — design norms for percolation tanks, farm ponds and check dams.
16. Ministry of Agriculture, Government of India, **Handbook of Hydrology** — Curve Number values for Indian land use / soil complexes.
17. Strange, W.L., *Runoff tables for Deccan catchments* (1928); **Inglis, C.C. & DeSouza, A.** (1929), *Runoff formulae for Western India*; **Barlow** (1912), UP catchments; **Khosla, A.N.** (1960), *Appraisal of water resources*.
18. **IMD Pune**, 0.25° × 0.25° daily gridded rainfall dataset (1901–present); *Evaporation Atlas of India*.
19. Saswata Nandi & co., **IMDLIB** — *An open-source library for retrieval and processing of gridded IMD datasets over India*, Environmental Modelling & Software, 2023.
20. **NRSC / ISRO** — Bhuvan thematic services (LULC 1:50k, Wasteland Atlas), Bhuvan Panchayat 3.0, Bhuvan MGNREGA geotagged assets, and the **Bhoonidhi** STAC Open Data API.
21. **Development Data Lab**, *SHRUG — Socioeconomic High-resolution Rural-Urban Geographic Platform for India* (open village/town polygons, Census 2011).
22. **SLUSI**, *Watershed Atlas of India*, 1:50,000 — watershed delineation and coding.
23. Ministry of Jal Shakti — **Mission Amrit Sarovar** guidelines; **Atal Bhujal Yojana**; **PMKSY Watershed Development Component** operational guidelines.
24. Ministry of Rural Development — **MGNREGA Schedule-I** water-conservation works and standard farm-pond designs.

---

## Appendix A — Priority Guide for the Handwritten Copy

If the lab copy has limited space, write these in full and keep the rest as a summary table:

**Write in full — in this priority order if space runs short:**

| # | Section | Why it earns its space |
|---|---|---|
| 1 | **§6.9 Worked numerical example** | Highest value per page in the whole document. Proves you can *do* the method, not just name it, and it is what an examiner will probe |
| 2 | §1.1–1.2 problem + objectives | Frames everything else |
| 3 | §2.3 simplified block diagram | The one diagram to draw if you draw only one |
| 4 | §3.1 FR table | Direct answer to "functional requirements" |
| 5 | §3.3 workflow data-flow diagram | Answers "project workflow" |
| 6 | §6.3, §6.6, §6.7 core algorithms | Catchment, runoff, pond sizing — the 20-mark subsystem |
| 7 | §4.2 A, B, C, E India-first source tables | Answers "APIs" and shows the design targets India |
| 8 | §4.1 tech stack table | Direct answer to "proposed technology stack" |
| 9 | §5.2 endpoint catalogue (★ rows at minimum) | Answers "API design" |
| 10 | §7.1–7.4 challenges, incl. §7.4 India-specific | Answers "expected challenges and solutions" |
| 11 | §6.5 suitability engine (AHP) | The "AI" in the project title |
| 12 | **Appendix D notation** | Half a page; makes every formula you wrote legible |

**Summarise or reference, don't transcribe:** §2.2 (draw §2.3 instead) · §2.5, §2.6 (caching, security —
one line each) · §3.6 wireframe (**do sketch it**, it is quick and covers the frontend requirement) ·
§3.7 job states · §5.3 (write **one** JSON contract, e.g. runoff) · §4.2 D, F, G · §8, §9, §10, §12.1, §14

**Keep as a reference sheet, not in the copy:** Appendix C glossary — use it to revise before the viva.

---

## Appendix B — India-Native Sources at a Glance

A one-page summary of *why this is an India system and not a generic one*. Worth writing out, because
it is the fastest way to show the design is grounded in the country it targets.

| Data need | India-native Tier-1 source | Why a global source is not enough |
|---|---|---|
| **Rainfall** | **IMD 0.25° gridded daily** (via `imdlib`), 1901→present | Gauge-based national record; reanalysis is a model, and IMD is what an Indian engineering review expects cited |
| **Elevation** | **CartoDEM 30 m** via Bhoonidhi STAC API (cross-check on COP30) | Survey-of-India-aligned national DEM; validates the foreign DEM we compute on |
| **Land category** | **Bhuvan Wasteland + LULC 1:50k** (NRSC/ISRO) | "Wasteland" is the *administrative class India allots pond land from* — **no global LULC product has this class at all** |
| **Village boundary** | **SHRUG** (~600k Census-2011 villages) + **LGD codes** | Global geocoders have Indian village *names* but usually not polygons; LGD is the code officials actually use |
| **Existing structures** | **Bhuvan MGNREGA geotagged assets** + **Amrit Sarovar** (~70k ponds) | Ground-truthed government records of ponds that were built and are maintained — our ML labels and our validation set. OSM is sparse and biased in rural India |
| **Groundwater / water table** | **CGWB** — 22,965 observation wells, 4×/year since 1969 | Sets the hard `d_max ≤ water_table − 1 m` limit on excavation depth; no global equivalent |
| **Runoff cross-check** | **Strange / Inglis-DeSouza / Khosla / Barlow** | Derived from *Indian* gauged catchments; SCS-CN is a US model (see CH-15) |
| **Design norms** | **IS 5477**, CGWB recharge master plan, **MGNREGA Schedule-I** farm-pond geometries | These are the dimensions that can actually be sanctioned and funded at village level |
| **Catchment validation** | **SLUSI Watershed Atlas of India** 1:50,000 | Independent Indian delineation to check our D8 output against |
| **Soil** | NBSS&LUP soil map of India (cross-check on SoilGrids) | India-native soil series backing the Hydrologic Soil Group assignment |

**Global sources retained** — and each verified for full Indian coverage (8°N–37°N, 68°E–97°E) —
because they are genuinely better on resolution or accessibility: **COP30** (best free vertical accuracy),
**ESA WorldCover 10 m** (finest LULC), **ERA5-Land 11 km / CHIRPS 5.5 km** (finer than IMD's 27 km),
**SoilGrids** (only programmatic soil API), **Sentinel-2** (NDWI bands), **Esri World Imagery** (sub-metre basemap).

---

## Appendix C — Glossary

Terms used throughout this document. Worth knowing cold for the viva — an examiner will probe the ones
marked ★.

**Geospatial**

| Term | Meaning |
|---|---|
| **AOI** | Area of Interest — the bounding box or polygon an analysis is run over |
| ★ **DEM** | Digital Elevation Model — a raster where each cell holds a ground elevation |
| **COP30 / SRTM / CartoDEM / NASADEM** | Specific global/national DEM products; COP30 = Copernicus 30 m, CartoDEM = ISRO's Indian DEM from Cartosat-1 |
| ★ **COG** | Cloud-Optimized GeoTIFF — internally tiled raster allowing byte-range reads of a window without downloading the whole file |
| **MVT** | Mapbox Vector Tile — binary vector tile format, used for contours and catchment outlines |
| **XYZ / WMTS / WMS** | Map tile protocols. XYZ = `{z}/{x}/{y}` slippy tiles; WMS = OGC map service |
| **STAC** | SpatioTemporal Asset Catalog — a JSON standard for searching satellite imagery catalogues |
| ★ **CRS / EPSG** | Coordinate Reference System, identified by an EPSG code. 4326 = WGS 84 lat/lon (degrees); 32643 = UTM 43N (metres) |
| **UTM** | Universal Transverse Mercator — metric projected CRS in 6° longitude zones; India spans 42N–47N |
| **Zonal statistics** | Summarising raster values within polygon zones (e.g. LULC area inside a catchment) |
| **LULC** | Land Use / Land Cover classification |
| **NDWI / NDVI** | Normalised Difference Water / Vegetation Index — band ratios from satellite imagery |
| **Hillshade** | Simulated shaded relief from a DEM, for visual terrain interpretation |
| **Douglas–Peucker** | Polyline simplification algorithm, used to generalise contours per zoom level |

**Hydrology**

| Term | Meaning |
|---|---|
| ★ **Catchment / watershed** | The upstream area whose surface runoff drains to a given point |
| ★ **Pour point / outlet** | The point whose catchment is being delineated |
| ★ **D8** | Flow-direction algorithm assigning each cell one of 8 neighbours by steepest descent |
| **D∞ (D-infinity)** | Flow direction allowing proportional splitting between two neighbours |
| ★ **Flow accumulation** | Count of upstream cells draining through each cell — high values mark drainage lines |
| ★ **Sink / depression filling** | Raising interior low points so flow routing does not dead-end |
| **Depression breaching** | Alternative to filling — carves an outlet channel instead, better on flat terrain |
| **Strahler order** | Stream-hierarchy numbering; headwaters = 1, order increases where equal streams meet |
| ★ **Runoff coefficient** | Fraction of rainfall that becomes surface runoff (`Q/P`) |
| ★ **SCS-CN** | Soil Conservation Service Curve Number method for estimating runoff depth from rainfall |
| ★ **CN (Curve Number)** | 30–100 index of runoff potential from land cover + soil; higher = more runoff |
| ★ **HSG** | Hydrologic Soil Group A/B/C/D — A infiltrates freely, D barely at all |
| ★ **AMC** | Antecedent Moisture Condition I/II/III (dry/average/wet) — adjusts CN for prior 5-day rainfall |
| ★ **Ia — initial abstraction** | Rainfall lost to interception, depression storage and infiltration before runoff begins |
| **S — potential maximum retention** | Storage capacity of the soil profile in the SCS-CN model, in mm |
| ★ **Tc — time of concentration** | Time for runoff to travel from the hydraulically most distant point to the outlet |
| ★ **Dependable rainfall (75 %)** | Rainfall equalled or exceeded in 75 % of years — the standard Indian design value |
| **Return period** | Average interval between events of a given magnitude (e.g. 25-year storm) |
| **TWI** | Topographic Wetness Index `ln(α/tan β)` — propensity of a location to accumulate water |
| **TPI** | Topographic Position Index — whether a cell sits in a valley, on a slope, or on a ridge |
| ★ **Stage–storage curve** | Water level ↔ surface area ↔ stored volume relationship for a reservoir |
| **FSL** | Full Supply Level — the design water surface elevation |
| **Freeboard** | Vertical margin between FSL and the top of the bund |
| **Dead storage** | Volume reserved for silt accumulation, not usable |
| **Live storage** | Usable volume above dead storage |
| **Spillway / waste weir** | Structure passing flood flows safely once the pond is full |
| **Prismoidal formula** | Volume of a truncated pyramid: `(d/3)(A_t + A_b + √(A_t·A_b))` |
| **Percolation tank** | Structure built to recharge groundwater rather than store surface water |

**Method & platform**

| Term | Meaning |
|---|---|
| ★ **AHP** | Analytic Hierarchy Process — derives criterion weights from pairwise expert comparisons |
| ★ **CR — Consistency Ratio** | AHP self-check; `CR ≥ 0.1` means the expert's judgements contradict each other |
| **MCDA / WLC** | Multi-Criteria Decision Analysis / Weighted Linear Combination |
| **DBSCAN** | Density-based clustering, used to turn high-suitability pixels into discrete sites |
| **NMS** | Non-Maximum Suppression — discards a candidate too close to a better-ranked one |
| ★ **SHAP** | Per-prediction feature-attribution method used for ML explainability |
| **Spatial block CV** | Cross-validation with contiguous spatial folds, avoiding autocorrelation leakage |
| **Weak supervision** | Deriving training labels from proxy sources rather than manual annotation |
| **ADR** | Architecture Decision Record — a documented, dated design decision with its rationale |
| **Circuit breaker** | Pattern that stops calling a repeatedly failing provider for a cooldown period |
| **Request coalescing** | Serving many identical concurrent requests from one upstream call |
| **Idempotency** | Property whereby repeating a request causes no additional effect |
| **RFC 7807** | Standard JSON "problem details" error format |
| **LGD** | Local Government Directory — Government of India's canonical administrative codes |
| **MGNREGA** | Mahatma Gandhi National Rural Employment Guarantee Act — funds most village water works |
| **Gairan / gochar / shamlat / poramboke** | State-specific revenue categories for common/government land |

---

## Appendix D — Notation

Symbols as used in §6 and §6.9.

| Symbol | Quantity | Unit |
|---|---|---|
| `P` | Rainfall depth (daily, monthly or annual as stated) | mm |
| `Q` | Runoff depth | mm |
| `S` | Potential maximum retention | mm |
| `Ia` | Initial abstraction | mm |
| `λ` | Initial abstraction ratio, `Ia = λS`; **0.3 in this design** | — |
| `CN` | Curve Number (subscript I / II / III = AMC class) | — |
| `A` | Catchment area (`A_ha`, `A_km2`, or m² as suffixed) | ha / km² / m² |
| `C` | Runoff coefficient, `Q/P` | — |
| `Q_peak` | Peak discharge (Rational method) | m³/s |
| `i` | Rainfall intensity | mm/h |
| `Tc` | Time of concentration | min |
| `L` | Longest flow path length | m |
| `H` | Relief (elevation range) | m |
| `S_slope` | Average slope along the flow path, `H/L` | m/m |
| `z` | Side slope, horizontal per 1 vertical (1V : zH) | — |
| `d` | Pond depth below ground level | m |
| `A_t`, `A_b` | Pond top and bottom areas | m² |
| `V` | Volume (capacity or excavation as stated) | m³ |
| `α` | Specific catchment area per unit contour width (TWI); **also** the AHP/ML fusion weight in §6.5.4 | m² / m ; — |
| `β` | Local slope angle (TWI) | degrees |
| `w_i` | AHP weight of criterion `i`, `Σw_i = 1` | — |
| `f_i(·)` | Fuzzy normalisation function for criterion `i`, → [0,1] | — |
| `S_ahp`, `S_ml`, `S` | Suitability score: AHP, ML, and fused | [0,1] |
| `acc(c)` | Flow accumulation at cell `c` | cells |
| `k` | Seepage / hydraulic conductivity | mm/day or m/s |
| `ET₀` | Reference evapotranspiration (FAO-56) | mm/day |
| `m`, `N` | Rank and sample size in the Weibull plotting position `m/(N+1)` | — |

> **Unit discipline.** `Q` is a **depth** in mm; volume requires `Q/1000 × A_m²`. Mixing mm with m, or
> ha with m², is the most common source of results that are wrong by exactly 10, 100 or 1000 — which
> is why every API field carries an explicit unit suffix (§5.1) and every metric computation runs in
> projected UTM, never in degrees (ADR-5, CH-10).
