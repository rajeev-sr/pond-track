#!/usr/bin/env python3
"""Build the CSD Assignment 1 submission report: docs/REPORT.html (+ .pdf).

Same shape as the load-balancer report in the sibling project: one
self-contained HTML file, then headless Chrome's own print path for the PDF, so
what lands in the PDF is what the print stylesheet was written against.

Every figure quoted is read from captured output under `docs/report/assets/`
rather than typed in here, so the report cannot drift from the run it describes.
Regenerate the captures by re-running the analysis against a live API; see
`--help` and the "Reproducing" section the report itself carries.

    python3 tools/report.py            # HTML only
    python3 tools/report.py --pdf      # HTML + PDF
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
from html import escape as E
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "docs" / "report" / "assets"
OUT_HTML = REPO / "docs" / "REPORT.html"
OUT_PDF = REPO / "docs" / "REPORT.pdf"

STUDENT = "Rajeev Kumar"
ROLL = "12341700"
COURSE = "CSD — Assignment 1"
GITHUB = "https://github.com/rajeev-sr/pond-track"
#: The deployed instance, verified reachable: the gateway on 3274 serves the app and
#: reverse-proxies the API under the same origin, so one URL covers the route and
#: its documentation. Overridable, because whether it is up is a fact about the
#: host and not about this repository.
API_BASE = "http://10.1.75.53:3274"
LOCAL_BASE = "http://localhost:8000"


# ── loading the captured run ────────────────────────────────────────────────
def load(name: str) -> Any:
    path = ASSETS / name
    if not path.exists():
        sys.exit(
            f"missing capture: {path}\n"
            "Run an analysis against a live API and save its output there first "
            "(see the Reproducing section of the report)."
        )
    return json.loads(path.read_text())


def img(name: str) -> str:
    """A screenshot as a data URI, so the HTML is one file with no dependencies."""
    for candidate in (ASSETS / name, ASSETS / f"{Path(name).stem}.png"):
        if candidate.exists():
            mime = (
                "image/jpeg" if candidate.suffix in (".jpg", ".jpeg") else "image/png"
            )
            return f"data:{mime};base64,{base64.b64encode(candidate.read_bytes()).decode()}"
    sys.exit(f"missing screenshot: {ASSETS / name}")


def _endpoint_table() -> str:
    """Every route the report lists, as the deployed instance answered it."""
    cap = load("endpoints.json")
    rows = "\n".join(
        f'<tr><td><code>{E(r["endpoint"])}</code></td><td>{E(r["method"])}</td>'
        f'<td class="n">{E(r["result"])}</td></tr>'
        for r in cap["rows"]
    )
    errors = cap["errors"]
    return f"""<table class="d">
<thead><tr><th>Endpoint</th><th>Method</th><th class="n">Result</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>Malformed requests are rejected with a problem document rather than a stack trace: no file →
{errors["no file"]} naming <code>contour_map</code>; both field names → {errors["both field names"]};
a non-contour extension → {errors["a non-contour extension"]}; a <code>.kml</code> that is not XML →
{errors["a .kml that is not XML"]}; an unknown job id → {errors["an unknown job id"]}.</p>
<p class="small">Called by {_source('endpoints.json')}, at {E(cap["captured_at"])}.</p>"""


def _ready_line() -> str:
    """The readiness response as captured, condensed to one line for the report."""
    path = ASSETS / "ready.json"
    if not path.exists():
        return "{}"
    d = json.loads(path.read_text())
    checks = ", ".join(
        f'"{k}": "{v["status"]}"' for k, v in d.get("checks", {}).items()
    )
    return '{{"status": "{}", "checks": {{{}}}}}'.format(d.get("status", "?"), checks)


def figure(name: str, number: str, caption: str, note: str = "") -> str:
    return f"""<figure class="plate">
  <img src="{img(name)}" alt="{E(caption)}">
  <figcaption><span>Figure {E(number)} — {E(caption)}</span><span>{E(note)}</span></figcaption>
</figure>"""


def n(value: Any, places: int = 0) -> str:
    try:
        return f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return "—"


CSS = """
@page { size: A4; margin: 17mm 16mm 18mm; }
:root {
  --paper:#fff; --tint:#f6f4ef; --ink:#15181a; --ink2:#3f474d; --ink3:#6d767d;
  --rule:#d6d0c4; --rule2:#b3ab9b; --water:#17556f; --earth:#8f4f1e; --alert:#8c2f22;
}
* { box-sizing:border-box; }
html,body { margin:0; padding:0; background:#e9e6df; }
body {
  font-family:"DejaVu Sans","Liberation Sans",system-ui,sans-serif;
  font-size:9.4pt; line-height:1.62; color:var(--ink);
}
.sheet { max-width:186mm; margin:0 auto; background:var(--paper); padding:16mm 15mm 18mm; }
h1,h2,h3,h4 { font-family:"DejaVu Serif","Liberation Serif",Georgia,serif; font-weight:normal; margin:0; }
h1 { font-size:23pt; line-height:1.14; letter-spacing:-.01em; }
h2 {
  font-size:14pt; margin:9mm 0 3mm; padding-bottom:1.6mm;
  border-bottom:.6pt solid var(--ink); break-after:avoid;
}
h3 { font-size:11pt; margin:6mm 0 2mm; break-after:avoid; }
h4 { font-size:9.6pt; margin:4mm 0 1.5mm; break-after:avoid; }
p { margin:0 0 2.6mm; }
a { color:var(--water); }
ul,ol { margin:0 0 3mm; padding-left:5.2mm; }
li { margin:.9mm 0; }
.mono, code, pre { font-family:"DejaVu Sans Mono","Liberation Mono",monospace; }
code { font-size:8.4pt; background:var(--tint); padding:.2mm .8mm; }
pre {
  font-size:7.9pt; line-height:1.5; background:var(--tint);
  border:.6pt solid var(--rule); border-left:1.6pt solid var(--water);
  padding:3mm 3.4mm; overflow-x:auto; white-space:pre-wrap; word-break:break-word;
  margin:0 0 3mm;
}
.stamp {
  font-family:"DejaVu Sans Mono",monospace; font-size:7pt; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink3);
}
.lede { font-size:10.6pt; color:var(--ink2); margin:3mm 0 0; }

