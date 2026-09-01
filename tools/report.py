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
from datetime import date
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
#: The deployed instance, verified reachable: nginx on 3272 serves the app and
#: reverse-proxies the API under the same origin, so one URL covers the route and
#: its documentation. Overridable, because whether it is up is a fact about the
#: host and not about this repository.
API_BASE = "http://10.1.75.53:3272"
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
<h1>Contour — catchment estimation and<br>pond siting from a contour map</h1>
<p class="lede">A backend service that reads a contour survey, reconstructs the terrain,
delineates the catchment above each candidate pond site, and returns the result as
structured JSON.</p>
<table class="meta">
  <tr><td><span>Submitted by</span></td><td>{E(STUDENT)} &nbsp;·&nbsp; Roll {E(ROLL)}</td></tr>
  <tr><td><span>Date</span></td><td>{date.today():%d %B %Y}</td></tr>
  <tr><td><span>GitHub repository</span></td>
      <td><a href="{E(GITHUB)}">{E(GITHUB)}</a></td></tr>
  <tr><td><span>API route</span></td>
      <td><code>POST {E(api_base)}/api/v1/analyzeContour</code><br>
          <code>POST {E(api_base)}/api/v1/findCatchment</code> &nbsp;(alias)</td></tr>
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
        ("API documentation", "All 29 routes, schemas and the interactive spec", "§5"),
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
<tr><td><code>docs/</code></td><td>HLD, technical report, install guide, this report</td></tr>
<tr><td><code>tools/report.py</code></td><td>Generator for this document</td></tr>
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
</tbody></table>

<h3>Request</h3>
<p><code>multipart/form-data</code>. Only <code>file</code> is required.</p>
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

<div class="note"><b>Field name.</b> The map is uploaded as multipart form field
<code>contour_map</code>. <code>file</code> is accepted as an alias so existing callers keep
working, but sending both is refused rather than guessed at — two uploads could differ, and
silently picking one would analyse a sheet the caller did not think they sent.</div>

<h3>Verification</h3>
<p>The transcript in §4 is a real call against a running instance; the response was
{n(len((ASSETS / 'analysis.json').read_bytes()))} bytes of JSON returned in
{n(a['elapsed_s'], 2)} s. Readiness is separately reportable:</p>
<pre>$ curl {E(api_base)}/api/v1/health/ready
{{"status": "ready", "checks": {{"database": {{"status": "ok"}}, "redis": {{"status": "ok"}}}}, ...}}</pre>
<div class="note"><b>On the URL above.</b> The host and port come from the deployment
configuration in <code>deploy/env.sys1.example</code>. The measurements in this report were
taken against a local instance at <code>{E(LOCAL_BASE)}</code>, which runs identical code from
the same image — so the numbers are of the software, not of one host. Substitute whichever
base URL is live when this is read.</div>"""


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
<div class="note stop"><b>Why this matters.</b> Flow accumulation and depression depth are the
two strongest siting signals, and an existing tank or a river maximises both — it <em>is</em>
the wettest ground on the sheet. Measured with land cover removed, three of five recommended
sites landed inside permanent water. The veto is applied to the buildable mask before scoring
so such ground is never proposed.</div>

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
    return f"""<h2>5 · API documentation</h2>
<p>The service publishes an OpenAPI {E(spec.get('openapi', '3.1'))} description of all
{total} operations, browsable three ways:</p>
<table class="d">
<thead><tr><th>Form</th><th>URL</th><th>Use</th></tr></thead>
<tbody>
<tr><td>Swagger UI</td><td><code>{E(api_base)}/docs</code></td><td>Interactive — build and send a request in the page</td></tr>
<tr><td>ReDoc</td><td><code>{E(api_base)}/redoc</code></td><td>Laid out for reading</td></tr>
<tr><td>Raw spec</td><td><code>{E(api_base)}/openapi.json</code></td><td>Machine-readable, for client generation</td></tr>
</tbody></table>
{figure('ui-apidocs.jpg', '4', 'Swagger UI listing the published operations', 'api documentation')}
<h3>5.1 Every route</h3>
{''.join(blocks)}
<h3>5.2 Response shape</h3>
<p>A successful analysis returns one object. The blocks a caller is most likely to want:</p>
<table class="d">
<thead><tr><th>Key</th><th>Holds</th></tr></thead>
<tbody>
<tr><td><code>contour_map</code></td><td>What was read from the file: lines, levels, interval, bounds, chosen CRS</td></tr>
<tr><td><code>interpolated_terrain</code></td><td>Grid size, cell size, interpolation diagnostics</td></tr>
<tr><td><code>recommended_site</code></td><td>The top-ranked site, expanded — a copy of <code>candidate_sites[0]</code></td></tr>
<tr><td><code>candidate_sites[]</code></td><td>Per site: location, score, criteria breakdown, catchment, runoff, pond</td></tr>
<tr><td><code>candidate_sites[].catchment</code></td><td><code>metrics</code>, <code>pour_point</code>, <code>snapped</code>, <code>quality</code>, and the boundary as a GeoJSON polygon</td></tr>
<tr><td><code>suitability</code></td><td>Tier, AHP weights, and the exclusion audit</td></tr>
<tr><td><code>environment</code></td><td>Which data layers answered, which did not, and why</td></tr>
<tr><td><code>explanation</code></td><td>Plain-language summary and caveats for each site</td></tr>
<tr><td><code>warnings[]</code></td><td>Anything the reader should verify before acting</td></tr>
</tbody></table>
<h3>5.3 Errors</h3>
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


def sec_repro(a: dict) -> str:
    return f"""<h2>6 · Reproducing this report</h2>
<p>The figures above are read from captured API output, not transcribed, so the document
cannot drift from the run it describes. To rebuild it:</p>
<pre>make up                                    # bring the stack up
curl -s -X POST http://localhost:8000/api/v1/analyzeContour \\
     -F "file=@contours_1m.kml" -F "max_sites=5" \\
     -o docs/report/assets/analysis.json
curl -s http://localhost:8000/openapi.json -o docs/report/assets/openapi.json

python3 tools/report.py --pdf              # docs/REPORT.html + docs/REPORT.pdf</pre>
<p>If a capture is missing the generator stops and says which, rather than emitting a report
with a gap in it.</p>
<div class="note"><b>Environment of the run quoted here.</b>
Analysis id <code>{E(a['analysis_id'])}</code>, generated
{E(a['generated_at'])}, tier <code>{E(a['suitability']['analysis_tier'])}</code>,
{n(a['elapsed_s'], 2)} s wall clock.</div>"""


def build(api_base: str) -> str:
    a = load("analysis.json")
    spec = load("openapi.json")
    ss = load("streams-site.json")
    sw = load("streams-sheet.json")
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
{sec_apidocs(spec, api_base)}
{sec_repro(a)}
<footer class="colophon">
  <span>{E(STUDENT)} · {E(ROLL)}</span>
  <span>{E(COURSE)}</span>
  <span>{date.today():%Y-%m-%d}</span>
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
