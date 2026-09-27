# API Reference

Base URL when running locally: **`http://localhost:8000/api/v1`**

Interactive docs — including a real captured response as the example —
**http://localhost:8000/docs**. Machine-readable schema: `/openapi.json`.

The browser UI at **http://localhost:8080** calls this same API; nginx serves the
page and reverse-proxies `/api/`, so `http://localhost:8080/docs` reaches these
docs from a single origin. Its upload limit is pinned to the API's own 50 MB.

All geometry is **GeoJSON in EPSG:4326**. Every measurement carries its unit in
the field name (`area_ha`, `volume_m3`, `depth_m`, `rainfall_mm`, `relief_m`).

---

## `POST /analyzeContour`

Upload a contour map; get pond sites, their catchments, runoff and pond designs.
`POST /findCatchment` is an identical alias. To analyse an area drawn on the map
instead, with no file, see [`POST /analyzeArea`](#post-analyzearea).

**Request** — `multipart/form-data`. Only `contour_map` is required.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `contour_map` | file | — | **Required.** Contour map, `.kml` / `.kmz` (also accepts `.xml`) |
| `file` | file | — | Accepted alias for `contour_map`. Send one, not both — both is a 400 |
| `cell_size_m` | float 1–30 | *derived* | Interpolation grid resolution. Omit to derive it from mean contour spacing |
| `max_sites` | int 1–25 | `5` | Maximum ranked sites |
| `max_slope_pct` | float | `8.0` | Reject cells steeper than this |
| `min_upstream_ha` | float | `1.0` | A site must receive runoff from at least this much upstream area |
| `score_threshold` | float 0–1 | `0.55` | Suitability floor for *channel* candidates; natural depressions are not gated by it |
| `min_separation_m` | float | `300` | Two sites closer than this describe one structure |
| `min_depression_depth_m` | float | `0.3` | Below this a hollow is survey noise |
| `snap_radius_m` | float | `150` | How far a pour point may be nudged onto the drainage line |
| `include_catchment_geometry` | bool | `true` | Include each catchment's GeoJSON polygon |
| `include_contours` | bool | `false` | Echo the parsed contours as GeoJSON (large) |
| `enrich` | bool | `true` | Fetch soil, land cover and rainfall from the area's own location |
| `rainfall_years` | int 1–70 | `30` | Years of rainfall record |

### Worked example

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'contour_map=@contours_1m.kml' \
  -F 'max_sites=3' \
  -o analysis.json
```

Terrain only, with no network access at all (~3 s):

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'contour_map=@contours_1m.kml' -F 'enrich=false'
```

Finer grid, geometry omitted for a compact response:

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'contour_map=@contours_1m.kml' \
  -F 'cell_size_m=3' \
  -F 'include_catchment_geometry=false'
```

### Response shape

```
analysis_id, generated_at, elapsed_s, stage_timings_s
input                     filename, size, and every option applied
contour_map               what was READ from the file — see below
interpolated_terrain      grid resolution and how it was derived; conditioning results
area_of_interest          GeoJSON bbox of the contours
suitability               analysis tier, layers used/unavailable, criteria weights, constraints
environment               soil, land cover, rainfall — and any provider failures
recommended_site          the top-ranked site (same shape as an entry below)
candidate_sites[]         ranked sites, each with catchment + runoff + pond
warnings[]
```

**`contour_map`** — evidence that nothing was assumed:

```json
{
  "elevation_source": "uploaded_contour_map",
  "elevation_strategy": "placemark_name",
  "lines_parsed": 1355, "lines_unresolved": 0, "vertices_used": 159113,
  "levels": 32, "contour_interval_m": 1.0,
  "elevation_min_m": 267.0, "elevation_max_m": 298.0, "relief_m": 31.0,
  "bounds_4326": [81.2814, 21.2398, 81.3126, 21.2636],
  "centroid_4326": [81.29703, 21.25170],
  "working_crs_epsg": 32644,
  "has_boundary_polygon": true,
  "warnings": []
}
```

`elevation_strategy` is one of `coordinate_z`, `extended_data`,
`placemark_name`, `folder_name` — tried in that order.

**A candidate site:**

```
rank, suitability_score (0–100), site_kind
    site_kind: "natural_depression" (a bowl — least excavation)
             | "channel_position"   (excavation on a drainage line)
location              lon, lat, projected x/y, grid row/col
terrain               elevation, depression depth, slope, upstream area
region                extent of the candidate landform
catchment_pour_point  where the catchment was delineated from — the region's spill
                      point, which is not the same cell as the pond position
criteria_breakdown[]  per criterion: raw value, normalised, weight, contribution
                      (the contributions sum to the score)
catchment
    metrics           area_ha/km2, perimeter, relief, mean & max slope,
                      longest_flow_path_m, time_of_concentration_min (Kirpich),
                      form_factor (Horton), compactness_coefficient (Gravelius)
    pour_point        GeoJSON Point + grid indices
    snapped           whether the point moved onto the channel, and how far
    quality           touches_survey_edge, plain-language confidence
    geometry          GeoJSON (Multi)Polygon
runoff
    curve_number      composite CN at AMC I/II/III, HSG, per-land-cover breakdown
    annual_mean       depth, volume, runoff coefficient
    design_75_percent_dependable   the figure to size on
    assumptions[]     every departure from the textbook, stated
pond
    recommended       depth, plan and bottom dimensions, gross/live/dead capacity,
                      excavation and embankment volumes, indicative cost
    binding_constraint       WHICH limit produced this size
    constraints_evaluated    every limit considered, with its value
    footprint         contiguous buildable land around the site
    stage_storage_curve      depth ↔ flooded area ↔ volume, from the terrain
    hydrological_check       capacity against the catchment's annual yield
```

`binding_constraint` is the actionable part. Values: `practical_excavation_depth`,
`plan_area_geometry`, `sustainable_yield_share`, `water_table_clearance`,
`budget`. On the sample, three candidates hit three different constraints.

### `summary` — the three headline results

Every analysis response, from a contour map or a drawn area, opens with the three
results the brief asks for, for the recommended site:

```json
"summary": {
  "available": true,
  "site_rank": 1,
  "suitability_score": 82.7,
  "pond_location": {"lat": 21.25114, "lon": 81.29545},
  "catchment_area_ha": 180.25,
  "catchment_area_km2": 1.8025,
  "expected_water_volume_m3": 73514.0,
  "expected_water_volume_basis": "the pond's live storage, capped by the catchment's 75 % dependable annual inflow: the water it can collect in three years of four",
  "expected_water_volume_limited_by": "storage",
  "pond_capacity_m3": {"gross": 81682.0, "live": 73514.0},
  "annual_inflow_m3": {"mean": 530632.0, "dependable_75_percent": 326365.0}
}
```

`expected_water_volume_m3` is the smaller of the pond's live storage and the
catchment's 75 % dependable inflow: storage the catchment cannot fill in a normal
year is not collectable, and inflow beyond what the pond holds spills.
`expected_water_volume_limited_by` says which one bound it. Inflow is never
totalled across sites — catchments nest, so a sum would count the same water twice.
When no site qualifies, `available` is `false` and the values are `null`.

Every entry in `candidate_sites` carries the same figure for itself as
`expected_water` — `volume_m3`, `limited_by`, `basis`, `pond_capacity_m3` and
`annual_inflow_m3` — which is what the map labels each site marker with.

### The plain-language explanation (FR-14)

Every analysis carries an `explanation` block: what was chosen, why it scored as
it did, what limits the pond, and the caveats.

```json
{ "available": true,
  "recommended": {
    "summary": "Site #1 is the strongest of the 5 assessed, scoring 81.3 out of 100. ... What limits it is depth: the pond is already as deep as is practical to excavate and maintain by ordinary means. ...",
    "caveats": ["Land tenure is not modelled: ..."],
    "generated_by": "deterministic templates over the computed values -- no language model."
  },
  "alternatives": [ ... ] }
```

**No language model is used, and that is a design decision rather than a
limitation.** Handing the numbers to an LLM would read more fluently and be worse
in three ways that matter here:

* **Determinism.** The same analysis must produce the same words. A
  recommendation a village acts on cannot vary between two runs of identical
  inputs, and it has to be reproducible months later when someone asks why a site
  was chosen.
* **Traceability.** Every clause is generated from a named field, so any sentence
  can be checked against the JSON beside it. A generated paragraph can restate a
  number correctly and still assert a causal claim the data does not support.
* **No dependency.** The system runs offline on a local machine with no
  credentials, and prose should not be the thing that breaks that.

The ordering is deliberate — decision, reason, limit, caveat — so a reader who
stops after one sentence still has the answer. Caveats are emitted only when they
apply: a catchment clipped by the survey edge, a degraded tier, a pond holding
under 5 % of its catchment's yield, or a margin over the runner-up too narrow to
read as a ranking. Tenure is flagged every time, because it is never modelled.

---

### Analysis tiers

`suitability.analysis_tier` states what was actually measured:

| Tier | Available | Consequence |
|---|---|---|
| `full` | terrain + soil + land cover + rainfall | everything measured |
| `no_soil_lulc` | terrain + rainfall | runoff on a stated assumed soil group |
| `terrain_only` | terrain alone | site, catchment area and stage–storage capacity; no runoff |

`environment.provider_failures` names any layer that was dropped and why. Layers
are fetched concurrently within a 20 s budget; a slow or failing provider
degrades the tier rather than delaying the response.

---

### Exclusions

Some ground cannot hold a pond regardless of how well it scores. `suitability.exclusions`
reports the veto that was applied to the buildable mask *before* scoring:

```json
"exclusions": {
  "excluded_cells": 95441,
  "removed_by": {
    "standing_water": 29243,
    "major_watercourse": 27969,
    "building": 1453,
    "road": 45694,
    "land_cover_water": 27472,
    "land_cover_built_up": 32266
  },
  "sources": ["OpenStreetMap", "land cover", "terrain"],
  "confidence": "high",
  "notes": ["27,969 cells excluded within 50 m of a mapped river or canal: ..."]
}
```

Buffers: standing water 0 m (the bank of a tank is buildable, the water is not),
river or canal 50 m, building 50 m, road 20 m, land-cover water 20 m. **Streams,
drains and ditches are deliberately not excluded** — a check dam on a nala is the
intended recommendation.

A river is classified from either tagging convention OSM uses for it: the
`waterway=river` centreline *and* the areal `natural=water` + `water=river` body,
which carries no `waterway` tag. An unlabelled `natural=water` area elongated
≥ 5:1 and larger than 2 ha is treated as a watercourse on shape, and `notes` says
so when that happens. `notes` also reports when water multipolygon *relations*
were not part of the window, since a river mapped as a relation would be absent
rather than mis-buffered.

`confidence` says how much protection was actually in force — `high` (both land
cover and OpenStreetMap answered), `partial` (one did), or `terrain-only`
(neither, leaving only a 2,000 ha upstream-area backstop). At `terrain-only` the
site explanation leads with that caveat, because terrain alone cannot tell a good
pond site from a pond that already exists: both are depressions where water
collects.

---

## `POST /analyzeArea`

Analyse a **rectangle drawn on the map**, with no file. Terrain is the Copernicus
GLO-30 global elevation model at its native 30 m, fetched for the rectangle plus a
500 m buffer. The response has **the same shape as `/analyzeContour`**, so every
section above applies; `contour_map` is `null` and `terrain_source` says where the
terrain came from.

**Request** — `application/json`. Only `bbox` is required.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `bbox` | 4 numbers | — | **Required.** `[min_lon, min_lat, max_lon, max_lat]` in WGS84 degrees; 0.1–100 km² |
| `max_sites` | int 1–25 | `5` | Maximum ranked sites |
| `max_slope_pct` | float | `8.0` | Reject cells steeper than this |
| `enrich` | bool | `true` | Fetch soil, land cover and rainfall for the area |
| `include_contours` | bool | `false` | Return contours traced from the terrain, as GeoJSON |
| `include_catchment_geometry` | bool | `true` | Include each catchment's GeoJSON polygon |

```bash
curl -X POST http://localhost:8000/api/v1/analyzeArea \
  -H 'Content-Type: application/json' \
  -d '{"bbox": [81.2814, 21.2398, 81.3126, 21.2636]}'
```

- **Sites are proposed only inside the rectangle.** The buffer is there so a
  catchment that begins outside the line you drew is still measured whole.
- `terrain_source` carries the dataset, resolution, the drawn and the analysed
  bounds, the Copernicus tiles read, and whether the terrain came from the local
  cache (fetched terrain is kept on disk, so redrawing the same area is fast).
- The resolution is stated because it matters: at 30 m a 141 m pond spans about
  five cells, where the sample contour sheet interpolates to 5 m.

| Status | When |
|---|---|
| 400 | `bbox` not four numbers, inverted or zero-size, out of range, or below 0.1 km² |
| 413 | Above the area cap (`MAX_AOI_KM2`, 100 km²); the response gives `area_km2` and `max_km2` |
| 422 | No terrain exists there: the rectangle lies over open sea, where Copernicus has no tile |
| 503 | Copernicus could not be reached; the detail names the tiles tried |

The returned `dem_id` works with every follow-up route below, exactly as a
contour upload's does.

**How it compares with a contour upload.** Over the sample sheet's own extent,
Copernicus agrees with the 5 m contour surface at r = 0.905 (relief 30.3 m against
31.0 m, after a constant 5 m offset), but none of its candidates lies within 430 m
of the upload's recommended site, and the catchments differ: each 30 m cell covers 36 times the ground. Use
a drawn area to screen any village; upload a contour map where a survey exists.
`scripts/compare_area_vs_contour.py` reproduces the comparison.

## `POST /terrain/contour-map`

Parse and interpolate only — useful for checking what was read before committing
to a full analysis.

```bash
curl -X POST http://localhost:8000/api/v1/terrain/contour-map \
  -F 'contour_map=@contours_1m.kml'
```

```json
{
  "dem_id": "a6a18c43079641fc",
  "contour_map": { "...": "as above" },
  "interpolated_terrain": { "...": "grid resolution, hull coverage, method" },
  "area_of_interest": { "type": "Polygon", "coordinates": [] },
  "warnings": []
}
```

Optional query parameter: `cell_size_m`.

Parsed maps are held in memory and do not survive a restart.

---

## `GET /terrain/contour-map/{dem_id}/contours`

Echo the contours exactly as parsed, so you can confirm the elevations were read
from the right place.

```bash
curl "http://localhost:8000/api/v1/terrain/contour-map/$DEM_ID/contours?limit=3&simplify_deg=0.0001"
```

| Parameter | Meaning |
|---|---|
| `limit` | return only the lowest N contour lines |
| `simplify_deg` | Douglas–Peucker tolerance in degrees, 0–0.01 |

---

## `POST /hydrology/catchment`

The land that drains to any point: the polygon, the morphometrics, and how far
the point had to move to reach a channel.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `dem_id` | string | — | From `/analyzeContour` or `/terrain/contour-map` |
| `lon`, `lat` | float | — | The pour point |
| `snap_radius_m` | float 0–2000 | `150` | How far the point may move onto the drainage line |

```bash
curl -s -X POST localhost:8000/api/v1/hydrology/catchment \
     -F "dem_id=$DEM" -F 'lon=81.2899514' -F 'lat=21.2473513' | jq '.metrics, .snapped'
```

```json
{ "area_ha": 264.26, "area_km2": 2.64257, "relief_m": 24.0,
  "mean_slope_pct": 4.35, "longest_flow_path_m": 3833.4,
  "time_of_concentration_min": 78.8, "form_factor": 0.18,
  "compactness_coefficient": 2.26, "touches_grid_edge": false }
{ "was_snapped": true, "moved_m": 150.0,
  "search_radius_m": 150.0, "hit_the_search_limit": true }
```

Three different points give three visibly different catchments — 7.70 ha,
1.24 km², 1.99 km² on the bundled survey — which is how a reader checks the flow
routing is doing something rather than taking it on trust. The browser suite
asserts it and fails if any two agree.

### Snapping matters more than it looks

A click a few metres off the channel lands on a hillside cell whose catchment is
the hillside: a few hectares instead of a few hundred, and the result looks
entirely plausible (HLD CH-12). So the point is moved to the
highest-accumulation cell within `snap_radius_m`, and the response says how far.

The search area is a **circle**, so `moved_m ≤ search_radius_m` always holds. A
square window — which is what a naïve array slice gives you — reaches
`radius·√2` into its corners, and reported a 175 m move under a 150 m radius.

**`hit_the_search_limit`** is set when the point moved as far as it was allowed,
meaning the search ran out of room rather than finding a channel. It is set more
often than not, and the answer deserves less confidence when it is.

**`touches_grid_edge`** means part of the contributing area lies outside the
survey, so the reported area is a lower bound.

Setting `snap_radius_m=0` delineates exactly where you clicked, which on the
sample returns a single cell — 0.00 ha. That is the correct answer to the
question and almost never the one intended.

### Conditioning: fill, breach, or auto

`conditioning` decides how the surface is made routable, and the response reports
what was done under `conditioning`:

| Value | What it does |
|---|---|
| `fill` | Raises every depression to its spill level. Robust, always succeeds. |
| `breach` | Carves outlets through thin barriers first, then fills the rest. |
| `auto` *(default)* | Breaches when more than 15 % of the surface has no usable gradient. |

**Why breaching exists here.** A pond goes *in* a depression, and filling removes
the depression. The measurements already work around that — slope and depth are
taken on the original surface — but the *routing* still runs over a raised
plateau, so accumulation spreads across a filled hollow instead of converging
into it and leaving by one channel.

On the bundled survey, `breach` resolves **209 of 313 depressions with 941 cells
carved, a maximum cut of 0.78 m and 487 m³ moved.** Those are interpolation
artefacts where two contour lines nearly touched, and a filled surface hides
every one.

It is bounded in depth (2 m) and path length (40 cells), and **refuses rather
than trenches**: an unbounded least-cost search always finds *some* path, and
carving four metres across half a survey to drain a closed basin invents
topography rather than revealing it. Breaching reduces filling; it does not
replace it — the fill pass runs afterwards regardless, so the surface is always
fully routable.

```json
{ "method": "breach_then_fill", "method_chosen_by": "breach",
  "depressions_found": 313, "depressions_breached": 209, "cells_carved": 941,
  "max_carve_depth_m": 0.777, "total_carve_volume_m3": 487.0,
  "limits": {"max_breach_depth_m": 2.0, "max_breach_length_cells": 40},
  "flatness": {"flat_pct": 8.38, "gradient_threshold": 0.001,
               "interpretation": "some flat ground; routing is reliable but check the catchment shapes"},
  "cells_still_filled": 96010, "max_fill_depth_m": 12.0 }
```

`flatness.interpretation` is there because the number alone says little. Above
40 % flat, D8 directions are largely an artefact of the conditioning rather than
of the terrain, and catchment boundaries should be read as indicative.

---

## `POST /hydrology/streams`

The drainage network of a DEM, with Strahler order per channel.

A catchment outline says how much land drains to a point. This says *where the
water goes on the way* — often the more useful picture for siting: the same
location on a first-order headwater collects from a few hectares, on a
fourth-order channel from hundreds, and needs a spillway sized accordingly.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `dem_id` | string | — | From `/analyzeContour` or `/terrain/contour-map` |
| `threshold_ha` | float 0–10000 | `1.0` | Contributing area at which a channel begins |
| `lon`, `lat` | float | — | Pour point; restricts the network to that catchment |
| `snap_radius_m` | float 0–2000 | `150` | How far the pour point may move onto a channel |

```bash
curl -s -X POST localhost:8000/api/v1/hydrology/streams \
     -F "dem_id=$DEM" -F 'threshold_ha=1' \
     -F 'lon=81.2899514' -F 'lat=21.2473513' | jq .network
```

```json
{
  "threshold_ha": 1.0, "threshold_cells": 400, "stream_cell_count": 3106,
  "reach_count": 83, "max_strahler_order": 4,
  "total_length_km": 18.265,
  "drainage_density_km_per_km2": 6.912, "area_analysed_km2": 2.64257,
  "by_order": {"1": {"reaches": 64, "length_m": 8745.5},
               "2": {"reaches": 16, "length_m": 6221.3},
               "3": {"reaches": 2,  "length_m": 1522.8},
               "4": {"reaches": 1,  "length_m": 1775.1}}
}
```

Each GeoJSON feature carries `strahler_order`, `length_m` and
`upstream_area_ha`. Drive line width from the order — that is the reason for
computing it.

**The threshold is an area, not a cell count.** 1 ha means the same thing at 5 m
(400 cells) and at 30 m (11), so a value tuned on one survey transfers to
another. It is deliberately small for village terrain: a nala draining a few
hectares is exactly what a check dam sits on.

**A reach is a Strahler stream**, running from where it attains its order to
where it loses it — not from junction to junction. That distinction is load
bearing: cutting at every junction chops a trunk into one segment per tributary,
and the counts then stop obeying Horton's law of stream numbers. On the sample
catchment it produced 16 order-4 "reaches" against 10 of order 3, a bifurcation
ratio of 0.62, which is impossible. As Strahler streams the numbers run
64/16/2/1 and the mean lengths 137/389/761/1775 m — both of Horton's laws hold,
and `app/tests/golden/test_streams_analytic.py` plus the real-terrain checks in
`test_contour_round_trip.py` assert them.

**`drainage_density_km_per_km2` is null unless a pour point is given.** Channel
length over an arbitrary rectangle is a property of the rectangle, so the field
carries nothing rather than a number that means nothing.

---

## `POST /terrain/contours`

Trace contour lines through the DEM behind a `dem_id`, at any interval — not just
the one the uploaded file happened to use.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `dem_id` | string | — | From `/analyzeContour` or `/terrain/contour-map` |
| `interval_m` | float 0–500 | `1.0` | Vertical spacing between contours |
| `index_every` | int 1–50 | `5` | Mark every Nth line as an index contour |
| `simplify` | bool | `true` | Douglas–Peucker at a third of a cell |

```bash
curl -s -X POST localhost:8000/api/v1/terrain/contours \
     -F "dem_id=$DEM" -F 'interval_m=2' | jq .generation
```

```json
{
  "interval_m": 2.0, "index_every": 5, "level_count": 15,
  "line_count": 448, "index_line_count": 81,
  "elevation_min_m": 267.0, "elevation_max_m": 297.989,
  "total_length_m": 316908.5,
  "vertices_before_simplify": 75823, "vertices_after_simplify": 12508,
  "vertex_reduction_pct": 83.5, "simplify_tolerance_m": 1.75,
  "working_crs_epsg": 32644
}
```

Each GeoJSON feature carries `elevation_m`, `is_index` and `length_m`. Draw index
contours thicker and label only those — labelling all 31 levels of a 1 m map
produces an unreadable mat of text.

**Levels snap to multiples of the interval.** At 1 m the lines fall on 267, 268,
269; at 5 m on 270, 275, 280. Starting from the surface minimum would give
267.31, 268.31 — correct, and useless to read off a map.

**Simplification removes about 84 % of the vertices** at every interval tested.
Marching squares emits one per cell crossing, which no browser will draw; the
tolerance is a third of a cell, so the line stays well inside the
interpolation's own uncertainty.

**Marching squares, not `gdal_contour`** (HLD Decision 7) — no system GDAL binary
to depend on, and the raster is already in memory.

### It is also a check on the interpolation

Regenerating the input interval and comparing against the surveyed lines tests
`services.interpolate` in a way nothing else does. On the bundled sample the DEM
reproduces the surveyed elevations to a **median 0.109 m, 95th percentile
0.371 m** — a tenth of the 1 m contour interval. `app/tests/golden/test_contour_round_trip.py`
asserts it.

That comparison is on *elevations*, not on distances between lines: a contour's
horizontal position is its elevation divided by the local slope, so on nearly
flat ground a 0.1 m vertical error displaces the line tens of metres sideways
with nothing wrong.

---

## `POST /terrain/derivatives`

Write the DEM behind a `dem_id` as Cloud-Optimized GeoTIFFs and get XYZ tile
templates back. Needs the tiles service: `docker compose up -d titiler`.

HLD ADR-3 is why this exists: a 5 m DEM over 8.5 km² is 342,550 cells, and
shipping that as JSON freezes the tab. As a COG the browser fetches only the
256×256 tiles it can see.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `dem_id` | string | — | From `/analyzeContour` or `/terrain/contour-map` |
| `products` | string | `dem,slope,hillshade` | Comma-separated subset |
| `hillshade_azimuth_deg` | float 0–360 | `315` | Light direction, compass degrees |
| `hillshade_altitude_deg` | float 0–90 | `45` | Light elevation above the horizon |
| `hillshade_z_factor` | float 0–20 | `1.0` | Vertical exaggeration; 3–5 reads well on Indian plateau relief |

```bash
DEM=$(curl -s -X POST localhost:8000/api/v1/analyzeContour \
        -F 'contour_map=@contours_1m.kml' -F 'enrich=false' | jq -r .dem_id)
curl -s -X POST localhost:8000/api/v1/terrain/derivatives \
     -F "dem_id=$DEM" -F 'hillshade_z_factor=4' | jq '.layers[].product'
```

Each layer carries a template with `{z}/{x}/{y}` left for the map client:

```json
{
  "product": "hillshade",
  "tile_url_template": "/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=/data/cache/cog/71/71c6…-hillshade.tif&rescale=0,254",
  "legend": "Shaded relief, light from the north-west at 45 degrees",
  "reused": true,
  "raster": {"epsg": 32644, "resolution_m": 5.0, "width_px": 650, "height_px": 527,
             "dtype": "uint8", "stats": {"min": 0, "max": 254, "valid_cells": 332430}}
}
```

**`reused`** — the raster was already on disk. Rasters are content-addressed on
the elevation grid itself plus the parameters that shaped it, so the same DEM is
never rasterised twice: 3 s for the first call, 56 ms for the second.

**Colouring.** Slope is rescaled to a fixed `0,15` %, because per-raster
rescaling would make a flat plateau look as varied as a hillside when the number
that matters is the 8 % buildability threshold. Elevation *is* rescaled to its
own 2nd–98th percentiles — a fixed range renders 30 m of plateau relief as one
flat colour. Hillshade gets no colormap: the band already *is* the grey value.

**Slope is Horn's method**, the same function the siting model uses, on the
**original** ground rather than the depression-filled surface — a filled hollow
reads as 0 % slope exactly where a pond would go.

## `GET /terrain/{dem_id}/overlays`

Slope and shaded relief **without a tile server** — what the workspace draws. For
each layer, the URL of one PNG and the four corners it belongs at (top-left,
top-right, bottom-right, bottom-left, as MapLibre's image source takes them).
The grid is at most a few hundred cells a side, so a single image is small, and
nothing but the API has to be running.

```bash
curl -s localhost:8000/api/v1/terrain/$DEM/overlays | jq '.overlays[] | {product, url}'
```

```json
{"product": "slope", "url": "/api/v1/terrain/3f1c…/overlay/slope",
 "coordinates": [[81.2764, 21.2683], [81.3178, 21.2683], [81.3178, 21.2349], [81.2764, 21.2349]],
 "legend": "Slope, percent (0-15 % shown; siting rejects above 8 %)",
 "resolution_m": 30.0, "size_px": [143, 123]}
```

### `GET /terrain/{dem_id}/overlay/{product}`

The image itself: `hillshade` in grey or `slope` in the magma ramp over the same
fixed 0–15 % as the tiled layer, transparent where there is no terrain. Rendered
once per `dem_id` and cached on disk.

```bash
curl -s -o slope.png localhost:8000/api/v1/terrain/$DEM/overlay/slope
```

---

## Rainfall: two sources, and the spread between them

`environment.rainfall_sources` reports every rainfall source that answered and how
far apart they are.

| Source | Dataset | Resolution | Temperature |
|---|---|---|---|
| Open-Meteo | ERA5-Land reanalysis | 0.1° (~11 km) | no |
| NASA POWER | MERRA-2 / satellite, `community=AG` | 0.5 × 0.625° (~55 × 60 km) | **yes** |

Both are fetched concurrently. Either alone is enough to produce a runoff
estimate, so **one being down no longer drops the analysis to `terrain_only`** —
verified live with Open-Meteo returning `429`, where POWER carried the analysis to
tier `full`.

```json
{ "primary_source": "nasa_power",
  "primary_reason": "finest resolution available; SCS-CN runs on this source's daily series unblended, because averaging two reanalyses' daily series would split single storms across days and understate runoff",
  "sources": {
    "nasa_power": {"mean_annual_mm": 1282.4, "cv": 0.169, "complete_years": 30,
                   "resolution": "0.5 x 0.625 deg (~55 x 60 km)", "has_temperature": true}
  },
  "failures": [{"source": "open_meteo_era5_land", "reason": "HTTP 429 from archive-api.open-meteo.com"}],
  "interpretation": "only one rainfall source answered, so the figure has no independent corroboration…" }
```

With both answering it also carries `ensemble_median_annual_mm`,
`inter_source_range_mm`, `inter_source_sigma_mm` and
`inter_source_spread_fraction`. Over the sample location the two differ by about
**15 %** — 1313 mm against 1504 mm — and that disagreement is the honest
uncertainty in a figure the pond volume is proportional to.

### The daily series is never blended

This is the important constraint. **SCS-CN is non-linear in daily rainfall
depth** — runoff from 100 mm in one day far exceeds runoff from 50 mm on each of
two days — and two reanalyses put the same storm on slightly different days.
Averaging them would turn one 100 mm storm into two 50 mm ones and *systematically
understate* runoff, while producing a smoother, better-behaved-looking series.

So SCS-CN always runs on one source's daily series, chosen deterministically by
resolution rather than by which replied first, and the ensemble is used only for
the annual statistics and the uncertainty band. It is the same trap as running
SCS-CN on annual totals — which HLD §6.9 measures at `C = 0.907` against the
correct `0.393` — reached from a different direction.

### Temperature

Only NASA POWER supplies mean air temperature, which is what Khosla's runoff
formula needs. It is taken from whichever member of the ensemble carries it, not
from the primary — the primary is chosen for rainfall resolution, and the finer
rainfall source is not the one with the temperature, so reading it off the primary
would leave Khosla permanently unavailable even when a temperature had been
fetched.

---

## The Indian runoff cross-check

Every `runoff` block in an analysis carries a `cross_check`. HLD CH-15 is why:
**SCS-CN is a US model**, calibrated on American watersheds, and a monsoon
dropping a third of the annual total in one month behaves nothing like the
rainfall its curve numbers were fitted to. The pipeline already applies the
Indian corrections — `Ia = 0.3S` per CWC/IMD rather than the US `0.2S`, AMC
adjustment per day, the model run on the **daily** series rather than annual
totals — but a corrected US model is still a US model.

So the figure is checked against formulae fitted on *Indian gauged catchments*:

| Method | Fitted on | Form |
|---|---|---|
| Inglis & DeSouza (1929) | 53 stream-gauging sites, Western India | ghat `R = 0.85P − 30.5`; plains `R = (P − 17.8)P/254` (cm/yr) |
| Khosla (1960) | Indian + US catchments, monthly | `R_m = P_m − 0.48 T_m` |
| Barlow (1912) | Uttar Pradesh catchments | `R = K P`, K = 0.07–0.36 by catchment class |
| Rational | — | `Q = C i A / 360` — peak flow for a spillway, not volume |

Which run is decided by region: Deccan and Western Ghats get Inglis-DeSouza, the
Gangetic plain gets Barlow, anywhere else gets Khosla. **Every method reports
itself either way** — one that cannot run says why, because "no cross-check was
available" and "the cross-check agreed" are different statements about the same
number.

```json
{ "region": "general", "scs_cn_runoff_mm": 251.5, "comparable_methods": 1,
  "empirical_range_mm": [442.2, 442.2],
  "agreement_band_mm": [331.6, 552.7], "agreement_tolerance": 0.25,
  "agrees_with_empirical": false, "ratio_to_nearest_empirical": 0.569,
  "interpretation": "the SCS-CN estimate of 252 mm is 1.8x *below* the lowest Indian empirical figure (442 mm), which is the less common direction — check the curve number and the antecedent moisture class before relying on either" }
```

**Agreement is a tolerance band, not containment.** With one comparable method the
"range" is a single point and nothing lands inside it — strict containment
reported disagreement for a figure 1 % away from the only number available. A
quarter is what regional fits from the 1910s–1930s, applied outside their own
catchments, can be asked to come within.

**Khosla is reported but excluded from the range.** Its loss term is `0.48 × T`,
about 14 mm for a 30 °C month against monsoon rainfall of 300–400 mm — while
actual monthly ET in central India runs 100–200 mm. It returns a coefficient near
0.88, which no rural catchment has. The figure is shown because the formula is
what the method *is*; it is kept out of the comparison rather than dragging it
upward.

**Strange (1928) is not implemented, and says so.** It is a tabulation of runoff
as a percentage of monsoon rainfall for Good/Average/Bad catchment character, not
a closed-form expression, and the table has to come from Strange or a standard
Indian irrigation text. Values written from memory and presented as a cross-check
would lend false confidence to the figure being checked, so the method reports
what it needs instead of returning a number.

---

## Villages

Village search needs the index loaded — `make seed STATE=chhattisgarh`. The
contour endpoints above need no database at all.

### `GET /villages/search`

Find a village by name, however it is spelled.

| Query | Type | Default | Meaning |
|---|---|---|---|
| `q` | string | — | Village name, Latin or Devanagari |
| `state` | string | — | Narrow to one state; matched leniently |
| `district` | string | — | Narrow to one district |
| `limit` | int 1–50 | `10` | Maximum results |

Both `q` and the stored names pass through one transliteration fold, so the
caller's spelling need not match the Census enumerator's:

```bash
curl -sG localhost:8000/api/v1/villages/search \
  --data-urlencode 'q=kutelabhata' --data-urlencode 'district=durg'
```

```json
{
  "query": "kutelabhata",
  "query_folded": "kutelabat",
  "filters": {"state": null, "district": "durg"},
  "count": 2,
  "results": [
    {
      "name": "Kutelabhatha",
      "display": "Kutelabhatha, Durg, Chhattisgarh",
      "identifiers": {"lgd_code": null, "census_2011_id": "442569",
                      "census_2001_id": "1146000",
                      "shrid": "11-22-409-03317-442569"},
      "gram_panchayats": [{"name": "Kutelabhata", "lgd_code": "124575"}],
      "similarity": 1.0,
      "matched_by": "folded",
      "boundary_level": "subdistrict",
      "hierarchy_is_ambiguous": false,
      "focus": {"lon": 81.309013, "lat": 21.19651,
                "is_centre_of": "subdistrict", "approximate": true}
    }
  ],
  "note": null
}
```

`कुटेलाभाठा` and `Kutelabhaata` return the same top hit at `similarity: 1.0`.

**`matched_by`** — `exact` (the caller typed the register's spelling) ·
`folded` (agreed only after folding) · `prefix` · `trigram`. Worth showing: "we
found your village" and "we found something 31 % similar" are different claims.

**`hierarchy_is_ambiguous`** — set when another result shares both the name *and*
the hierarchy. Durg district holds ten villages called Khapri, two of them in
the same sub-district, so name-plus-place is not always enough. Show the
identifier, or the Panchayat, when this is set.

**`identifiers.lgd_code` is always null.** No open source publishes a village
LGD code. The LGD code that *is* available belongs to the Gram Panchayat and
appears under `gram_panchayats` — a Panchayat covers a cluster of villages, so
putting its code in the village's field would misidentify it. Use
`census_2011_id` or `shrid` as the canonical key.

**`gram_panchayats`** — the elected body/bodies the village belongs to. Usually
one; occasionally several (12,045 Indian villages sit in two or more). Worth
having for three reasons: the Panchayat is what plans and builds MGNREGA water
works, so it is the unit a pond proposal is addressed to; it carries the only
LGD code obtainable; and its name resolves the namesakes the hierarchy cannot —
Durg's two Khapris are in `Khapri K` and `Khapri`.

**`note`** — present when the result needs explaining: nothing matched, or
nothing matched *well* under a filter. Searching Raipur for a Durg village
returns weak matches rather than none, and the note says where the strong match
actually is.

### `GET /villages/{village_id}`

Metadata and canonical identifiers. `identifiers.shrid` composes the Census-2011
codes; consecutive village codes are geographic neighbours.

### `GET /villages/{village_id}/boundary`

The best available polygon, **always labelled with what it outlines**:

```json
{
  "available": true,
  "represents": "subdistrict",
  "is_village_boundary": false,
  "of": "Durg",
  "area_ha": 66256.03,
  "source": "geoboundaries",
  "caveat": "This is the subdistrict outline containing the village, not the village boundary — no open source publishes village polygons for this state. Do not compute a village area from it."
}
```

Read `represents` before using `area_ha`. No keyless source publishes Indian
village polygons, so this normally returns the containing sub-district — 66,256
ha for Durg, against a village of a few hundred. If SHRUG's polygon set is
obtained, drop it into `data/seed/` and re-seed; `represents` then reads
`village` and `caveat` becomes `null`.

### `GET /villages/{village_id}/imagery`

Esri World Imagery tile template, attribution and bounds to fit the view to.
Note the `{z}/{y}/{x}` ordering — this service puts the row before the column,
and transposing them returns the wrong part of the world rather than an error.

### `GET /villages/resolve?lon=&lat=`

Reverse-geocode a point to the smallest seeded area containing it. Answers at
sub-district precision and says so; `422` when the point falls outside every
seeded area.

---

## Land availability

### `POST /land/available`

Parcels a pond could actually be dug on (FR-3). Terrain says where water
collects; this says where you are *allowed* to dig, and the two are independent
— a model that only knows the first will happily recommend the middle of a
village.

```bash
curl -sX POST http://localhost:8000/api/v1/land/available \
  -F dem_id=$DEM_ID \
  -F max_slope_pct=5 \
  -F min_area_m2=400 \
  -F use_osm=true | jq '.summary'
```

```json
{
  "parcel_count": 132,
  "total_available_ha": 62.885,
  "criteria": { "max_slope_pct": 5.0, "min_parcel_area_m2": 400.0,
                "cropland_allowed": false, "osm_exclusions_applied": true },
  "removed_by": { "slope": 165021, "land_cover": 88238, "osm_building": 4193,
                  "osm_road": 21044, "osm_water": 9370 }
}
```

Land cover comes from the cache the analysis filled for this `dem_id`, and land
cover and OpenStreetMap are fetched together under the same 20 s deadline an
analysis gives them: a layer that misses it is named in `unavailable` and the
parcels are computed without it, rather than the request outliving the gateway.

`removed_by` is the reason this endpoint is worth reading rather than trusting.
A parcel count on its own invites the assumption that terrain was the
constraint; on the Durg sheet slope removes 49 % of cells at the HLD's 5 %
default and land cover another 26 %, which is a different conversation.

**Exclusions.** Buffered OSM features — buildings 50 m, roads 20 m, existing
water 100 m so a new tank does not duplicate an old one — plus land cover that
rules the ground out and slope above `max_slope_pct`. The 5 % default is
stricter than the 8 % siting uses, because steep ground is ruled out by
excavation cost long before it is ruled out by physics.

**OSM only ever subtracts.** A building missing from OSM is not evidence of open
ground, so a village with thin coverage gets an optimistic answer rather than a
wrong one. `criteria.osm_exclusions_applied` says whether any features were
found at all — when it is `false`, read the parcels as a best case.

Both providers degrade rather than fail: if WorldCover or Overpass is
unreachable the parcels still come back and `unavailable` names what was lost.
Successful OSM windows are cached on disk for a fortnight, which turns a 15-
second call into a sub-second one; public Overpass returned 504, 502, 429 and a
clean 2.3 s answer for the same query within one afternoon, so the cache is not
an optimisation.

---

### `POST /land/cadastral`

Upload a land-parcel layer (**FR-11**) — GeoJSON, or a zipped shapefile. Give a
`job_id` too and every candidate site is checked against the parcel it sits on.

```bash
curl -sX POST http://localhost:8000/api/v1/land/cadastral \
  -F file=@parcels.zip -F job_id=$JOB | jq '.sites'
```

```json
[ { "rank": 1, "suitability_score": 81.3, "ownership": "Gram Panchayat gairan",
    "tenure": "allottable", "parcel_area_ha": 18.388 },
  { "rank": 2, "tenure": "unknown -- the site falls outside every parcel in the layer" } ]
```

This closes the largest gap between *recommended* and *buildable*. No open
dataset carries village-level ownership, so the model cannot know tenure; this
lets someone who has the layer supply it. A site outside every parcel is reported
`unknown` rather than attached to the nearest polygon — 50 m outside a parcel is
not on it.

**The datum is the quiet trap.** Indian cadastral sheets are frequently on
Everest 1830 / Kalianpur, and PROJ's *default* transformation for that pair is a
ballpark offset that moves nothing. Measured at 81.29 E, 21.25 N: six published
operations shift the point 173–194 m, and `Transformer.from_crs(4145, 4326)`
shifts it by **0**. Parcels would look correct and sit ~190 m from the truth. So
the operation is selected explicitly and returned with its stated accuracy:

```json
{ "source_crs": "Kalianpur 1962", "reprojected_to_wgs84": true,
  "datum_operation": "Kalianpur 1962 to WGS 84 (2)", "datum_accuracy_m": 1.0 }
```

Restricting the candidate set by the layer's own extent is the obvious refinement
and is also a trap: the published operations declare areas of use that a single
village's bounding box does not intersect, which filtered every real operation
out and left only the ballpark. The extent is tried first, then the unrestricted
set.

A shapefile arriving **without a `.prj`** is refused rather than assumed to be
WGS 84. GeoJSON without a `crs` member is accepted as WGS 84, because RFC 7946
requires that — a specified default, not a guess.

**Parsing is defensive**, since this is the one endpoint that reads a file a
stranger chose: path traversal, absolute paths, symlinks, entry-count floods and
zip bombs are all rejected before anything is written, and extraction is counted
as it streams because an archive's declared size is its own claim about itself.

---

## Analysis jobs

The synchronous `POST /analyzeContour` is still the right call from a script. A
browser needs something to paint during the ~24 seconds a cold analysis takes,
which is what these are for (HLD 5.1: long operations answer `202`).

### `POST /analysis`

```bash
curl -isX POST http://localhost:8000/api/v1/analysis \
  -F contour_map=@contours_1m.kml -F include_contours=true
```

```json
{
  "job_id": "c6617c923297447f8d521bffc054b30f",
  "state": "queued",
  "status_url": "/api/v1/analysis/c6617.../status",
  "result_url": "/api/v1/analysis/c6617.../result",
  "executor": "in_process",
  "estimated_duration_s": 25,
  "poll_after_s": 1
}
```

`executor` says who took the work: `celery` when a worker answered a ping,
`in_process` otherwise. The fallback is deliberate — this deployment is
local-only, and accepting a job into a queue nothing is draining would leave the
client polling `queued` for ever, which is the one failure an async API must not
have.

### `POST /analysis/area`

The job form of `POST /analyzeArea`, for a browser's progress bar. Same JSON body,
same `202` answer as `POST /analysis`; poll the same status and result routes.

```bash
curl -isX POST http://localhost:8000/api/v1/analysis/area \
  -H 'Content-Type: application/json' \
  -d '{"bbox": [81.2814, 21.2398, 81.3126, 21.2636]}'
```

The rectangle is validated **before** the job is accepted, so a bad box is a `400`
or `413` at once rather than a job that fails a minute later. Its progress reports
a `terrain` step — fetching Copernicus — where a contour job reports `parse` and
`interpolate`; everything after is the same pipeline.

**Sending a start twice is safe with `Idempotency-Key`.** Both job routes accept an
optional `Idempotency-Key` header (8–64 letters, digits, `-` or `_`). A start whose
key was seen on the same instance in the last ten minutes gets the original `202`
back, same `job_id`, instead of a second job; a missing or malformed key is simply
ignored. The web page sends one per Run and retries a start that gets no answer
within 20 s: from the campus Wi-Fi about half of all new connections to the lab
address hang, and the retry cannot know whether the first attempt arrived.

```bash
for n in 1 2; do
  curl -s -X POST http://localhost:8000/api/v1/analysis/area \
    -H 'Content-Type: application/json' -H 'Idempotency-Key: run-4f2a9c1e7b' \
    -d '{"bbox": [81.2814, 21.2398, 81.3126, 21.2636]}' | jq -r .job_id
done   # the same id twice
```

### `GET /analysis/{job_id}/status`

```bash
curl -s http://localhost:8000/api/v1/analysis/$JOB/status | jq '{state, progress_pct, current_step_label}'
```

```json
{ "state": "running", "progress_pct": 11,
  "current_step_label": "Fetching soil, land cover and rainfall" }
```

The state machine is HLD §3.7: `queued` → `running` (→ `retrying`) → one of
`done`, `partial`, `failed`, `cancelled`. `is_terminal` says when to stop
polling; `steps[]` carries every stage with its own outcome.

**`progress_pct` is weighted by measured cost, not step count.** Enrichment is
20.0 s of a 24.3 s cold run — 82 % of it — so an evenly-weighted bar would reach
57 % and then not move for twenty seconds, which reads as a hang. Each step's
`weight` is in the response so a client can draw the stages to scale.

### `GET /analysis/{job_id}/result`

Served once the job is `done` **or `partial`**, and the second case is the point.
If SoilGrids is unreachable there is still terrain, rainfall and a catchment; the
analysis falls back to an assumed soil group and says so in `warnings`. Refusing
the result because one optional layer was missing would throw away a usable
answer (NFR-5).

A *requested* skip is not a degradation: `enrich=false` settles to `done`,
because the caller got exactly what they asked for.

### `DELETE /analysis/{job_id}`

`204`. Work already running is not interrupted — there is no safe way to kill a
thread mid-GDAL-read — but the record goes, so nothing is served from it.

---

## Suitability

### `GET /suitability/weights`

The AHP weight vector the model ships with, the per-tier vectors derived from it,
and the **consistency audit** of the judgements it encodes.

```bash
curl -s http://localhost:8000/api/v1/suitability/weights | jq '.audit.consistency'
```

```json
{ "lambda_max": 9.10583, "consistency_index": 0.01323,
  "consistency_ratio": 0.00912, "random_index": 1.45,
  "threshold": 0.1, "is_consistent": true }
```

Nine hardcoded numbers are unfalsifiable — a reader can disagree with them but
cannot show they are *incoherent*. This reconstructs the pairwise matrix they
imply, snaps it to the scale an expert would have used, and re-derives the
weights from it. CR = 0.009 against Saaty's 0.10 threshold, so they hold.

The audit also caught an arithmetic slip: the elicited table sums to 1.05 rather
than 1.00. The exported vector is normalised, which is ratio-preserving and so
changes no score or ranking — but the effective weight on flow accumulation is
0.200, not the 0.21 that was tabulated, and a report should quote the number the
model used.

### `POST /suitability/weights/ahp`

Derive weights from your own pairwise comparisons.

```bash
curl -sX POST http://localhost:8000/api/v1/suitability/weights/ahp \
  -H 'Content-Type: application/json' \
  -d '{"criteria":["flow","slope","depth"],
       "matrix":[[1,2,4],[0.5,1,2],[0.25,0.5,1]]}' | jq '.weights'
