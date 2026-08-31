#!/usr/bin/env python3
"""Screen candidate villages against the site-selection criteria (M0-15).

Choosing where to test the system is a decision that should rest on measurements
rather than on local knowledge alone, so this reads the same public sources the
API itself uses and prints the numbers:

  * relief and mean slope from Copernicus DEM GLO-30
  * land cover -- cropland, built-up and open-water share -- from ESA WorldCover
  * annual rainfall and its variability from Open-Meteo / ERA5-Land

The criteria, from the implementation plan:

  1. relief of at least 20 m across the candidate area, or there is no gradient
     for water to follow and nothing for D8 to route;
  2. an existing pond or tank nearby, so a result can be sanity-checked against
     a structure a village already found worth building;
  3. neither coastal nor urban -- a built-up majority means there is no land to
     excavate, and tidal ground breaks the runoff assumptions.

Usage:
    python scripts/screen_sites.py                       # the recorded candidates
    python scripts/screen_sites.py 81.297,21.2517 ...    # arbitrary lon,lat pairs

Needs the backend dependencies (``make venv``) and network access. Nothing is
written; this only reports.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# Roughly 2.5 km across -- big enough to hold a plausible catchment, small enough
# that a village's own terrain is not averaged away against its neighbours'.
DEFAULT_HALF_SPAN_M = 1250.0

MIN_RELIEF_M = 20.0
MAX_BUILT_UP_PCT = 40.0
MIN_OPEN_WATER_PCT = 0.2

#: Pause between candidates so the free rainfall archive is not hammered.
PROVIDER_PAUSE_S = 12.0


@dataclass(frozen=True)
class Candidate:
    name: str
    lon: float
    lat: float
    note: str = ""


@dataclass(frozen=True)
class Unlocated:
    """A named candidate whose position is not yet known.

    Recorded rather than guessed. A screening report is only worth reading if
    every coordinate in it came from somewhere -- inventing one to fill the row
    would produce a plausible set of measurements about the wrong piece of
    ground, which is worse than an admitted gap.
    """

    name: str
    searched: str
    resolve_with: str


# Durg district, Chhattisgarh.
#
# Positions are OpenStreetMap fixes where OSM has one. It mostly does not: the
# surveyed area itself contains ten unnamed `place=hamlet` nodes and exactly one
# named village. That is the case for seeding the Census 2011 / SHRUG village
# polygons (M0-11) rather than treating OSM as the village register.
CANDIDATES = [
    Candidate(
        "Khapri (sample contour map)",
        81.29703,
        21.25170,
        "centroid of the supplied contours_1m.kml; reverse-geocodes to "
        "Khapri, Durg Tahsil",
    ),
    Candidate(
        "Jevra Sirsa",
        81.30610,
        21.24645,
        "recorded as 'Sisra'; fixed from the Jeora Sirsa landmark on SH7, "
        "inside the surveyed area -- confirm against SHRUG",
    ),
    Candidate(
        "Bhilai (urban reference)",
        81.37328,
        21.21207,
        "the district's steel city; included so the built-up screen can be "
        "seen rejecting somewhere it should",
    ),
    Candidate(
        "Durg town (urban reference)", 81.40079, 21.19830, "district headquarters"
    ),
]

UNLOCATED = [
    Unlocated(
        "Kutelabhata / kutelabhatha",
        "Nominatim, nine spellings including Devanagari (कुटेलाभाठा), and an "
        "Overpass name search across Durg district -- all empty. The Census "
        "register seeded by M0-11 has it as `kutelabhatha`, SHRID "
        "11-22-409-03317-442569, in Durg sub-district; its village code 442569 "
        "adjoins Khapri's 442570, so it borders the surveyed area. The register "
        "carries names and codes but no geometry, so there is still no point to "
        "sample terrain at",
        "SHRUG village polygons dropped into data/seed/ (M0-11d), or a survey fix",
    ),
]


def bounds_around(lon: float, lat: float, half_span_m: float):
    from app.providers.elevation.base import Bounds

    dlat = half_span_m / 110_540.0
    dlon = half_span_m / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    return Bounds(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def screen(candidate: Candidate, half_span_m: float) -> dict:
    import numpy as np

    from app.providers.elevation.copernicus_aws import fetch_dem
    from app.providers.landcover.worldcover import fetch_landcover
    from app.providers.rainfall.open_meteo import fetch_rainfall
    from app.services.hydrology import slope_percent

    out: dict = {
        "name": candidate.name,
        "lon": candidate.lon,
        "lat": candidate.lat,
        "note": candidate.note,
        # `blocking` holds failures that leave a criterion unevaluated; `context`
        # holds ones that only cost background detail. Conflating the two made a
        # rate-limited rainfall lookup read as "this village is unsuitable".
        "blocking": [],
        "context": [],
    }
    bounds = bounds_around(candidate.lon, candidate.lat, half_span_m)

    try:
        dem = fetch_dem(bounds, cell_size_m=30.0, buffer_m=0.0)
        z = dem.elevation
        finite = z[np.isfinite(z)]
        out["elev_min"] = float(finite.min())
        out["elev_max"] = float(finite.max())
        out["relief_m"] = out["elev_max"] - out["elev_min"]
        slope = slope_percent(z, dem.cell_size_m)
        out["mean_slope_pct"] = float(np.nanmean(slope))
        out["p95_slope_pct"] = float(np.nanpercentile(slope, 95))
    except Exception as exc:  # a screening tool reports every failure, never aborts
        out["blocking"].append(f"elevation: {type(exc).__name__}: {exc}")

    try:
        cover = fetch_landcover(bounds.as_tuple(), dem.shape, dem.transform, dem.epsg)
        out["dominant_cover"] = cover.dominant_class
        # `fractions` holds shares of valid cells, not percentages.
        fractions = cover.fractions
        out["built_up_pct"] = 100.0 * float(fractions.get("built_up", 0.0))
        out["cropland_pct"] = 100.0 * float(fractions.get("cropland", 0.0))
        out["water_pct"] = 100.0 * float(fractions.get("permanent_water", 0.0))
    except Exception as exc:
        out["blocking"].append(f"land cover: {type(exc).__name__}: {exc}")

    try:
        rain = fetch_rainfall(candidate.lon, candidate.lat, years=30)
        out["rain_mm"] = rain.mean_annual_mm
        out["rain_cv"] = rain.cv
        out["rain_75_mm"] = rain.dependable_75_mm
    except Exception as exc:
        # Rainfall is not one of the three criteria -- it is useful background
        # for later runoff work, so losing it must not decide the verdict.
        out["context"].append(f"rainfall: {type(exc).__name__}: {exc}")

    verdict = []
    if out.get("relief_m") is not None:
        ok = out["relief_m"] >= MIN_RELIEF_M
        verdict.append(("relief >= 20 m", ok, f"{out['relief_m']:.1f} m"))
    if out.get("built_up_pct") is not None:
        ok = out["built_up_pct"] <= MAX_BUILT_UP_PCT
        verdict.append(("not urban", ok, f"{out['built_up_pct']:.1f} % built up"))
    if out.get("water_pct") is not None:
        ok = out["water_pct"] >= MIN_OPEN_WATER_PCT
        verdict.append(
            ("existing water body", ok, f"{out['water_pct']:.2f} % open water")
        )
    out["verdict"] = verdict
    out["evaluated"] = len(verdict) == 3 and not out["blocking"]
    out["passes"] = out["evaluated"] and all(ok for _, ok, _ in verdict)
    return out


def report(result: dict) -> None:
    print(f"\n  {result['name']}  ({result['lat']:.5f}, {result['lon']:.5f})")
    if result["note"]:
        print(f"    {result['note']}")
    if "relief_m" in result:
        print(
            f"    elevation      {result['elev_min']:.1f}-{result['elev_max']:.1f} m"
            f"   relief {result['relief_m']:.1f} m"
        )
        print(
            f"    slope          mean {result['mean_slope_pct']:.2f} %"
            f"   p95 {result['p95_slope_pct']:.2f} %"
        )
    if "dominant_cover" in result:
        print(
            f"    land cover     {result['dominant_cover']}"
            f"   cropland {result['cropland_pct']:.1f} %"
            f"   built-up {result['built_up_pct']:.1f} %"
            f"   water {result['water_pct']:.2f} %"
        )
    if "rain_mm" in result:
        print(
            f"    rainfall       {result['rain_mm']:.0f} mm/yr"
            f"   CV {result['rain_cv']:.3f}"
            f"   75% dependable {result['rain_75_mm']:.0f} mm"
        )
    for name, ok, detail in result["verdict"]:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name:22s} {detail}")
    for failure in result["blocking"]:
        print(f"    [ ?? ] criterion unevaluated -- {failure}")
    for note in result["context"]:
        print(f"    [ -- ] background only, verdict unaffected -- {note}")
    if not result["evaluated"]:
        print("    => INDETERMINATE: a criterion could not be measured")
    else:
        print(
            f"    => {'SUITABLE' if result['passes'] else 'NOT SUITABLE as a test site'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "points",
        nargs="*",
        metavar="lon,lat",
        help="screen these coordinates instead of the recorded candidates",
    )
    parser.add_argument(
        "--half-span-m",
        type=float,
        default=DEFAULT_HALF_SPAN_M,
        help=f"half-width of the box sampled (default {DEFAULT_HALF_SPAN_M:g} m)",
    )
    args = parser.parse_args()

    if args.points:
        candidates = []
        for raw in args.points:
            lon_s, _, lat_s = raw.partition(",")
            candidates.append(Candidate(raw, float(lon_s), float(lat_s)))
    else:
        candidates = CANDIDATES

    print(
        f"Screening {len(candidates)} location(s) over "
        f"{2 * args.half_span_m / 1000:.1f} km boxes."
    )
    print(
        f"Criteria: relief >= {MIN_RELIEF_M:g} m | built-up <= {MAX_BUILT_UP_PCT:g} % "
        f"| open water >= {MIN_OPEN_WATER_PCT:g} %"
    )

    results = []
    for index, candidate in enumerate(candidates):
        if index:
            # Open-Meteo answered 429 when this ran five candidates back to back.
            # The archive API is free; not hammering it is the price of using it.
            time.sleep(PROVIDER_PAUSE_S)
        results.append(screen(candidate, args.half_span_m))
        report(results[-1])

    if not args.points and UNLOCATED:
        print("\n  Named but not yet located -- not screened, and not guessed at:")
        for item in UNLOCATED:
            print(f"    {item.name}")
            print(f"      searched:     {item.searched}")
            print(f"      resolve with: {item.resolve_with}")

    passing = [r["name"] for r in results if r["passes"]]
    unknown = [r["name"] for r in results if not r["evaluated"]]
    print(f"\n  {len(passing)} of {len(results)} pass: {', '.join(passing) or 'none'}")
    if unknown:
        print(f"  indeterminate: {', '.join(unknown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