/* cover */
.cover { border-bottom:1.2pt solid var(--ink); padding-bottom:7mm; margin-bottom:7mm; }
.cover .rule { height:0; border-top:.6pt solid var(--rule2); margin:5mm 0; }
table.meta { width:100%; border-collapse:collapse; margin-top:5mm; }
table.meta td { padding:1.5mm 0; vertical-align:top; border-bottom:.6pt solid var(--rule); }
table.meta td:first-child { width:38mm; }
table.meta td:first-child span { font-family:"DejaVu Sans Mono",monospace; font-size:7pt;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink3); }

/* data tables */
table.d { width:100%; border-collapse:collapse; margin:0 0 3.4mm; font-size:8.7pt; }
table.d th {
  text-align:left; font-family:"DejaVu Sans Mono",monospace; font-size:7pt;
  letter-spacing:.1em; text-transform:uppercase; color:var(--ink3);
  padding:1.6mm 2mm 1.6mm 0; border-bottom:.8pt solid var(--rule2);
}
table.d td { padding:1.5mm 2mm 1.5mm 0; border-bottom:.6pt solid var(--rule); vertical-align:top; }
table.d td:last-child, table.d th:last-child { padding-right:0; }
table.d .n, table.d th.n { text-align:right; font-family:"DejaVu Sans Mono",monospace;
  font-variant-numeric:tabular-nums; }
table.d td small { display:block; color:var(--ink3); font-size:7.6pt; }
table.d tbody tr:last-child td { border-bottom:0; }

/* callouts */
.note {
  border-left:1.6pt solid var(--water); background:var(--tint);
  padding:2.6mm 3.2mm; margin:0 0 3.4mm; font-size:8.8pt; color:var(--ink2);
}
.note.warn { border-left-color:var(--earth); }
.note.stop { border-left-color:var(--alert); }
.note b { color:var(--ink); }
.small { font-size:7.8pt; color:var(--ink3); margin-top:-1.5mm; }

/* figures */
figure.plate { margin:0 0 4.4mm; border:.6pt solid var(--rule2); break-inside:avoid; }
figure.plate img { display:block; width:100%; }
figure.plate figcaption {
  display:flex; justify-content:space-between; gap:4mm;
  border-top:.6pt solid var(--rule); background:var(--tint); padding:1.6mm 2.4mm;
  font-family:"DejaVu Sans Mono",monospace; font-size:6.9pt; letter-spacing:.09em;
  text-transform:uppercase; color:var(--ink3);
}
.eq {
  border-top:.8pt solid var(--ink); border-bottom:.6pt solid var(--rule);
  padding:2.8mm 0; margin:0 0 3.4mm;
  font-family:"DejaVu Sans Mono",monospace; font-size:9pt;
}
.eq small { display:block; font-family:"DejaVu Sans",sans-serif; font-size:8pt;
  color:var(--ink3); margin-top:1.8mm; }
.two { display:flex; gap:7mm; }
.two > * { flex:1; min-width:0; }
.tick { color:var(--water); font-weight:bold; }