```

```json
{ "flow": 0.5714, "slope": 0.2857, "depth": 0.1429 }
```

**An inconsistent matrix is refused with `400`.** Judging A twice as important as
B, B twice as important as C, and C more important than A is a contradiction no
weight vector can express; CR detects it, and returning weights anyway would
dress a contradiction up as a recommendation. The refusal carries the ratio, so
it can be acted on:

```json
{ "type": "/errors/validation", "consistency_ratio": 6.13027, "threshold": 0.1,
  "detail": "the pairwise judgements are inconsistent: CR = 6.130 ..." }
```

Agreement between the eigenvector and the textbook row-mean is reported as a
cross-check, but note it checks the *arithmetic*, not the consistency: a
symmetric cycle sends both methods to identical equal weights while CR exceeds 6.

### `POST /suitability/analyze`

Run an analysis with your own weights. `202` and a `job_id`, as
`POST /analysis`.

```bash
curl -sX POST http://localhost:8000/api/v1/suitability/analyze \
  -F contour_map=@contours_1m.kml \
  -F 'weights_json={"weights":{"flow_accumulation":0.05,"slope":0.30,
       "depression_depth":0.25,"soil_runoff_potential":0.10,
       "land_availability":0.10,"distance_to_stream":0.05,
       "plan_concavity":0.05,"distance_to_settlement":0.05,
       "distance_to_waterbody":0.05}}'
