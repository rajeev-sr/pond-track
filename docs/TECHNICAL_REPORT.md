# Contour — AI-Based Village Pond Planning System

**Technical Report**

A system that takes a contour map of an Indian village and returns where to build
a water-harvesting pond, how large it can be, how much runoff will reach it, and
which constraint limits it — with every figure traceable to a named source and
every limitation stated.

Built for CSD Assignment 1. Runs locally under Docker Compose; nothing is
deployed. **Every data source is open and keyless** — there is no API key to
obtain, and no credential field in the configuration.

---

## Contents

1. [Introduction and problem statement](#1-introduction-and-problem-statement)
2. [System architecture](#2-system-architecture)
3. [Methodology](#3-methodology)
4. [Implementation](#4-implementation)
5. [Results for a case-study village](#5-results-for-a-case-study-village)
6. [Validation](#6-validation)
7. [Scope and limitations](#7-scope-and-limitations)
8. [Future work](#8-future-work)
9. [References](#9-references)

---

## 1. Introduction and problem statement

India's rural water security depends heavily on small surface storage: village
ponds, farm ponds, check dams and percolation tanks. Under MGNREGA and the
Watershed Development Component of PMKSY, tens of thousands are sanctioned every
year. The siting decision is usually made from local knowledge and a site visit.

Local knowledge is genuinely good at some of this — a farmer knows where water
stands after rain — and genuinely bad at the rest. Three questions are hard to
answer by eye:

* **How much land drains to this point?** Catchment boundaries follow ridgelines
  that are invisible at ground level on gentle terrain.
* **How much water will actually arrive?** That needs decades of rainfall, the
  soil's infiltration behaviour, and the land cover — none of it observable on
  the day of the visit.
* **What limits the pond?** A pond can be limited by available land, by practical
  excavation depth, or by the water available. The three imply completely
  different responses, and confusing them wastes the intervention.

### 1.1 What this system does

Given a contour map — the artefact a village survey actually produces — it:

1. reconstructs a terrain surface from the contour lines;
2. routes flow over it and delineates the catchment above any point;
3. fetches ~30 years of daily rainfall, soil and land cover for that location;
4. estimates runoff by SCS-CN, cross-checked against Indian empirical formulae;
5. scores candidate sites against nine weighted criteria;
6. sizes a pond at each and reports **which constraint binds**;
7. renders the result as map layers, a PDF report and a GeoJSON bundle.

### 1.2 Why a contour map is the input

Most published work in this area starts from a satellite DEM. This system does
too when one is available, but its primary input is a **contour map supplied by
the user**, for a practical reason: a village-level survey sheet at a 0.5–1 m
contour interval is more accurate than any freely available DEM. Copernicus
GLO-30 is 30 m horizontally with metre-scale vertical error — on terrain with
16 m of relief across 2.8 km, that error is a large fraction of the signal the
model depends on.

Accepting the survey sheet directly also means the tool fits how the work is
actually organised: the contour map already exists, and it belongs to the people
making the decision.

### 1.3 Non-goals

Structural drawings of bund and spillway; legal land-title verification;
real-time flood forecasting; groundwater modelling; procurement. These are
excluded deliberately, not by omission — see §7.

---

## 2. System architecture

### 2.1 Shape

```
  Browser (React 18 + TypeScript + MapLibre GL)
      │  single origin: nginx serves the page and proxies /api
      ▼
  nginx ──────────────────────────────► FastAPI (Python 3.12)
                                          │
            ┌─────────────────────────────┼──────────────────────────┐
            ▼                             ▼                          ▼
      services/                     providers/                  PostGIS + Redis
   (pure computation:            (one adapter per external      (village index,
    hydrology, siting,            source, all keyless)           job progress,
    pond, runoff, land)                                          rainfall cache)
```

Three layers, and the boundary between the middle two is the one that carries
weight:

* **`services/`** is pure computation over NumPy arrays and plain dicts. It knows
  nothing about HTTP, and nothing about which web service supplied a raster. This
  is what makes the numerical core testable without a network: 896 of the 1,160
  tests run with no network and no database.
* **`providers/`** are adapters with one contract — return typed data or raise
  `ProviderUnavailableError`. Nothing above them knows which service answered, so
  a source can be swapped or a fallback inserted without touching the maths.
* **`api/`** is thin: validate, delegate, translate absence into RFC 7807 problem
  details.

### 2.2 Degradation as a first-class design

Every external source is optional, and the system reports which tier of answer it
produced:

| Tier | Available | What is still true |
|---|---|---|
| `full` | terrain + soil + land cover + rainfall | everything measured |
| `no_soil_lulc` | terrain + rainfall | runoff uses an assumed soil group |
| `terrain_only` | terrain alone | catchment and pond geometry unaffected |

This is not a fallback bolted on late. It is the reason the tool works at all in
practice: SoilGrids latency is erratic, and public Overpass returned HTTP 504,
502, 429 and a clean 2.3-second answer for the *same query* within one afternoon
of testing. A system that fails when one provider is slow is a demo.

The same principle drives the job state machine's `PARTIAL` state (§4.4): core
steps succeeded, an optional enrichment did not, the result is served with
warnings naming what was lost.

### 2.3 What changed during the build

Four architectural decisions were revised against evidence, and the reasons are
more instructive than the decisions:

**Two thresholds, not one.** `services/siting.py` rejects slope above 8 %;
`services/land.py` rejects above 5 %. This looked like an inconsistency and is
not: siting asks "could a pond work here", FR-3 asks "is this parcel worth
surveying", and steep ground is ruled out by excavation cost long before physics.
The HLD fixes 5 % for land availability specifically.

**OSM subtracts, never adds.** Open-Street-Map features are used only to *remove*
land from the available set. A building absent from OSM is not evidence of open
ground, so a village with thin coverage gets an optimistic answer rather than a
wrong one, and the response says which it was.

**The reach definition was wrong and the data said so.** Stream reaches were
initially cut at every junction. On the sample catchment that produced 16
fourth-order reaches against 10 third-order — a bifurcation ratio of **0.62**,
which is physically impossible: a higher order cannot be more numerous than the
order feeding it. A reach is a *Strahler stream*: it runs from where it attains
its order down to where it loses it.

Corrected, the whole sheet gives 210 / 50 / 11 / 2 reaches of orders 1–4, so
bifurcation ratios of **4.20 / 4.55 / 5.50** — within the 3–5 band Horton's law
leads one to expect, with the top ratio high on a sample of two. Restricted to
the recommended site's catchment it is 44 / 13 / 2 / 1, ratios 3.38 / 6.50 / 2.00;
those are noisier because the denominators are 13, 2 and 1. Extent has to be
stated with any such figure, which is why the API reports the area analysed
alongside the counts.

**The stage–storage curve now ends where terrain stops containing water.** It
used to continue past that point, and the curve *fell* as depth rose — 79,473 m³
at 2.00 m against 45,336 m³ at 4.25 m, impossible for a stage–storage curve. A
capped flood fill sums whichever cells the traversal popped first and those
subsets are not nested across levels. There is no honest number past containment,
so the curve stops rather than carrying one.

---

## 3. Methodology

### 3.1 Contour lines to a terrain surface

The input is a set of 3-D polylines at known elevations. The surface is built by
Delaunay triangulation of the contour vertices and linear interpolation within
each triangle, sampled onto a regular metric grid. Cell size is derived from mean
contour spacing unless given.

Two details matter:

* **The working CRS is projected and metric** (UTM zone from the data's own
  longitude — EPSG:32642–32647 for India). Every slope, area and volume is
  computed in metres. Only the API boundary converts to EPSG:4326.
* **Elevation is read from wherever the KML put it** — the placemark `<name>`,
  the coordinate *z*, or `ExtendedData`. Real survey exports use all three, and a
  parser that handles one of them rejects most real files.

### 3.2 Hydrological conditioning

Flow routing requires that every cell have a downhill neighbour. Interpolated
surfaces have spurious pits, so the DEM is conditioned first, by one of two
methods chosen from the terrain:

* **Priority-Flood with an ε gradient** (Barnes et al. 2014) fills depressions
  and imposes a minimal slope across flats.
* **Bounded least-cost breaching** (Lindsay 2016) carves an outlet instead,
  preferred when the flat fraction exceeds 15 % — filling a large flat plain
  raises its whole surface and destroys the drainage pattern.

Which was used, and how many cells were altered, is reported: a catchment
delineated over a heavily filled surface deserves less confidence, and six months
later nobody remembers which it was.

### 3.3 Flow routing and catchment delineation

D8 single-flow-direction with ESRI's encoding (1/2/4/8/16/32/64/128); flow
accumulation by Kahn's topological ordering, which is O(n) and avoids the
recursion depth a naive traversal hits on a 650×527 grid. A catchment is the
upstream set of a pour point, found by reverse traversal.

**Pour-point snapping** moves a clicked point onto the drainage line within a
search radius. This matters more than it looks: a point one cell off the channel
can report a few hectares where the channel reports a few hundred. The snap
distance is always reported, and the search mask is circular — it was square
initially, which reported a 175 m move under a 150 m radius.

### 3.4 Rainfall

Two independent reanalyses, never blended: **Open-Meteo ERA5-Land** (0.1°) and
**NASA POWER MERRA-2** (0.5°×0.625°). Both are fetched, the spread between them
is reported as uncertainty, and one complete daily series is used for SCS-CN —
because a blended daily series has no physical meaning even where blended monthly
totals would.

Derived: annual mean and CV, 50/75/90 % dependable rainfall by Weibull plotting
position, monthly normals, and a **monsoon window found from the normals** — the
four consecutive months carrying the largest share — rather than assumed to be
June–September, which is wrong for the north-east monsoon regions.

### 3.5 Runoff

SCS-CN on the **daily** series, with:

* composite CN from the (land cover × hydrologic soil group) area table;
* AMC adjustment per day from the preceding five days' rainfall;
* **initial abstraction Ia = 0.3S**, which is CWC and IMD practice for India
  rather than the 0.2S of the original American method. The difference is roughly
  a fifth of the annual total, so it is not a detail.

Cross-checked against four Indian empirical methods — Inglis–DeSouza (1929),
Khosla (1960), Barlow (1912) and the Rational method — selected by region. The
cross-check is reported, not averaged in: agreement raises confidence, and
disagreement is information.

Strange's (1928) tables are deliberately **not** implemented. It is a tabulation
rather than a formula, and inventing values would lend false confidence to a
number a reader would reasonably trust.

### 3.6 Suitability: AHP

Nine criteria, weighted by the Analytic Hierarchy Process, with the weight set
derived from IMSD (NRSA/ISRO) practice rather than invented:

| Criterion | Weight | Criterion | Weight |
|---|---|---|---|
| Flow accumulation | 0.200 | Plan concavity | 0.076 |
| Slope | 0.171 | Distance to stream | 0.076 |
| Depression depth | 0.133 | Distance to settlement | 0.057 |
| Soil runoff potential | 0.124 | Distance to waterbody | 0.048 |
| Land availability | 0.114 | | |

Criteria are normalised over the *feasible* set, not the whole raster. Feasible
cells already sit in the extreme tail of each criterion relative to the full
surface, so normalising globally clips them all to 1.0 and every candidate ties
at 100/100 — destroying the ranking.

Hard constraints are applied as a mask afterwards, not as a penalty: no terrain
argument makes a lake or a settlement buildable.

**The weights are auditable, and auditing them found a defect.** The pairwise
comparison matrix they imply, snapped to the Saaty scale an expert would actually
have used, gives λ_max = 9.106, CI = 0.0132, **CR = 0.0091** against Saaty's 0.10
threshold — so the judgements are internally coherent. The same audit showed the
elicited table summed to **1.05**, not 1.00. The exported vector is normalised,
which is ratio-preserving and changes no score or ranking, but the effective
weight on flow accumulation is 0.200 rather than the 0.21 that was tabulated.
`GET /api/v1/suitability/weights` returns the whole audit, and
`POST /api/v1/suitability/weights/ahp` refuses an inconsistent matrix with
HTTP 400 rather than dressing a contradiction up as a recommendation.

Candidate extraction is DBSCAN clustering of high-scoring cells, a representative
point per cluster, then non-maximum suppression at 300 m so two recommendations
cannot describe the same structure.

### 3.7 Pond design

Stage–storage–area by flood fill from the site cell; prismoidal volume for a
truncated pyramid, `V = (d/3)(A_top + A_bottom + √(A_top·A_bottom))`, with a
closure constraint — at side slope *z* a depth *d* removes 2*zd* from each plan
dimension, so a deep pond needs a wide top or the geometry cannot close.

Depth is chosen by bounded search over d ∈ [2.5, 4.5] m subject to
V ≤ 0.30 × annual runoff, with freeboard, silt dead storage and a spillway width.

**The output that matters is which constraint binds.** Depth, dimensions and cost
are a calculator result; *which limit stopped the design growing* is the planning
recommendation, and it is the difference between "build a 4.5 m pond" and
"acquire more land, because the parcel is what is limiting you".

### 3.8 Available land (FR-3)

Exclusion mask — OSM buildings ⊕ 50 m, roads ⊕ 20 m, existing water ⊕ 100 m (so a
new tank does not duplicate an old one), land cover that rules the ground out, and
slope above 5 % — intersected with an inclusion mask of bare, sparse, grass and
shrub cover. Cropland is opt-in, being usually private.

Then OpenCV morphological opening (remove speckle) followed by closing (fill the
pinholes opening leaves), connected-component labelling, and vectorisation with
per-parcel attributes. The response reports **which rule removed what**, because a
parcel count alone invites the assumption that terrain was the constraint.

---

## 4. Implementation

### 4.1 Repository

```
backend/app/
  api/v1/       9 routers — 27 operations
  services/     24 modules — the numerical core, no I/O
  providers/    16 adapters — elevation, rainfall, soil, land cover, vector
  db/           SQLAlchemy 2.0 + GeoAlchemy2 models; 7 Alembic migrations
  templates/    Jinja2 report
  tests/        1,160 tests: unit, property, golden, integration, e2e
frontend/src/   React 18 + TypeScript + MapLibre GL — 3,955 lines
docs/           HLD, implementation plan, API reference, install guide, this
```

16,891 lines of Python outside tests, 11,381 lines of tests, 3,955 of TypeScript.

### 4.2 Libraries and why

| Concern | Choice | Reason |
|---|---|---|
| Raster I/O | rasterio + GDAL | windowed COG reads avoid loading whole rasters |
| Arrays | NumPy, SciPy | Delaunay, EDT, filters |
| Vector | Shapely 2.0, pyproj | geometry and CRS |
| Morphology | OpenCV | `connectedComponentsWithStats` is where it earns its place |
| AHP | NumPy `linalg.eig` | principal eigenvector directly |
| API | FastAPI + Pydantic v2 | auto OpenAPI is a graded deliverable |
| Map | MapLibre GL | GPU vector tiles; 1,355 contour lines render smoothly |
| PDF | Jinja2 + WeasyPrint | the report is HTML/CSS, so its layout is inspectable |
| Figures | matplotlib | vector output, no browser needed |

Contextily was planned for basemap tiles in the PDF and dropped: it fetches from a
web service, and a report generator that fails offline is worse than one drawn on
white.

### 4.3 Provider fallbacks

Elevation tries the uploaded contour map, then Copernicus GLO-30 via `/vsicurl`,
then alternates. Rainfall queries both reanalyses. Overpass rotates over global
mirrors — and this is where a subtle failure lived: a regional mirror
(`overpass.osm.ch`, Switzerland-only) answered a Durg query with **HTTP 200 and
zero elements**, reporting a town as having no buildings, roads or water at all.
An exclusion layer failing *open* is the worst possible direction. Only global
mirrors are used now, and a `remark` field with no elements is treated as failure,
because Overpass reports query errors at HTTP 200.

### 4.4 Asynchronous analysis

A cold analysis takes ~24 s, 20 of them in the provider fetch. `POST /analysis`
returns `202` with a job id; `GET /analysis/{id}/status` reports the state machine
of HLD §3.7 with per-step outcomes.

**Progress is weighted by measured cost, not step count.** Enrichment is 20.0 s of
a 24.3 s run — 82 % of it — so an evenly-weighted bar reaches 57 % and then does
not move for twenty seconds, which reads as a hang. The step weights are the
measured fractions, and the UI draws each stage at its true width so the reader
can see the wait coming.

Work runs on a Celery worker when one answers a ping and in-process otherwise.
That fallback is deliberate: accepting a job into a queue nothing is draining
would leave the client polling `queued` for ever, which is the one failure mode an
async API must not have. The response says which path was taken.

### 4.5 Caching

Two caches, of deliberately different kinds:

* **Rainfall** in PostgreSQL, keyed `(source, grid cell, date)` — because
  individual day-tuples are reusable across analyses. A unique constraint makes it
  a cache rather than an append log; without it a re-fetch double-counts rainfall
  and inflates every runoff figure derived from it.
* **OSM windows** on disk, content-addressed by rounded bbox — because Overpass
  answers a bbox and there is no partial reuse to be had. Measured effect:
  18.2 s → 0.62 s, identical output.

---

## 5. Results for a case-study village

**Sisra / Kutelabhata area, Durg district, Chhattisgarh.** Input:
`contours_1m.kml`, 6.7 MB.

### 5.1 What was read

| | |
|---|---|
| Contour lines parsed | 1,355 (159,113 vertices) |
| Levels | 32 at 1.0 m interval, 267.0–298.0 m |
| Relief | 31.0 m |
| Elevation held in | placemark `<name>` |
| Working CRS | EPSG:32644 (derived) |
| Interpolated grid | 650 × 527 at 5 m = 342,550 cells |

### 5.2 Recommended site

| | |
|---|---|
| Site kind | channel position, score 81.3/100 |
| Location | 21.25114 N, 81.29545 E |
| Catchment | 180.25 ha (1.80 km²) |
| Relief / longest flow path | 16.0 m / 2,836 m |
| Time of concentration (Kirpich) | 65 min |
| Pond | 4.5 m deep, 141 × 141 m |
| Gross capacity | 81,682 m³ |
| Indicative cost | ₹1,20,48,099 at ₹130/m³ |
| **Binding constraint** | **practical excavation depth** |

### 5.3 Water

| | |
|---|---|
| Mean annual rainfall | 1,282.4 mm (1996–2025, NASA POWER) |
| 75 % dependable | 1,069.4 mm |
| Coefficient of variation | 0.199 |
| Monsoon | Jun–Sep, 86.3 % of annual |
| Soil | clay, HSG D |
| Composite CN (AMC II) | 87.7 |
| Annual runoff | 530,632 m³, C = 0.230 |
| Design yield (75 % dependable) | 326,365 m³ |
| Pond captures | 25.0 % of design-year yield |

### 5.4 Drainage and land

Drainage network within the recommended site's catchment: **60 channels,
12.235 km** above a 1 ha threshold, highest Strahler order 4, drainage density
**6.788 km/km²**. Over the whole surveyed sheet the same threshold gives 273
reaches and 50.2 km. Density is quoted for the catchment rather than the sheet
because density over an arbitrary rectangle is a property of the rectangle.

Available land (FR-3): **132 parcels, 62.9 ha** with OSM exclusions applied
(3 buildings, 190 roads, 47 tracks, 11 water bodies found). Without OSM the same
sheet gives 190 parcels and 82.6 ha, so the exclusion layer removes about 24 % of
the nominally buildable ground. Slope at the 5 % threshold removes 49 % of cells
and land cover a further 26 % — the terrain, not the OSM features, is what
dominates.

### 5.5 Sites rejected, and why that matters

Bhilai and Durg town were screened by `scripts/screen_sites.py` and **rejected
as urban**: both exceed the 40 % built-up ceiling the screen applies. They are in
the candidate list *deliberately*, as negative test cases — a siting model that
cannot decline a location is not making a judgement, and a screen that has never
been observed rejecting anything has not been tested.

(Exact built-up percentages are not quoted here. They depend on the AOI window
the screen uses, and re-measuring them with a different window gives different
figures — so the threshold and the outcome are reported, which are the parts that
are stable.)

No coordinate was invented for Kutelabhata when none could be sourced. The
village has no polygon in any keyless dataset, and the seeder reports it as
unlocated rather than guessing.

### 5.6 Deliverable artefacts

* **PDF report** — 4 pages of A4: map figure, catchment and rainfall tables, the
  per-criterion score breakdown, data sources with licences, and a limitations
  section. 814 kB.
* **GeoJSON export** — 1,371 features across 5 labelled layers (survey extent,
  contours, candidate sites, catchments, pond footprints), ready for QGIS.
* **Browser UI** — 11 toggleable layers over satellite imagery, live progress,
  rainfall and stage–storage charts.

---

## 6. Validation

Validation here means: does the computation do what the method says, and do the
numbers survive a check that could fail?

### 6.1 The HLD worked example, reproduced in code

HLD §6.9 works a pond design by hand: 60 × 45 m top, 3.5 m deep, side slope
1V:1.5H → bottom 49.5 × 34.5 m, gross capacity **7,647.6 m³**. A unit test
asserts the implementation reproduces it to 0.1 m³. Any change to the prismoidal
geometry fails that test.

### 6.2 Golden tests against analytic surfaces (89 tests)

Terrain with a known answer, so the assertion is on the mathematics rather than
on a recorded output:

* **`test_hydrology_analytic.py` (39)** — D8 directions on a tilted plane,
  accumulation on a converging valley, catchment area on a cone where the true
  area is computable.
* **`test_streams_analytic.py` (21)** — Strahler ordering on a constructed binary
  network, and Horton's laws of stream numbers and lengths.
* **`test_contour_round_trip.py` (14)** — the strongest single check, below.
* **`test_source_interchangeability.py` (15)** — the *same* terrain with its
  elevation stored four different ways (placemark name, coordinate z,
  ExtendedData, KMZ) must give the same catchment. This is what proves the parser
  generalises rather than fitting the sample file.

### 6.3 Contours → DEM → contours

The interpolation is checked by regenerating contours from the surface it built
and comparing them to the input lines. On `contours_1m.kml` at a 1 m interval:
**median vertical residual 0.109 m**, 95th percentile 0.371 m, maximum
1.211 m, over 1,472 sampled points.

The residual is not zero and should not be: the grid samples at cell centres
rather than on the line, so half a cell of horizontal offset on a 1-in-15 slope is
a few centimetres of vertical difference. A residual near zero would suggest the
test was comparing something to itself.

### 6.4 Property tests

Invariants asserted over generated inputs with Hypothesis: catchment area is
monotonic in pour-point position downstream; stage–storage volume is
non-decreasing in depth on every surface tried, including the flat and gently
sloping cases that exposed the capped-fill bug in §2.3; unit conversions round-trip.

One process note: a `pytest.skip` inside a Hypothesis test aborts the whole
property run, so an early version reported "1 passed, 1 skipped" while testing
nothing. It now uses `assume` with twelve start candidates, and the Hypothesis
statistics confirm real examples are generated.

### 6.5 Schema and documentation drift

Two tests exist because both artefacts rot silently:

* **Schema drift** compares the SQLAlchemy models to the migrated database with
  `alembic.autogenerate.compare_metadata`. All 7 migrations round-trip
  base ↔ head with no drift, and the village seed reproduces exactly (19,715
  villages, 7,595 admin areas, 11,532 panchayats).
* **API documentation drift** compares the live OpenAPI schema to `docs/API.md`.
  It was written after finding that **9 of 24 routes had gone undocumented** with
  nothing failing, and it immediately caught the next two.

### 6.6 Browser end-to-end (45 tests)

A hand-rolled Chrome DevTools Protocol client uploads the sample map through the
page's own file input and asserts the numbers reach the screen. It catches what
no unit test can: nginx capping the request body below the API's limit, a bundle
that fails to mount, a MapLibre layer silently dropped, or a response field the UI
reads under a name the API stopped using. It skips rather than fails when the
stack is down or no Chrome is present.

### 6.7 Clean-room install verification

`docs/INSTALL.md` was executed literally in a clean directory. It was wrong in
four ways, one of them serious: with the shipped `REDIS_HOST_PORT=6379`, on a
machine already running Redis, `docker compose up -d` **reports success** while
Docker leaves the container attached to no network — so the API cannot resolve
`redis` and the only symptom is a readiness check saying `degraded` with a DNS
error. The troubleshooting section promised `port is already allocated`, which
never appears. Defaults moved to 15432/16379; re-verified from scratch with no
editing.

An installation guide nobody has followed from scratch is always wrong somewhere.

### 6.8 What has *not* been validated

Stated plainly, because the gap matters more than the tests that pass:

* **No comparison against a gauged catchment.** The runoff figures are design
  estimates by an accepted method, not measurements checked against observed
  flow. The HLD's ±15 % catchment-area comparison target was not met, because no
  independently surveyed catchment for this area was obtainable.
* **No ground-truth pond outcomes.** Nothing in this system has been checked
  against a pond that was actually built and observed to fill.
* **No independent DEM cross-check.** Regenerating contours validates internal
  consistency, not absolute accuracy. A SLUSI or Survey of India toposheet for
  Durg would allow that and was not obtained.
* **Land-cover accuracy is inherited.** ESA WorldCover's own accuracy (~75 % for
  the classes used) propagates into the composite CN unexamined.

---

## 7. Scope and limitations

### 7.1 Method limitations

**The terrain is interpolated, not surveyed.** Between two contours the surface is
inferred. A hollow smaller than the contour interval — 1 m here — cannot appear in
it at all, and 99,493 cells of the sample sheet lay in closed depressions that
conditioning had to flood, the deepest 12.00 m. That is reported, because a 12 m
depression on a 31 m relief sheet may be a real landform or an interpolation
artefact, and the tool cannot tell which.

**A site can be a pond that already exists.** This is the sharpest failure mode
the model has, and it is worth stating plainly. Siting weights flow accumulation
highest and scores depression depth alongside it — and an existing tank maximises
both, because it *is* a place where water collects. Measured on the sample sheet
with land cover removed, **three of five recommended sites landed inside
permanent water**: the model was right that the location was hydrologically
ideal and wrong that anything should be built there.

Two independent sources now exclude it — ESA WorldCover's `permanent_water`
class and OpenStreetMap water polygons — so one provider failing no longer
removes the protection (that case falls to one site in five). When *neither*
answers, `environment.water_exclusion.confidence` reports `none` and the
explanation leads with it, because terrain alone genuinely cannot distinguish a
good pond site from a pond: both are depressions where water collects.

### 7.1.1 The siting veto

That finding generalised. `services/exclusions.py` builds a single boolean veto
applied to the buildable mask before any site is scored, so an excluded cell is
never ranked rather than ranked and then filtered. Four hazards, with buffers
chosen from what each one actually does to a pond:

| Hazard | Buffer | Why that distance |
|---|---|---|
| Standing water (tank, pond) | 0 m | The *bank* of a tank is good ground; only the water itself is barred. |
| Major watercourse (river, canal, riverbank) | 50 m | A village pond cannot impound a river, and the land beside one floods. |
| Building | 50 m | Impoundment next to a dwelling is a safety question, not a siting one. |
| Road | 20 m | The embankment and its borrow area need the room. |
| Land-cover water (raster) | 20 m | Two pixels of a 10 m product: boundary slack, not a setback. |

**Rivers are excluded; streams are not.** `MAJOR_WATERWAYS` covers `river`,
`canal` and `riverbank`; `MINOR_WATERWAYS` — `stream`, `drain`, `ditch` — is
never vetoed, because a check dam on a nala is the *intended* answer, not an
error. Collapsing the two would have removed the best sites on the sheet along
with the impossible ones.

**A river is mapped twice, and the first version of this only read one of them.**
The classifier keyed off the `waterway` tag alone. OSM maps a large river as a
thin centreline tagged `waterway=river` *and* as the wide body actually rendered,
tagged `natural=water` + `water=river` — which carries no `waterway` tag at all.
The body therefore fell to the standing-water rule and its 0 m buffer, the rule
written for a tank whose bank is legitimately good ground.

The consequence was not academic. On the sample sheet the Shivnath's areal body
is 563.6 ha, and the river runs a median 181 m wide (75–255 m), so the
centreline's 50 m buffer lay entirely inside the water on 23 of 25 sampled
cross-sections and never reached the bank. That left **64.7 ha of bank and
floodplain recommendable**, and siting returned a site 50 m from the river.

`classify_water` now reads both conventions — `waterway ∈ MAJOR_WATERWAYS` or
`water ∈ MAJOR_WATER_VALUES` — which took the veto from 27,969 to 53,843 river
cells and moved 26,486 cells out of the standing-water bucket where they never
belonged. Two supporting changes close the same gap from other directions:

* An unlabelled `natural=water` area is classified by *shape*, but only well
  clear of the tanks it must not catch: promoted to a watercourse solely when it
  is both elongated ≥ 5:1 and larger than 2 ha. A misjudged tank costs one
  candidate beside an existing tank; a missed river puts a pond in a river, so
  the thresholds are set to be quiet rather than clever, and any promotion is
  reported in the response.
* Land-cover water is dilated by 20 m. Raw class pixels gave no margin at all,
  so the waterline itself stayed sitable — and when OSM is the source that
  failed, this raster is the only water protection left. Two pixels of a 10 m
  product is classification slack at the land/water boundary, not a hydrological
  setback, which is why it is nothing like the 50 m river buffer.

**Multipolygon relations.** `build_query` asks for ways only, on measured cost
grounds: adding `relation[...]` across every feature kind answered a 3 × 2.6 km
window with HTTP 504. But a large river's areal extent is frequently a
multipolygon relation, and missing one does not mis-buffer the river — it loses
the river entirely. Relations are therefore fetched as a *second, water-only*
request whose failure is non-fatal, and `water_relations` records whether it
landed, because "no relations here" and "relations not fetched" are different
facts. Adding coverage must not introduce a new way to lose what already worked.

A terrain-only backstop bars cells draining more than 2,000 ha, which is
river-scale contributing area. It is a floor for the case where both vector
sources fail, not a substitute for them: the sample sheet's entire extent
accumulates only 386 ha, so on a map this size the backstop correctly never
fires and the honest report is `confidence: terrain-only`.

Measured on the sample sheet (27,472 water cells, 27,390 river, 87 building,
7,818 road):

| Sources available | Sites landing on a hazard | Reported confidence |
|---|---|---|
| Land cover + OSM | none | `high` |
| OSM only | none | `partial` |
| Land cover only | 1 of 5 in water | `partial` |
| Neither | 3 of 5 in water | `terrain-only` |

**How the earlier version of this table lied.** It read "none" for the
land-cover-plus-OSM row while a site sat 50 m from the Shivnath, because the
audit that produced it built its river ground truth by calling
`_split_water` — the function under test:

```python
_, major = exclusions._split_water(list(osm.water))   # the bug, on both sides
```

Implementation and ground truth shared the blind spot, so the check could not
fail. `tests/golden/test_no_pond_in_a_river.py` now derives ground truth from OSM
tags directly and is deliberately *broader* than the product's predicate — it
also matches on the feature name, which no product code consults.

That test also stopped relying on sampling five points. With the fix reverted,
the five recommended sites happened to miss the river band, so a point check
still passed; what caught it was asserting that the veto mask **covers** the
independently derived 50 m standoff. That measure reads 0.000 % uncovered with
the fix and 12.4 % without it, against a 1 % threshold — five points can miss a
river by luck, a mask cannot.

The response carries the audit at `suitability.exclusions` — cell counts per
hazard, which sources contributed, and the confidence — so a reader can see what
protection was in force rather than assume it.

**D8 routes all flow to one neighbour.** On genuinely flat ground this produces
parallel flow lines where real water spreads. Conditioning mitigates it; it does
not remove it.

**Catchments clipped by the sheet edge are lower bounds.** The contour map covers
its own extent and nothing beyond, so a catchment reaching the boundary has part
of its contributing area unmeasurable. This is flagged per catchment rather than
silently reported as a measurement.

**Runoff is a design estimate.** SCS-CN is an accepted method, not a measurement.
The cross-checks bound it; they do not calibrate it.

**Khosla's formula is reported but excluded from the comparison range.** On this
catchment it returns C = 0.88 — its loss term of 0.48·T is about 14 mm/month
against 300–400 mm of monsoon rainfall, so it degenerates. Reporting it and
excluding it is more honest than either hiding it or averaging it in.

### 7.2 Data limitations

**Village polygons do not exist in open data.** geoBoundaries stops at
sub-district; SHRUG's geometry is behind a form. Each village therefore links to
its containing sub-district, and `boundary_level` records that the polygon is the
sub-district's — a 662 km² tehsil is not a village boundary, and the API refuses
to imply otherwise.

**187 villages carry no polygon at all**, from one genuinely ambiguous name (two
Nawagarh tehsils in the same state, different districts). They are searchable; the
seeder reports them rather than guessing.

**Village-level LGD codes need a registration** at `lgdirectory.gov.in`. Only the
Gram Panchayat code is openly available, so `villages.lgd_code` is null rather
than populated with a plausible-looking wrong value.

**OSM coverage varies by village.** The sample sheet has 190 roads and 3
buildings — the roads are well mapped, the buildings clearly are not. Exclusions
are therefore a lower bound, and the response says so.

**Land tenure is not modelled at all.** No open dataset gives village-level
ownership, so a site that is physically ideal may be privately held. This is the
single largest gap between "recommended" and "buildable", and it has to be checked
on the ground.

### 7.3 Engineering limitations

**Costs are indicative.** One excavation rate applied to a volume, with no
lead-in, lining, land acquisition, or local schedule of rates.

**The pond footprint has no orientation.** The design fixes plan dimensions and
depth; nothing in the model chooses a bearing. The rectangle drawn on the map and
exported to GeoJSON carries an `orientation: indicative` property, because a
polygon in a GIS file looks surveyed whether or not it is.

**Job state is in memory unless Redis is up**, and reports are held in memory and
bounded. The durable artefact is the analysis, which can be re-rendered.

**No authentication.** The API is unauthenticated by design — it runs locally.

### 7.4 Requirements not built

`FR-11` cadastral upload, `FR-12` side-by-side site comparison, `FR-13`
water-balance simulation with evaporation and seepage, and `FR-14`
natural-language explanation are **not implemented**. They were scoped as
*Should*/*Could* and are listed as unbuilt in the burn-down table
(`docs/IMPLEMENTATION_PLAN.md` §0.2) rather than described as if present.

`FR-10` is partial: GeoJSON and PDF export work; save/load of named projects does
not.

Four capabilities — rainfall statistics, runoff, pond design, soil — are computed
and tested but have **no standalone endpoint**. They are returned inside
`POST /analyzeContour`, because the analysis is the unit of work and splitting
them would mean re-deriving the DEM per call. The capability is delivered; the
separate route in the HLD's endpoint catalogue is not.

The ML suitability layer (HLD §6.5.4) is **not built**. The "AI" in this system is
the AHP-MCDA engine with an auditable consistency ratio, not a learned model. A
model trained without ground-truth pond outcomes would be a model of its own
training labels.

---

## 8. Future work

Ordered by what would most improve the answer, not by what is most interesting to
build:

1. **Ground-truth validation.** Bhuvan's MGNREGA geotagged asset layer carries
   locations of water-harvesting structures already built. Comparing
   recommendations against them is the single highest-value next step — it turns
   internal consistency into external accuracy, and would supply the training
   labels an ML layer needs to mean anything.
2. **A surveyed DEM cross-check.** A SLUSI sheet or Survey of India toposheet for
   Durg would let §6.3's internal round-trip become an absolute accuracy figure.
3. **Cadastral overlay (FR-11).** Land tenure is the largest gap between
   recommended and buildable. Accepting an uploaded ownership layer needs no new
   science, only the plumbing.
4. **Water-balance simulation (FR-13).** Evaporation and seepage over the year
   turn "capacity" into "how long it holds water", which is what a village
   actually asks.
5. **Strange (1928) tables**, once a citable published source is in hand.
6. **India-native sources** — Bhoonidhi CartoDEM, IMD gridded rainfall. These
   would need credentials, which the project deliberately has none of: the
   configuration for them was removed rather than left as empty placeholders,
   because five blank credential fields made `/health/ready` report five
   "missing key" lines describing features that were never built. Adding one
   back would mean re-adding its config, which is a small, honest cost to pay
   only when the source is actually wired up.
7. **ML suitability layer**, only after (1). Random-forest or gradient-boosted
   scoring with SHAP attribution, validated by spatial block cross-validation, so
   the reported skill is not inflated by spatial autocorrelation.

---

## 9. References

**Standards and Indian practice**

1. Central Water Commission / IMD practice on initial abstraction, Ia = 0.3S.
2. **IMSD Guidelines**, NRSA/ISRO — the standard Indian methodology for RS/GIS
   water-harvesting site selection; the AHP criteria set derives from it.
3. IS 5477 (Parts 1–4) — Fixing the capacity of reservoirs.
4. Inglis, C.C. & DeSouza (1929) — runoff formulae for the Bombay Presidency.
5. Khosla, A.N. (1960) — *Appraisal of Water Resources*.
6. Barlow (1912) — runoff coefficients for United Provinces catchments.
7. Strange, W.L. (1928) — runoff tabulation. *Cited but not implemented.*
8. Kirpich, Z.P. (1940) — Time of concentration of small agricultural watersheds.

**Method**

9. Saaty, T.L. (1980) — *The Analytic Hierarchy Process*. McGraw-Hill.
10. Horton, R.E. (1945) — Erosional development of streams.
11. Strahler, A.N. (1957) — Quantitative analysis of watershed geomorphology.
12. Horn, B.K.P. (1981) — Hill shading and the reflectance map.
13. Barnes, R., Lehman, C. & Mulla, D. (2014) — Priority-Flood depression filling.
14. Lindsay, J.B. (2016) — Efficient hybrid breach-fill DEM conditioning.
15. Ester, M. et al. (1996) — DBSCAN.
16. USDA-SCS (1972) — *National Engineering Handbook*, Section 4: Hydrology.

**Data sources**

17. Copernicus DEM GLO-30, ESA — 30 m global DEM.
18. ESA WorldCover 2021 v200 — 10 m land cover, CC-BY 4.0.
19. ISRIC SoilGrids v2.0 — 250 m soil properties, CC-BY 4.0.
20. Open-Meteo ERA5-Land archive — daily reanalysis, CC-BY 4.0.
21. NASA POWER (MERRA-2) — daily reanalysis, public domain.
22. SHRUG SHRID→LGD crosswalk, Harvard Dataverse — CC0 1.0.
23. geoBoundaries gbOpen ADM1–ADM3 — ODbL 1.0.
24. OpenStreetMap contributors, via Overpass API — ODbL 1.0.

---

*Companion documents: `docs/HLD.md` (design), `docs/IMPLEMENTATION_PLAN.md`
(phasing and burn-down), `docs/API.md` (endpoint reference), `docs/INSTALL.md`
(clean-room verified setup).*
