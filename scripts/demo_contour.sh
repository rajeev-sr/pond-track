#!/usr/bin/env bash
#
# Demonstrate POST /api/v1/analyzeContour end to end against a local server.
#
#   ./scripts/demo_contour.sh [path/to/contours.kml]
#
# Defaults to the sample contour map in the repository root. Prints a readable
# summary and leaves the full JSON response in demo_output/.
#
# Deliberately depends on nothing but bash, curl and python3 -- all three ship
# with the stack you already need to run the API, so the demo works on a fresh
# clone with no extra installs.

set -euo pipefail

API="${API:-http://localhost:8000}"
KML="${1:-contours_1m.kml}"
OUT_DIR="${OUT_DIR:-demo_output}"
MAX_SITES="${MAX_SITES:-3}"
ENRICH="${ENRICH:-true}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

[ -f "$KML" ] || fail "contour map not found: $KML
Pass one as the first argument, or run from the repository root."

command -v curl >/dev/null || fail "curl is required"
command -v python3 >/dev/null || fail "python3 is required"

bold "1. Is the API up?"
if ! curl -fsS --max-time 5 "$API/api/v1/health" >/dev/null 2>&1; then
  fail "no API at $API
Start it first:   cp .env.example .env && docker compose up -d
Then re-run this script. Override the address with API=http://host:port"
fi
curl -fsS "$API/api/v1/health" | python3 -m json.tool | sed 's/^/   /'

bold "2. Readiness and which data layers are configured"
curl -fsS "$API/api/v1/health/ready" -o "${OUT_DIR:-demo_output}/ready.json" --create-dirs
python3 - "${OUT_DIR:-demo_output}/ready.json" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
print(f"   status: {d['status']}")
for name, state in d["checks"].items():
    print(f"   {name:<10} {state['status']}")
for feat, state in d["features"].items():
    print(f"   {feat:<24} {state}")
PY

bold "3. POST $KML to /api/v1/analyzeContour"
mkdir -p "$OUT_DIR"
RESPONSE="$OUT_DIR/analysis.json"
printf '   uploading %s (%s)\n' "$KML" "$(du -h "$KML" | cut -f1)"

HTTP_CODE=$(curl -s -X POST "$API/api/v1/analyzeContour" \
  -F "file=@${KML}" \
  -F "max_sites=${MAX_SITES}" \
  -F "enrich=${ENRICH}" \
  -o "$RESPONSE" -w '%{http_code}')

if [ "$HTTP_CODE" != "200" ]; then
  printf '\033[31m   HTTP %s\033[0m\n' "$HTTP_CODE"
  python3 -m json.tool < "$RESPONSE" | sed 's/^/   /'
  fail "request failed"
fi
printf '   HTTP 200, %s of JSON -> %s\n' "$(du -h "$RESPONSE" | cut -f1)" "$RESPONSE"

bold "4. What came back"
python3 - "$RESPONSE" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
cm, it, su, env = d["contour_map"], d["interpolated_terrain"], d["suitability"], d["environment"]

def row(label, value):
    print(f"   {label:<26} {value}")

print("\n   -- read from the file (nothing assumed) --")
row("elevation found in", cm["elevation_strategy"])
row("contour lines", f"{cm['lines_parsed']:,} ({cm['vertices_used']:,} vertices)")
row("levels / interval", f"{cm['levels']} levels @ {cm['contour_interval_m']} m")
row("elevation range", f"{cm['elevation_min_m']} - {cm['elevation_max_m']} m "
                       f"(relief {cm['relief_m']} m)")
row("working CRS", f"EPSG:{cm['working_crs_epsg']} (derived from the centroid)")

print("\n   -- interpolated terrain --")
row("grid resolution", f"{it['grid_resolution_m']} m "
                       f"({'derived' if it['grid_resolution_derived'] else 'as requested'})")
row("mean contour spacing", f"{it['mean_contour_spacing_m']} m")
row("grid size", f"{it['grid_size'][0]} x {it['grid_size'][1]} "
                 f"= {it['grid_cells']:,} cells")
row("hull coverage", f"{it['hull_coverage_pct']} %")
row("max upstream area", f"{it['max_upstream_area_ha']} ha")