```

`weights_json` takes either a vector or `{"criteria": [...], "matrix": [[...]]}`
and derives the weights itself. Both forms are validated **before any work
starts** — a `400` in 10 ms rather than after a 24-second analysis. A vector must
cover every criterion the model knows, because which subset goes live depends on
whether the providers answer, and that is not knowable until enrichment has run.

Weighting slope and depression depth heavily changes the answer, which is the
whole point: the default run recommends a `channel_position` at 84.0/100, and
that vector recommends a `natural_depression` at 88.2/100 instead.

### `GET /suitability/{job_id}/compare?ranks=1,2,3`

Two to five sites side by side (**FR-12**). A decision-shaped comparison, not a
second copy of the numbers `/sites` already returns.

```bash
curl -s "http://localhost:8000/api/v1/suitability/$JOB/compare?ranks=1,2,3" \
  | jq '.leads_on_count, .trade_offs[].what_that_means'
```

It reports three things no single site's payload can:

* **Who leads on each metric, and by how much**, with the direction stated —
  higher is better for capacity, worse for cost.
* **Two derived metrics**: the share of its catchment's yield the pond can
  actually hold, and cost per cubic metre of *live* storage. On the sample sheet
  one site holds 30 % of its yield and another 1 %, which is the difference
  between a well-matched pond and one dwarfed by its own catchment — and neither
  figure exists in either site's own response.
* **The trade-off in words.** A site can lose on capacity and still be the right
  choice when its binding constraint is one you can fix.

**A metric whose spread is under 1 % picks no winner** and is flagged `uniform`.
Cost per cubic metre of gross capacity is identical across sites by construction
— cost is excavated volume times a flat rate — so declaring a winner on it would
invite a decision based on an artefact of the cost model.

Scores are normalised across the candidate set of one analysis, so a 72 here does
not mean the same as a 72 from a different run; the response says so.

---

### `GET /suitability/{job_id}/sites`

The ranked-sites projection of a finished job: ranking, scores and per-criterion
contributions, without the contour geometry and rainfall series a client
comparing sites does not need.

```bash
curl -s http://localhost:8000/api/v1/suitability/$JOB/sites \
  | jq '.sites[0].criteria_breakdown'