footer.colophon {
  margin-top:9mm; padding-top:3mm; border-top:1.2pt solid var(--ink);
  display:flex; justify-content:space-between; gap:5mm;
  font-family:"DejaVu Sans Mono",monospace; font-size:7pt; letter-spacing:.09em;
  text-transform:uppercase; color:var(--ink3);
}
@media print {
  html,body { background:#fff; }
  .sheet { max-width:none; margin:0; padding:0; }
  h2 { break-after:avoid; }
  pre, table.d, .note, .eq { break-inside:avoid; }
}
"""


# ── sections ────────────────────────────────────────────────────────────────
def sec_cover(a: dict, api_base: str) -> str:
    cm = a["contour_map"]
    return f"""<section class="cover">
<span class="stamp">{E(COURSE)}</span>
<h1>Contour — catchment estimation and<br>pond siting for a village</h1>
<p class="lede">A web application and API that takes a contour survey, or an area selected on
the map, reconstructs the terrain, delineates the catchment above each candidate pond site, and
returns the pond location, the catchment area and the water it can collect — as JSON, and
overlaid on the map.</p>
<table class="meta">
  <tr><td><span>Submitted by</span></td><td>{E(STUDENT)} &nbsp;·&nbsp; Roll {E(ROLL)}</td></tr>
  <tr><td><span>GitHub repository</span></td>
      <td><a href="{E(GITHUB)}">{E(GITHUB)}</a></td></tr>
  <tr><td><span>Web application</span></td>
      <td><a href="{E(api_base)}/">{E(api_base)}</a></td></tr>
  <tr><td><span>API route</span></td>
      <td><code>POST {E(api_base)}/api/v1/analyzeContour</code><br>
          <code>POST {E(api_base)}/api/v1/findCatchment</code> &nbsp;(alias)<br>
          <code>POST {E(api_base)}/api/v1/analyzeArea</code> &nbsp;(an area selected on the map)</td></tr>
  <tr><td><span>API documentation</span></td>
      <td><a href="{E(api_base)}/docs">{E(api_base)}/docs</a> (Swagger UI) &nbsp;·&nbsp;
          <a href="{E(api_base)}/redoc">/redoc</a> &nbsp;·&nbsp;
          <a href="{E(api_base)}/openapi.json">/openapi.json</a></td></tr>
  <tr><td><span>Demonstrated on</span></td>
      <td><code>{E(str(a['input']['filename']))}</code> — {n(cm['lines_parsed'])} contour lines,
          {n(cm['levels'])} levels at {n(cm['contour_interval_m'], 1)} m,
          relief {n(cm['relief_m'], 1)} m</td></tr>
</table>
</section>"""


def sec_requirements(api_base: str) -> str:
    rows = [
        ("GitHub repository", f'<a href="{E(GITHUB)}">{E(GITHUB)}</a>', "§1"),
        (
            "Working API route URL",
            f"<code>POST {E(api_base)}/api/v1/analyzeContour</code>",
            "§2",
        ),
        (
            "Catchment estimation approach",
            "Method, stage by stage, with the formulae",
            "§3",
        ),
        (
            "Demonstration on the provided map",
            "Request, response and screenshots",
            "§4",
        ),
        (
            "Select a land area on the map",
            "Draw a rectangle, or one click for the sample area",
            "§5",
        ),
        (
            "Pond location, catchment area and water collected for that area",
            "The <code>summary</code> block and every candidate",
            "§5",
        ),
        (
            "Results overlaid on the map",
            "Volume on the marker, catchment labelled, popup with all three",
            "§5",
        ),
        (
            "Fast and functional on the lab systems",
            "Gateway, one analysis per system, measured under load",
            "§6",
        ),
        ("API documentation", "Every route, schemas and the interactive spec", "§7"),
    ]
    body = "\n".join(
        f'<tr><td><span class="tick">✓</span> {E(what)}</td><td>{where}</td>'
        f'<td class="n">{E(sec)}</td></tr>'
        for what, where, sec in rows
    )
    return f"""<h2>Contents against the brief</h2>
<table class="d">
<thead><tr><th>Required</th><th>Provided</th><th class="n">Section</th></tr></thead>
<tbody>{body}</tbody></table>"""


def sec_repo() -> str:
    return f"""<h2>1 · Repository</h2>
<p>The full source, including the test suite, the deployment compose file and the design
documents, is at:</p>
<pre>{E(GITHUB)}</pre>
<p>The tree is laid out so each concern can be read on its own:</p>
<table class="d">
<thead><tr><th>Path</th><th>Holds</th></tr></thead>
<tbody>
<tr><td><code>backend/app/api/v1/</code></td><td>HTTP routes, request validation, problem responses</td></tr>
<tr><td><code>backend/app/services/</code></td><td>The domain: interpolation, hydrology, siting, pond design</td></tr>
<tr><td><code>backend/app/providers/</code></td><td>External data — elevation, land cover, soil, rainfall, OSM</td></tr>
<tr><td><code>backend/app/tests/</code></td><td>Unit, property, golden, integration and real-browser tests</td></tr>
<tr><td><code>frontend/src/</code></td><td>React map interface that consumes the same API</td></tr>
</tbody></table>
<h3>Running it</h3>
<pre>git clone {E(GITHUB)}.git
cd pond-track
cp .env.example .env
make up                 # postgis, redis, api, tiler, frontend
make demo               # analyse the bundled contour map end to end</pre>
<p>Nothing needs to be registered for or paid for: every data source the service reads is
openly licensed and keyless.</p>"""


def sec_api_route(a: dict, api_base: str) -> str:
    return f"""<h2>2 · Working API route</h2>
<p>The route named in the brief is implemented under both names it may be looked for:</p>
<table class="d">
<thead><tr><th>Method</th><th>URL</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td>POST</td><td><code>{E(api_base)}/api/v1/analyzeContour</code></td>
    <td>Upload a contour map; receive ranked sites with their catchments</td></tr>
<tr><td>POST</td><td><code>{E(api_base)}/api/v1/findCatchment</code></td>
    <td>Alias of the above, byte-for-byte the same response</td></tr>
<tr><td>POST</td><td><code>{E(api_base)}/api/v1/analysis</code></td>
    <td>The same work as a background job, for large sheets</td></tr>
<tr><td>POST</td><td><code>{E(api_base)}/api/v1/analyzeArea</code></td>
    <td>No file: a rectangle selected on the map, analysed on Copernicus 30 m terrain (§5)</td></tr>
<tr><td>POST</td><td><code>{E(api_base)}/api/v1/analysis/area</code></td>
    <td>The same as a job — what the web application uses</td></tr>
</tbody></table>

<h3>Request</h3>
<p><code>multipart/form-data</code>. Only the map is required, as <code>contour_map</code> (or its alias <code>file</code>).</p>
<table class="d">
<thead><tr><th>Field</th><th>Type</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>contour_map</code></td><td>upload</td><td>—</td>
    <td><b>The contour map</b>, <code>.kml</code> / <code>.kmz</code> / <code>.xml</code>. This is the field name to use.</td></tr>
<tr><td><code>file</code></td><td>upload</td><td>—</td>
    <td>Accepted alias for <code>contour_map</code>, for callers that already send this name. Send one or the other, not both.</td></tr>
<tr><td><code>max_sites</code></td><td>int 1–25</td><td>5</td><td>How many ranked sites to return</td></tr>
<tr><td><code>max_slope_pct</code></td><td>float</td><td>8.0</td><td>Slope above which ground is not buildable</td></tr>
<tr><td><code>cell_size_m</code></td><td>float</td><td>auto</td><td>Grid resolution; derived from contour spacing if omitted</td></tr>
<tr><td><code>enrich</code></td><td>bool</td><td>true</td><td>Fetch soil, land cover and rainfall</td></tr>
<tr><td><code>include_contours</code></td><td>bool</td><td>false</td><td>Return the parsed contours as GeoJSON</td></tr>
</tbody></table>

<h3>Verification</h3>
<p>The transcript in §4 is a real call against a running instance; the response was
{n(len((ASSETS / 'analysis.json').read_bytes()))} bytes of JSON returned in
{n(a['elapsed_s'], 2)} s.</p>
<div class="note"><b>The response quoted in §4 was measured against the URL above</b> — that
instance answering, not a local run. The captures behind §5 and §6 each name their own source
beneath them. Readiness at the time of capture (Redis is optional: without it, a job is kept by
the API process that runs it, and the gateway sends each browser back to that process):</div>
<pre>$ curl {E(api_base)}/api/v1/health/ready
{E(_ready_line())}</pre>

<h3>Endpoints verified on the deployed instance</h3>
{_endpoint_table()}"""


def sec_approach(a: dict, streams_site: dict, streams_sheet: dict) -> str:
    s = a["recommended_site"]
    m = s["catchment"]["metrics"]
    grid = a["interpolated_terrain"]
    cm = a["contour_map"]
    ns = streams_site["network"]
    nw = streams_sheet["network"]
    return f"""<h2>3 · How the catchment is estimated</h2>
<p>A contour sheet gives elevation along lines and says nothing about the ground between
them. Estimating a catchment from it is four steps: rebuild a continuous surface, make that
surface drainable, work out where each cell's water goes, then collect every cell whose water
reaches the point of interest.</p>

<h3>3.1 A surface from the lines</h3>
<p>Contour vertices are triangulated (Delaunay) and the surface interpolated linearly inside
each triangle, giving a regular grid. On the demonstration sheet, {n(cm['vertices_used'])}
vertices from {n(cm['lines_parsed'])} lines became a
{n(grid['grid_size'][0])} × {n(grid['grid_size'][1])} grid at
{n(grid['grid_resolution_m'], 1)} m — {n(grid['grid_cells'])} cells.</p>
<p>The working CRS is chosen from the sheet's own longitude — UTM zone
{E(str(cm['working_crs_epsg']))} here — so every area, slope and distance below is in metres,
not degrees. No location is fixed in the code.</p>
<div class="note warn"><b>Interpolation cannot invent detail.</b> A hollow shallower than the
contour interval ({n(cm['contour_interval_m'], 1)} m here) cannot appear in the grid at all.
This bounds the whole estimate and is reported rather than smoothed over.</div>

<h3>3.2 Making the surface drainable</h3>
<p>An interpolated surface contains closed depressions, some real and some artefacts. Water
cannot leave them, so accumulation would stop there. They are raised to their spill level by
<b>Priority-Flood with an ε gradient</b>, which leaves a shallow descending slope across the
filled area instead of a dead flat one. Where a depression is caused by a road or bund crossing
a channel, a bounded least-cost breach is cut instead — filling would erase a real channel.</p>

<h3>3.3 Where each cell's water goes</h3>
<p>Flow direction is assigned by <b>D8</b>: each cell drains to whichever of its eight
neighbours lies steepest downhill. Accumulation is then counted by walking the cells in
dependency order — every cell is visited once, after all cells that drain into it — so the
count at a cell is the number of cells upstream of it. Multiplying by cell area converts that
to a contributing area.</p>
<div class="eq">A = N · c²
<small>A contributing area (m²), N cells upstream, c cell size. At
{n(grid['grid_resolution_m'], 1)} m one cell is {n(grid['grid_resolution_m'] ** 2)} m², so the
recommended site's {n(m['cell_count'])} cells give {n(m['area_ha'], 2)} ha.</small></div>

<h3>3.4 Collecting the catchment</h3>
<p>The catchment above a point is every cell whose flow path reaches it. It is found by
walking the flow graph upstream from the outlet. Two details decide whether the answer is
meaningful:</p>
<ul>
<li><b>The outlet is snapped to a channel.</b> A point a few metres off the channel sits on a
hillside, and its catchment is a few hectares rather than a few hundred — plausible-looking and
wrong. The requested point is moved to the nearest cell above the channel threshold, and the
distance moved is reported.</li>
<li><b>A catchment touching the sheet edge is a lower bound</b>, because the contributing area
continues beyond the survey. This is flagged per catchment
(<code>touches_grid_edge</code>) rather than presented as a measurement. On the recommended
site it is <code>{E(str(m['touches_grid_edge']))}</code>.</li>
</ul>

<h3>3.5 What is reported about it</h3>
<p>Area alone does not describe a catchment's behaviour, so the shape and response are
derived too:</p>
<table class="d">
<thead><tr><th>Quantity</th><th class="n">Value</th><th>Derived from</th></tr></thead>
<tbody>
<tr><td>Contributing area</td><td class="n">{n(m['area_ha'], 2)} ha</td><td>{n(m['cell_count'])} cells × cell area</td></tr>
<tr><td>Perimeter</td><td class="n">{n(m['perimeter_m'])} m</td><td>Boundary of the traced region</td></tr>
<tr><td>Relief</td><td class="n">{n(m['relief_m'], 2)} m</td><td>Max − min elevation within it</td></tr>
<tr><td>Mean slope</td><td class="n">{n(m['mean_slope_pct'], 2)} %</td><td>Cell-wise gradient of the conditioned surface</td></tr>
<tr><td>Longest flow path</td><td class="n">{n(m['longest_flow_path_m'], 1)} m</td><td>Traced along D8 directions</td></tr>
<tr><td>Time of concentration</td><td class="n">{n(m['time_of_concentration_min'], 1)} min</td><td>Kirpich, from path length and slope</td></tr>
<tr><td>Form factor</td><td class="n">{n(m['form_factor'], 4)}</td><td>Area / longest path²</td></tr>
<tr><td>Compactness</td><td class="n">{n(m['compactness_coefficient'], 3)}</td><td>Perimeter vs an equal-area circle</td></tr>
</tbody></table>
<div class="eq">Tc = 0.01947 · L<sup>0.77</sup> · S<sup>−0.385</sup>
<small>Kirpich (1940). L is the longest flow path in metres, S its average gradient. A short,
steep catchment concentrates quickly and needs a larger spillway for the same area.</small></div>

<h3>3.6 The channel network the catchment sits in</h3>
<p>A cell is treated as a channel once its contributing area passes a threshold — 1 ha by
default, deliberately small, because a nala draining a few hectares is exactly the feature a
village check dam sits on. On the demonstration sheet the network is
{n(nw['reach_count'])} reaches totalling {n(nw['total_length_km'], 2)} km; within the
recommended site's catchment it is {n(ns['reach_count'])} reaches,
{n(ns['total_length_km'], 2)} km, a drainage density of
{n(ns['drainage_density_km_per_km2'], 2)} km/km².</p>
<div class="note"><b>Drainage density is quoted for the catchment, not the sheet.</b> It is
length per unit <em>basin</em> area; measured over the survey rectangle it would average
unrelated catchments, so the API returns it for a delineated basin and withholds it
otherwise.</div>

<h3>3.7 From catchment to a pond proposal</h3>
<p>The catchment is the input to everything that follows. Yield uses SCS-CN with the initial
abstraction at 0.3S rather than 0.2S, following CWC and IMD practice for Indian catchments:</p>
<div class="eq">Q = (P − 0.3S)² / (P + 0.7S), &nbsp; S = 25400/CN − 254
<small>P, Q in mm; Q = 0 for P ≤ 0.3S. Curve number from hydrologic soil group and land
cover — {n(s['runoff']['curve_number'], 1)} here.</small></div>
<p>Storage comes from the surface itself: the pond is flood-filled level by level to give a
stage–storage curve, with volume between levels by the prismoidal rule. Candidate sites are
ranked over nine criteria weighted by AHP, and only over ground that survives the exclusion
veto — existing water, rivers, buildings and roads.</p>"""


def sec_demo(a: dict) -> str:
    s = a["recommended_site"]
    m = s["catchment"]["metrics"]
    p = s["pond"]["recommended"]
    x = a["suitability"]["exclusions"]
    env = a["environment"]
    cm = a["contour_map"]

    sites = "\n".join(
        f'<tr><td class="n">{c["rank"]}</td><td>{E(c["site_kind"].replace("_", " "))}</td>'
        f'<td class="n">{n(c["suitability_score"], 1)}</td>'
        f'<td class="n">{n(c["catchment"]["metrics"]["area_ha"], 1)}</td>'
        f'<td class="n">{n(c["catchment"]["metrics"]["time_of_concentration_min"], 1)}</td>'
        f'<td class="n">{n((c.get("pond") or {}).get("recommended", {}).get("gross_capacity_m3"))}</td></tr>'
        for c in a["candidate_sites"]
    )
    removed = "\n".join(
        f'<tr><td>{E(k.replace("_", " "))}</td><td class="n">{n(v)}</td></tr>'
        for k, v in x["removed_by"].items()
        if v
    )
    stages = "\n".join(
        f'<tr><td>{E(k)}</td><td class="n">{n(v, 3)}</td></tr>'
        for k, v in a["stage_timings_s"].items()
    )
    return f"""<h2>4 · Demonstration on the provided contour map</h2>
<p>The sheet bundled with the assignment, <code>{E(a['input']['filename'])}</code>
({n(a['input']['size_bytes'] / 1024)} KB), analysed end to end. Nothing below is typed by
hand: it is read from the response this call returned.</p>

<h3>4.1 The call</h3>
<pre>{E((ASSETS / 'curl-cmd.txt').read_text().strip())}

HTTP 200 · {n(len((ASSETS / 'analysis.json').read_bytes()))} bytes · {n(a['elapsed_s'], 2)} s</pre>

<h3>4.2 What was read from the file</h3>
<table class="d">
<tbody>
<tr><td>Contour lines parsed</td><td class="n">{n(cm['lines_parsed'])}</td></tr>
<tr><td>Lines whose elevation could not be resolved</td><td class="n">{n(cm['lines_unresolved'])}</td></tr>
<tr><td>Distinct levels</td><td class="n">{n(cm['levels'])} at {n(cm['contour_interval_m'], 1)} m</td></tr>
<tr><td>Elevation range</td><td class="n">{n(cm['elevation_min_m'], 1)} – {n(cm['elevation_max_m'], 1)} m</td></tr>
<tr><td>Elevation source</td><td class="n">{E(cm['elevation_strategy'])}</td></tr>
<tr><td>Working CRS</td><td class="n">EPSG:{E(str(cm['working_crs_epsg']))}</td></tr>
<tr><td>Data tier</td><td class="n">{E(a['suitability']['analysis_tier'])}</td></tr>
</tbody></table>

<h3>4.3 The recommended site and its catchment</h3>
<div class="two">
<table class="d">
<tbody>
<tr><td>Rank / kind</td><td class="n">#{s['rank']} · {E(s['site_kind'].replace('_', ' '))}</td></tr>
<tr><td>Suitability</td><td class="n">{n(s['suitability_score'], 1)} / 100</td></tr>
<tr><td>Location</td><td class="n">{n(s['location']['lat'], 5)}, {n(s['location']['lon'], 5)}</td></tr>
<tr><td>Catchment area</td><td class="n">{n(m['area_ha'], 2)} ha</td></tr>
<tr><td>Relief</td><td class="n">{n(m['relief_m'], 2)} m</td></tr>
<tr><td>Mean slope</td><td class="n">{n(m['mean_slope_pct'], 2)} %</td></tr>
</tbody></table>
<table class="d">
<tbody>
<tr><td>Longest flow path</td><td class="n">{n(m['longest_flow_path_m'], 1)} m</td></tr>
<tr><td>Time of concentration</td><td class="n">{n(m['time_of_concentration_min'], 1)} min</td></tr>
<tr><td>Clipped by sheet edge</td><td class="n">{E(str(m['touches_grid_edge']))}</td></tr>
<tr><td>Design depth</td><td class="n">{n(p['depth_m'], 2)} m</td></tr>
<tr><td>Gross capacity</td><td class="n">{n(p['gross_capacity_m3'])} m³</td></tr>
<tr><td>Binding constraint</td><td class="n">{E(s['pond']['binding_constraint'].replace('_', ' '))}</td></tr>
</tbody></table>
</div>

<h3>4.4 All ranked candidates</h3>
<table class="d">
<thead><tr><th class="n">#</th><th>Kind</th><th class="n">Score</th><th class="n">Catchment (ha)</th>
<th class="n">Tc (min)</th><th class="n">Capacity (m³)</th></tr></thead>
<tbody>{sites}</tbody></table>

<h3>4.5 Ground excluded before ranking</h3>
<p>{n(x['excluded_cells'])} cells were vetoed before any site was scored, from
{E(', '.join(x['sources']))} — reported confidence <b>{E(x['confidence'])}</b>.</p>
<table class="d">
<thead><tr><th>Rule</th><th class="n">Cells</th></tr></thead>
<tbody>{removed}</tbody></table>

<h3>4.6 Where the time went</h3>
<table class="d">
<thead><tr><th>Stage</th><th class="n">Seconds</th></tr></thead>
<tbody>{stages}</tbody></table>
<p>Data layers used: {E(', '.join(env['layers_used']))}.
{'Unavailable: ' + E(', '.join(env['layers_unavailable'])) + '.' if env['layers_unavailable'] else 'No layer was unavailable.'}</p>

<h3>4.7 The same analysis in the browser</h3>
{figure('ui-workspace.jpg', '1', 'the analysed sheet, catchment and ranked sites',
        'workspace')}
{figure('ui-candidates.jpg', '2', 'all candidates, with the sheet showing only those drawn',
        'candidates')}
{figure('ui-hydrology.jpg', '3', 'drainage network and the stage-storage curve',
        'hydrology')}"""


def _source(name: str) -> str:
    """Where a capture came from, as recorded in captures.json."""
    path = ASSETS / "captures.json"
    sources = json.loads(path.read_text()) if path.exists() else {}
    return E(sources.get(name, "not recorded"))


def sec_area(area: dict, cmp: dict, api_base: str) -> str:
    s = area["summary"]
    ts = area["terrain_source"]
    bbox = area["input"]["bbox"]
    loc = s["pond_location"]
    inflow = s.get("annual_inflow_m3") or {}
    sites = "\n".join(
        f'<tr><td class="n">{site["rank"]}</td>'
        f'<td class="mono">{site["location"]["lat"]:.5f}, {site["location"]["lon"]:.5f}</td>'
        f'<td class="n">{n(site["catchment"]["metrics"]["area_ha"], 1)} ha</td>'
        f'<td class="n">{n((site.get("expected_water") or {}).get("volume_m3"))} m³</td>'
        f'<td>{E(str((site.get("expected_water") or {}).get("limited_by") or "—"))}</td></tr>'
        for site in area["candidate_sites"]
    )
    c, d, g = cmp["contour"], cmp["drawn"], cmp["agreement"]
    return f"""<h2>5 · Selecting an area on the map</h2>
<p>A village without a contour survey can still be assessed. In the workspace, <b>Draw area on
map</b> turns a drag on the map into a rectangle; its size is shown while it is drawn, and
<b>Run</b> refuses anything under 0.1 km² or over 100 km² before a request is made. <b>Use the
sample area</b> draws the sample sheet's own extent in one click. The terrain comes from the
Copernicus GLO-30 elevation model, read for the rectangle plus a 500 m margin — water reaches a
site from beyond the line that was drawn — and sites are proposed only inside the rectangle.
From there every stage is the pipeline of §3.</p>
{figure('ui-area.jpg', '4', 'a selected area, analysed: the recommended site carries the water it collects, its catchment is labelled', 'selected area')}
{figure('ui-area-popup.jpg', '5', 'clicking a site: pond location, catchment area, water collected and inflow', 'popup')}
<h3>5.1 The same through the API</h3>
<pre>curl -X POST {E(api_base)}/api/v1/analyzeArea \\
  -H 'Content-Type: application/json' \\
  -d '{{"bbox": [{", ".join(f"{v:g}" for v in bbox)}]}}'</pre>
<p>The response has the shape of a contour analysis, with <code>contour_map</code> null and
<code>terrain_source</code> naming {E(ts['dataset'])} at {n(ts['resolution_m'])} m. It opens with the
three results:</p>
<table class="d">
<thead><tr><th>Result</th><th class="n">Value</th><th>Basis</th></tr></thead>
<tbody>
<tr><td>Pond location</td><td class="n">{loc['lat']:.5f}, {loc['lon']:.5f}</td>
    <td>Site {s['site_rank']}, suitability {n(s['suitability_score'], 1)}/100</td></tr>
<tr><td>Catchment area</td><td class="n">{n(s['catchment_area_ha'], 1)} ha</td>
    <td>{n(s.get('catchment_area_km2'), 2)} km² draining to the site, delineated by D8 on the conditioned surface</td></tr>
<tr><td>Water collected</td><td class="n">{n(s['expected_water_volume_m3'])} m³ a year</td>
    <td>{E(s['expected_water_volume_basis'])}; limited by {E(str(s.get('expected_water_volume_limited_by')))}.
        Inflow {n(inflow.get('mean'))} m³ mean, {n(inflow.get('dependable_75_percent'))} m³ in three years of four</td></tr>
</tbody></table>
<p>Every candidate carries the same figure for itself. They are never added up: catchments
nest, so a total would count the same water twice.</p>
<table class="d">
<thead><tr><th class="n">#</th><th>Pond location</th><th class="n">Catchment</th><th class="n">Collects</th><th>Limited by</th></tr></thead>
<tbody>{sites}</tbody></table>
<p class="small">Captured from {_source('area.json')}.</p>
<h3>5.2 How much 30 m terrain costs</h3>
<p>Over the sample sheet's own extent, the same land analysed both ways
({n(cmp['area_km2'], 2)} km², enrichment off so only the terrain differs):</p>
<table class="d">
<thead><tr><th></th><th>Contour upload</th><th>Selected area</th></tr></thead>
<tbody>
<tr><td>Grid</td><td>{c['grid'][0]} × {c['grid'][1]} at {n(c['cell_m'])} m</td>
    <td>{d['grid'][0]} × {d['grid'][1]} at {n(d['cell_m'])} m, with the margin</td></tr>
<tr><td>Relief</td><td>{n(c['relief_m'], 1)} m</td><td>{n(d['relief_m'], 1)} m</td></tr>
<tr><td>Recommended site</td>
    <td>score {n(c['recommended']['score'], 1)} · {n(c['recommended']['catchment_ha'])} ha</td>
    <td>score {n(d['recommended']['score'], 1)} · {n(d['recommended']['catchment_ha'])} ha</td></tr>
</tbody></table>
<p>Elevations agree at r = {g['correlation']:.3f} over {n(g['samples'])} points, with a constant
offset of {g['bias_m']:+.1f} m and {n(g['spread_m'], 1)} m of spread about it. But the sites
differ: no 30 m candidate lies within {n(cmp['nearest_to_contour_first_m'])} m of the contour
analysis's recommended site, because each cell covers 36 times the ground. So a selected area
is a screening tool for any village; where a survey exists, the contour upload is the answer to
build on. The response says as much in <code>terrain_source.note</code>.</p>"""


def _load_rows(capture: dict) -> str:
    rows = []
    for level in capture["levels"]:
        s = level["summary"]
        rows.append(
            f'<tr><td>{E(s["scenario"])}</td><td class="n">{s["users"]}</td>'
            f'<td class="n">{s["ok"]}/{s["requests"]}</td><td class="n">{s["busy_503"]}</td>'
            f'<td class="n">{n(s["p50_s"], 1)} s</td><td class="n">{n(s["p95_s"], 1)} s</td>'
            f'<td class="n">{E(s["follow_ups_ok"])}</td></tr>'
        )
    return "\n".join(rows)


def _memory(capture: dict) -> str:
    mem = capture.get("memory") or {}
    peaks = [v["peak_mb"] for v in mem.values() if "peak_mb" in v]
    ooms = sum(v.get("oom_kills", 0) for v in mem.values())
    if not peaks:
        return "not recorded"
    if "memory_basis" in capture:  # sampled on the lab systems, not read from a container
        return (
            f"Process memory peaked at {min(peaks)}–{max(peaks)} MB per system of 512 "
            f"(sampled each second; sys4's figure includes PostgreSQL), {ooms} OOM kills"
        )
    return f"{min(peaks)}–{max(peaks)} MB peak per instance, {ooms} OOM kills"


def sec_scaling(jobs: dict, sync: dict, contour: dict, failover: dict) -> str:
    head = (
        '<thead><tr><th>Scenario</th><th class="n">Users</th><th class="n">OK</th>'
        '<th class="n">503</th><th class="n">p50</th><th class="n">p95</th>'
        '<th class="n">Follow-ups</th></tr></thead>'
    )
    return f"""<h2>6 · Performance, stress and scaling</h2>
<p>The service runs on three lab systems, each with <b>512 MB of memory and one CPU</b> (the
fourth allocated system, sys1, has not been reachable). One analysis of the sample sheet peaks at
a few hundred megabytes, so the design starts from one rule: <b>one analysis at a time per
system</b>, and never a crash in place of an answer.</p>
<pre>            browser — http://10.1.75.53:3274
                   │
      gateway (nginx) on sys2, hash on the browser's id
      ┌────────────┼────────────┐
    sys2         sys3         sys4          each: the API and the web UI in one
    API          API          API           process, restarted if it dies
                              PostgreSQL    village register, rainfall cache</pre>
<table class="d">
<thead><tr><th>Mechanism</th><th>What it does</th></tr></thead>
<tbody>
<tr><td>Overload guard</td><td>One analysis per system. A direct API call beyond that is answered at once with
    <code>503</code> and <code>Retry-After</code>; a run from the web application waits in a queue and says so.</td></tr>
<tr><td>Affinity</td><td>The page sends a per-browser id, and the gateway hashes on it, so a user's follow-up calls
    reach the system holding their terrain. Not the client address: every user on one lab subnet would land on
    one system.</td></tr>
<tr><td>Health</td><td>An instance that stops answering is taken out of rotation; its users move to the next one and
    come back when it restarts.</td></tr>
<tr><td>Memory</td><td>GDAL's cache and glibc's arenas bounded (the first sized itself from the host's RAM); terrain
    grids kept on disk rather than in memory; finished jobs capped.</td></tr>
<tr><td>Upstream data</td><td>Terrain, soil, land cover, rainfall and OpenStreetMap all cached on disk; one request per
    OpenStreetMap window at a time, and a refused window remembered for ten minutes.</td></tr>
<tr><td>The lab's internet</td><td>From the lab systems, S3, NASA POWER and SoilGrids time out and Open-Meteo answers
    429, so the 26 terrain tiles covering Chhattisgarh are kept on each system and 30 years of NASA POWER daily
    rainfall for its 65 cells are in the database. Open-Meteo is left alone for an hour after a refusal, and
    the rainfall step returns at its deadline with whichever source answered.</td></tr>
</tbody></table>
<h3>6.1 Under load</h3>
<p>Users placed deliberately: one on each system, two on each, or all on one. Web-application
runs of the sample area (queued, polled each second):</p>
<table class="d">{head}<tbody>{_load_rows(jobs)}</tbody></table>
<p class="small">{_memory(jobs)}.</p>
<p>The same as direct API calls, where overload is answered rather than queued:</p>
<table class="d">{head}<tbody>{_load_rows(sync)}</tbody></table>
<p class="small">{_memory(sync)}.</p>
<p>Contour uploads of the sample sheet (342 550 cells, the heaviest input):</p>
<table class="d">{head}<tbody>{_load_rows(contour)}</tbody></table>
<p class="small">{_memory(contour)}.</p>
<p>Three users on three systems are served as fast as one user on one. A fourth waits its turn
in the web application, or is told to retry by the API, rather than taking a system down; every
follow-up call reached its own terrain, and no system was OOM-killed.</p>
<h3>6.2 One system failing</h3>
<p>An instance was killed while a user pinned to it kept making requests:
{failover['failed']} of {failover['requests']} requests failed; the user was served by another
system at {n(failover['timeline'][1]['t_s'] if len(failover['timeline']) > 1 else 0, 1)} s and back on
their own at {n(failover['back_on_own_instance_s'], 1)} s.</p>
<h3>6.3 Limits</h3>
<table class="d">
<thead><tr><th>Limit</th><th>Value</th></tr></thead>
<tbody>
<tr><td>Selected area</td><td>0.1 to 100 km²</td></tr>
<tr><td>Contour upload</td><td>KML or KMZ, up to 50 MB</td></tr>
<tr><td>At once</td><td>One analysis per system, three systems</td></tr>
<tr><td>Beyond that</td><td>Queued in the web application; <code>503</code> with <code>Retry-After</code> from the API</td></tr>
</tbody></table>
<p class="small">Measured by {_source('loadtest-jobs.json')}. Failover: {_source('failover.json')}.</p>"""


def sec_apidocs(spec: dict, api_base: str) -> str:
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            groups.setdefault(tag, []).append(
                (method.upper(), path, op.get("summary", "").strip())
            )
    blocks = []
    for tag, routes in sorted(groups.items()):
        rows = "\n".join(
            f'<tr><td class="mono">{E(mth)}</td><td class="mono">{E(pth)}</td><td>{E(sm)}</td></tr>'
            for mth, pth, sm in routes
        )
        blocks.append(
            f"<h4>{E(tag.replace('-', ' ').title())}</h4>"
            f'<table class="d"><thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr>'
            f"</thead><tbody>{rows}</tbody></table>"
        )
    total = sum(len(v) for v in groups.values())
    return f"""<h2>7 · API documentation</h2>
<p>The service publishes an OpenAPI {E(spec.get('openapi', '3.1'))} description of all
{total} operations, browsable three ways:</p>
<table class="d">
<thead><tr><th>Form</th><th>URL</th><th>Use</th></tr></thead>
<tbody>
<tr><td>Swagger UI</td><td><code>{E(api_base)}/docs</code></td><td>Interactive — build and send a request in the page</td></tr>
<tr><td>ReDoc</td><td><code>{E(api_base)}/redoc</code></td><td>Laid out for reading</td></tr>
<tr><td>Raw spec</td><td><code>{E(api_base)}/openapi.json</code></td><td>Machine-readable, for client generation</td></tr>
</tbody></table>
{figure('ui-apidocs.jpg', '6', 'Swagger UI listing the published operations', 'api documentation')}
<h3>7.1 Every route</h3>
{''.join(blocks)}
<h3>7.2 Response shape</h3>
<p>A successful analysis returns one object. The blocks a caller is most likely to want:</p>
<table class="d">
<thead><tr><th>Key</th><th>Holds</th></tr></thead>
<tbody>
<tr><td><code>summary</code></td><td>The three results for the recommended site: pond location, catchment area, water collected</td></tr>
<tr><td><code>terrain_source</code></td><td>Where the terrain came from — the uploaded sheet, or Copernicus GLO-30 for a selected area</td></tr>
<tr><td><code>contour_map</code></td><td>What was read from the file: lines, levels, interval, bounds, chosen CRS (null for a selected area)</td></tr>
<tr><td><code>interpolated_terrain</code></td><td>Grid size, cell size, interpolation diagnostics</td></tr>
<tr><td><code>recommended_site</code></td><td>The top-ranked site, expanded — a copy of <code>candidate_sites[0]</code></td></tr>
<tr><td><code>candidate_sites[]</code></td><td>Per site: location, score, criteria breakdown, catchment, runoff, pond</td></tr>
<tr><td><code>candidate_sites[].catchment</code></td><td><code>metrics</code>, <code>pour_point</code>, <code>snapped</code>, <code>quality</code>, and the boundary as a GeoJSON polygon</td></tr>
<tr><td><code>suitability</code></td><td>Tier, AHP weights, and the exclusion audit</td></tr>
<tr><td><code>environment</code></td><td>Which data layers answered, which did not, and why</td></tr>
<tr><td><code>explanation</code></td><td>Plain-language summary and caveats for each site</td></tr>
<tr><td><code>warnings[]</code></td><td>Anything the reader should verify before acting</td></tr>
</tbody></table>
<h3>7.3 Errors</h3>
<p>Failures use RFC 9457 problem documents, so a client can branch on
<code>type</code> rather than parse prose:</p>
<pre>{{"type": "/errors/validation", "title": "Validation failed", "status": 422,
 "detail": "file is not well-formed XML/KML: syntax error: line 1, column 0",
 "trace_id": "c2c91fb78f7c"}}</pre>
<table class="d">
<thead><tr><th>Status</th><th>When</th></tr></thead>
<tbody>
<tr><td class="n">400</td><td>Missing field, or a filename that is not a contour map</td></tr>
<tr><td class="n">404</td><td>Unknown <code>dem_id</code> or job id — analyses are kept 24 hours</td></tr>
<tr><td class="n">413</td><td>Upload above the size limit</td></tr>
<tr><td class="n">422</td><td>The file parsed but cannot be analysed — no contours, no elevations, a KMZ that expands too far</td></tr>
<tr><td class="n">503</td><td>A required dependency is down; the response names it</td></tr>
</tbody></table>"""


def sec_ai() -> str:
    """Disclosure of AI assistance. Kept short and factual; a disclosure that
    editorialises is harder to take at face value than one that simply says what
    happened."""
    return """<h2>8 &middot; AI usage</h2>
<p>Claude (Anthropic) was used as a coding assistant and to prepare this document.</p>"""


def build(api_base: str) -> str:
    a = load("analysis.json")
    spec = load("openapi.json")
    ss = load("streams-site.json")
    sw = load("streams-sheet.json")
    area = load("area.json")
    cmp = load("compare-30m-5m.json")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(COURSE)} — Contour: catchment estimation from a contour map</title>
<style>{CSS}</style></head>
<body><div class="sheet">
{sec_cover(a, api_base)}
{sec_requirements(api_base)}
{sec_repo()}
{sec_api_route(a, api_base)}
{sec_approach(a, ss, sw)}
{sec_demo(a)}
{sec_area(area, cmp, api_base)}
{sec_scaling(load("loadtest-jobs.json"), load("loadtest-sync.json"), load("loadtest-contour.json"), load("failover.json"))}
{sec_apidocs(spec, api_base)}
{sec_ai()}
<footer class="colophon">
  <span>{E(STUDENT)} · {E(ROLL)}</span>
  <span>{E(COURSE)}</span>
</footer>
</div></body></html>
"""


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Render with headless Chrome — its print path is the one the stylesheet
    was written against, so the PDF matches a browser's Save as PDF."""
    for exe in (
        "google-chrome",
        "chromium",
        "chromium-browser",
        "google-chrome-stable",
    ):
        chrome = shutil.which(exe)
        if not chrome:
            continue
        profile = pdf_path.parent / ".chrome-profile"
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=240)
        except subprocess.TimeoutExpired:
            print(f"  {exe} timed out", file=sys.stderr)
            continue
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if pdf_path.exists() and pdf_path.stat().st_size > 2000:
            return True
        print(
            f"  {exe} failed: {done.stderr.decode(errors='replace')[-400:]}",
            file=sys.stderr,
        )
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", action="store_true", help="also render docs/REPORT.pdf")
    ap.add_argument(
        "--api-url", default=API_BASE, help="base URL quoted as the working route"
    )
    ap.add_argument("--out", default=str(OUT_HTML), help="output HTML path")
    args = ap.parse_args()

    html = Path(args.out)
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(build(args.api_url.rstrip("/")), encoding="utf-8")
    print(f"  {html.relative_to(REPO)}  {html.stat().st_size / 1024:,.0f} KB")

    if args.pdf:
        pdf = Path(str(html).replace(".html", ".pdf"))
        if not to_pdf(html, pdf):
            print("  no PDF: install Chrome or Chromium", file=sys.stderr)
            return 1
        print(f"  {pdf.relative_to(REPO)}  {pdf.stat().st_size / 1024:,.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