print("\n   -- data available --")
row("analysis tier", su["analysis_tier"])
row("layers used", ", ".join(su["layers_used"]))
if su["layers_unavailable"]:
    row("layers unavailable", ", ".join(su["layers_unavailable"]))
for f in env["provider_failures"]:
    row(f"! {f['layer']}", f["reason"][:60])
if env.get("soil"):
    row("soil", f"{env['soil']['usda_texture_class']} -> HSG "
                f"{env['soil']['hydrologic_soil_group']}")
if env.get("rainfall"):
    a, m = env["rainfall"]["annual"], env["rainfall"]["monsoon"]
    row("rainfall", f"{a['mean_mm']} mm/yr, 75% dependable {a['dependable_75_mm']} mm")
    row("monsoon (derived)", f"{m['type']}, {'-'.join(m['months'][::3])}, {m['share_pct']} %")

print(f"\n   -- {len(d['candidate_sites'])} candidate site(s), best first --")
for s in d["candidate_sites"]:
    c, ro, pn = s["catchment"], s.get("runoff") or {}, s.get("pond") or {}
    loc, met = s["location"], c["metrics"]
    print(f"\n   #{s['rank']}  {s['site_kind']}   score {s['suitability_score']}/100")
    print(f"       at {loc['lon']}, {loc['lat']}  ({s['terrain']['elevation_m']} m)")
    print(f"       CATCHMENT  {met['area_ha']} ha  ({met['area_km2']} km2)")
    print(f"                  relief {met['relief_m']} m | mean slope "
          f"{met['mean_slope_pct']} % | Tc {met['time_of_concentration_min']} min")
    print(f"                  longest flow path {met['longest_flow_path_m']} m | "
          f"confidence: {c['quality']['confidence'].split(':')[0]}")
    if ro.get("available"):
        cn, am, dep = ro["curve_number"], ro["annual_mean"], ro["design_75_percent_dependable"]
        print(f"       RUNOFF     CN {cn['composite_cn_amc2']} (HSG "
              f"{cn['hydrologic_soil_group']}) -> {am['runoff_volume_m3']:,.0f} m3/yr, "
              f"C = {am['runoff_coefficient']}")
        print(f"                  design (75% dependable) "
              f"{dep['runoff_volume_m3']:,.0f} m3")
    else:
        print(f"       RUNOFF     not estimated: {ro.get('reason', 'n/a')[:60]}")
    if pn.get("available"):
        r = pn["recommended"]
        print(f"       POND       {r['depth_m']} m deep, "
              f"{r['top_length_m']:.0f} x {r['top_width_m']:.0f} m -> "
              f"{r['gross_capacity_m3']:,.0f} m3 gross")
        print(f"                  live storage {r['live_storage_m3']:,.0f} m3 | "
              f"~Rs {r['estimated_cost_inr']:,.0f}")
        print(f"                  buildable land "
              f"{pn['footprint']['usable_buildable_area_ha']} ha | "
              f"BINDING CONSTRAINT: {pn['binding_constraint']}")
    print(f"       WHY        " + " | ".join(
        f"{b['criterion']} {b['contribution']:.3f}" for b in s["criteria_breakdown"]))

if d["warnings"]:
    print("\n   -- warnings --")
    for w in d["warnings"]:
        print(f"   * {w[:110]}")

print(f"\n   elapsed {d['elapsed_s']} s   stages: " +
      ", ".join(f"{k} {v}s" for k, v in d["stage_timings_s"].items()))
PY

bold "5. Also try"
UI="${UI_URL:-http://localhost:8080}"
if curl -fsS -o /dev/null --max-time 3 "$UI/" 2>/dev/null; then
  UI_LINE="   Map interface           $UI  (drop the KML on the page)"
else
  UI_LINE="   Map interface           docker compose up -d frontend, then $UI"
fi
cat <<TXT
$UI_LINE
   Interactive docs        $API/docs
   Terrain only, offline   curl -X POST $API/api/v1/analyzeContour \\
                             -F 'file=@$KML' -F 'enrich=false'
   Parse without siting    curl -X POST $API/api/v1/terrain/contour-map -F 'file=@$KML'
   Alias                   $API/api/v1/findCatchment

   Full response saved to  $RESPONSE
TXT