```

```json
[ { "criterion": "slope", "raw_value": 0.079, "normalised": 0.993,
    "weight": 0.4, "contribution": 0.397 },
  { "criterion": "depression_depth", "raw_value": 2.951, "normalised": 0.984,
    "weight": 0.333, "contribution": 0.328 } ]
```

The contributions sum to the score, so the ranking can be checked rather than
trusted. `analysis_tier` travels with the ranking because scores are not
comparable across tiers — a different set of criteria produced them.

---

## `GET /export/{job_id}?format=geojson`

Every result layer as one file, ready to open in QGIS.

```bash
curl -s "http://localhost:8000/api/v1/export/$JOB?format=geojson" \
  -o analysis.geojson
jq '.properties' analysis.geojson
```

```json
{ "analysis_id": "b5984c64b23e4eff",
  "analysis_tier": "full",
  "layers": ["candidate_site", "catchment", "contour", "pond_footprint",
             "survey_extent"],
  "feature_count": 1371 }
```

Every feature carries a `layer` property, which is what QGIS and `geopandas`
group by; without it a reader has to reconstruct a grouping the API already knew.
The CRS is stated explicitly rather than left to the GeoJSON default, because the
file is meant to be read months later by someone who was not told.

The pond footprint is a north-aligned rectangle of the right dimensions, and its
`orientation` property says it is indicative: the design fixes plan dimensions
but nothing in the model chooses a bearing, and a polygon in a GIS file looks
surveyed whether or not it is.

Contours are present only if the analysis was asked for them
(`include_contours=true`). Available for `partial` as well as `done`.

---

## Reports

### `POST /reports/generate`

Renders a finished analysis as an A4 PDF and returns the id to fetch it by.

```bash
curl -sX POST http://localhost:8000/api/v1/reports/generate \
  -F job_id=$JOB | jq
```

```json
{
  "report_id": "7d18a95b83774bed",
  "job_id": "9ece60afd3b5434b977d3f105217a889",
  "download_url": "/api/v1/reports/7d18a95b83774bed/download",
  "filename": "pond-siting-report-9ece60af.pdf",
  "size_bytes": 813890,
  "pages_hint": "4 pages of A4"
}
```

Two steps rather than one because rendering a page of vector contours takes a
couple of seconds, and because a report is a *document* with an identity — it can
be fetched twice, linked to, or downloaded by someone who did not run the
analysis.

**What is in it.** The map (drawn by matplotlib from the analysis itself, so no
basemap tiles and no network are needed), the catchment and rainfall figures, the
per-criterion score breakdown whose contributions sum to the score, the data
sources with their licences — and a limitations section that is not an appendix.

That last part is why this is not a JSON dump. A PDF outlives the API response
that explained itself and gets forwarded to people who never saw the tool, so the
caveats travel with the numbers: the terrain is interpolated between contours and
cannot show a hollow smaller than the interval; the pond footprint has no
orientation, because the design fixes plan dimensions and nothing chooses a
bearing; land tenure is not modelled, since no open dataset carries village-level
ownership; and the cost is one excavation rate applied to a volume, with no
lead-in, lining or acquisition.

Available for `partial` analyses too, with the missing layer stated on the first
page rather than left for the reader to notice.

### `GET /reports/{report_id}/download`

The PDF as an attachment. Reports are held in memory and bounded to the most
recent few; the durable artefact is the analysis, which can always be rendered
again.

Rendering needs WeasyPrint, which draws text through Pango and rasterises
through Cairo — so the Python wheel is not self-contained. The API image installs
those; a deployment without them answers `422` naming the missing renderer rather
than failing at import and refusing to boot.

---

## `GET /health` and `GET /health/ready`

`/health` never touches a backing service, so a container is not killed while
waiting for its database. `/health/ready` probes PostGIS and Redis and lists
which optional layers are configured; it returns `503` when a required
dependency is down. Redis is optional: with `REDIS_URL` empty, as on the lab
systems, a job is kept by the API process that runs it (the gateway sends each
browser back to that process), and the check reads `not_configured` rather than
`down`.

### `GET /health/features`

What this server can offer the page, always with `200` — a readiness probe that
answers `503` is right for an orchestrator and wrong for a browser, which logs
every `503` as an error. The workspace asks once per load and hides village
search when there is no village register database — here, a server without
one:

```bash
curl -s localhost:8000/api/v1/health/features
```

```json
{"village_search": {"available": false, "reason": "no village register database"},
 "analyses": {"limit": 1, "running": 0, "waiting": 0},
 "max_area_km2": 100.0, "max_upload_mb": 50}
```

`analyses` is the overload guard's state: how many analyses this instance runs at
once, how many are running, and how many jobs are waiting for a slot.

---

## Errors

Every error is [RFC 7807](https://datatracker.ietf.org/doc/html/rfc7807) problem
details, so there is one shape to handle:

```json
{
  "type": "/errors/unanswerable",
  "title": "Request cannot be answered",
  "status": 422,
  "detail": "no contour LineStrings found. Expected <Placemark> elements containing <LineString><coordinates>; check that the file is a contour export rather than points or polygons.",
  "instance": "/api/v1/analyzeContour",
  "trace_id": "b11408329271"
}
```

| Status | When |
|---|---|
| `400` | Malformed request — wrong extension, empty file, option out of range (`errors[]` names the field) |
| `404` | Unknown `dem_id` |
| `413` | Upload over 50 MB |
| **`422`** | **Well-formed request, but the file's contents make it unanswerable** |
| `503` | Every provider in a fallback chain failed |

The `400` / `422` distinction is deliberate: `422` means the upload succeeded and
the *contents* cannot be analysed, so the UI can show the parser's actual reason
instead of "invalid input". Examples: no contour LineStrings; all contours at one
elevation (no relief); coordinates outside lat/lon range (often a projected CRS,
or lat/lon transposed).

Errors carry a `trace_id` that matches the server log line.

---

## Limits

| | |
|---|---|
| Upload size | 50 MB, enforced while streaming — the read aborts, it does not buffer first |
| KMZ expansion | 250 MB, 100 entries (zip-bomb guard) |
| XML | entity expansion and external entities blocked (`defusedxml`) |
| Grid | 20 M cells; the *derived* resolution never approaches this |
| Enrichment | 20 s budget across all providers |
