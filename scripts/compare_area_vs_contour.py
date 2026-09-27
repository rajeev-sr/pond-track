#!/usr/bin/env python3
"""The same land analysed both ways: a 5 m contour DEM against Copernicus 30 m.

Drawing a rectangle on the map needs terrain from somewhere, and the only free
global terrain is a 30 m model. This measures what that costs, on land where a
surveyed answer exists: the sample contour sheet is analysed as an upload, then
its own extent is analysed as a drawn area, and the two are compared --

  * elevation agreement, sampled on the contour grid (bias, spread, correlation);
  * relief, grid size and run time for each;
  * the candidate sites each proposes, and how far apart the two #1s are.

Usage:
    python scripts/compare_area_vs_contour.py                  # contours_1m.kml
    python scripts/compare_area_vs_contour.py path/to/map.kml

Needs the backend dependencies and network access (Copernicus is read from its
public S3 bucket). Enrichment is off on both sides, so the comparison is of the
terrain alone. Nothing is written except the DEM cache in a temporary folder.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np
from pyproj import Transformer

from app.providers.elevation.base import DemGrid
from app.services.contour_analysis import (
    ContourAnalysisOptions,
    analyze_area,
    analyze_contour_map,
)

SAMPLE = Path(__file__).resolve().parents[1] / "contours_1m.kml"


def distance_m(a: dict[str, float], b: dict[str, float]) -> float:
    lat = math.radians((a["lat"] + b["lat"]) / 2.0)
    dx = (a["lon"] - b["lon"]) * 111_320.0 * math.cos(lat)
    dy = (a["lat"] - b["lat"]) * 110_540.0
    return math.hypot(dx, dy)


def elevation_agreement(
    fine: DemGrid, coarse: DemGrid, stride: int = 10
) -> dict[str, float]:
    """Coarse-grid elevation at every `stride`-th cell centre of the fine grid."""
    rows, cols = fine.shape
    rr, cc = np.mgrid[0:rows:stride, 0:cols:stride]
    a, _, c, _, e, f = fine.transform
    xs, ys = Transformer.from_crs(fine.epsg, coarse.epsg, always_xy=True).transform(
        c + a * (cc + 0.5), f + e * (rr + 0.5)
    )
    a3, _, c3, _, e3, f3 = coarse.transform
    ci = np.floor((np.asarray(xs) - c3) / a3).astype(int)
    ri = np.floor((np.asarray(ys) - f3) / e3).astype(int)
    ok = (ri >= 0) & (ri < coarse.shape[0]) & (ci >= 0) & (ci < coarse.shape[1])
    z_fine = fine.elevation[rr, cc].astype(float)
    z_coarse = np.full(z_fine.shape, np.nan)
    z_coarse[ok] = coarse.elevation[ri[ok], ci[ok]]
    both = np.isfinite(z_fine) & np.isfinite(z_coarse)
    diff = z_coarse[both] - z_fine[both]
    return {
        "samples": int(both.sum()),
        "bias_m": float(diff.mean()),
        "spread_m": float(diff.std()),
        "mae_m": float(np.abs(diff).mean()),
        "correlation": float(np.corrcoef(z_fine[both], z_coarse[both])[0, 1]),
    }


def describe(name: str, body: dict[str, Any], dem: DemGrid, seconds: float) -> None:
    z = dem.elevation[np.isfinite(dem.elevation)]
    rows, cols = dem.shape
    print(
        f"\n{name}: {cols}x{rows} cells at {dem.cell_size_m:g} m, "
        f"{z.min():.1f}-{z.max():.1f} m (relief {z.max() - z.min():.1f} m), {seconds:.1f} s"
    )
    for site in body["candidate_sites"]:
        loc, metrics = site["location"], site["catchment"]["metrics"]
        print(
            f"  #{site['rank']}  {loc['lat']:.5f}, {loc['lon']:.5f}  "
            f"score {site['suitability_score']:>5}  {site.get('site_kind', ''):<18} "
            f"catchment {metrics['area_ha']:>8.1f} ha"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kml", nargs="?", type=Path, default=SAMPLE)
    parser.add_argument("--json", type=Path, help="also save the figures here")
    args = parser.parse_args()

    def options() -> ContourAnalysisOptions:
        return ContourAnalysisOptions(enrich=False, max_sites=5)

    t = time.perf_counter()
    upload = analyze_contour_map(args.kml.read_bytes(), args.kml.name, options())
    upload_s = time.perf_counter() - t

    assert upload.parsed is not None
    b = upload.parsed.bounds
    bbox = [b.min_lon, b.min_lat, b.max_lon, b.max_lat]
    with tempfile.TemporaryDirectory() as store:
        t = time.perf_counter()
        drawn = analyze_area(bbox, options(), store=Path(store))
        cold_s = time.perf_counter() - t
        t = time.perf_counter()
        analyze_area(bbox, options(), store=Path(store))
        warm_s = time.perf_counter() - t

    up, dr = upload.as_dict(), drawn.as_dict()
    print(f"extent {bbox}  ({dr['input']['area_km2']} km²)")
    describe("contour upload", up, upload.dem, upload_s)
    describe(f"drawn area (cold; warm {warm_s:.1f} s)", dr, drawn.dem, cold_s)

    agree = elevation_agreement(upload.dem, drawn.dem)
    print(
        f"\nelevation, Copernicus minus contour, over {agree['samples']} points: "
        f"bias {agree['bias_m']:+.2f} m, spread {agree['spread_m']:.2f} m, "
        f"MAE {agree['mae_m']:.2f} m, r = {agree['correlation']:.3f}"
    )
    nearest = None
    if up["candidate_sites"] and dr["candidate_sites"]:
        first = up["candidate_sites"][0]["location"]
        nearest = min(distance_m(first, s["location"]) for s in dr["candidate_sites"])
        print(f"nearest drawn-area candidate to the upload's #1: {nearest:.0f} m")

    if args.json:

        def side(body: dict[str, Any], dem: DemGrid, seconds: float) -> dict[str, Any]:
            z = dem.elevation[np.isfinite(dem.elevation)]
            rec = body["recommended_site"] or {}
            return {
                "grid": [dem.shape[1], dem.shape[0]],
                "cell_m": dem.cell_size_m,
                "elevation_m": [round(float(z.min()), 1), round(float(z.max()), 1)],
                "relief_m": round(float(z.max() - z.min()), 1),
                "seconds": round(seconds, 1),
                "recommended": {
                    "location": rec.get("location"),
                    "score": rec.get("suitability_score"),
                    "catchment_ha": (rec.get("catchment") or {})
                    .get("metrics", {})
                    .get("area_ha"),
                },
            }

        args.json.write_text(
            json.dumps(
                {
                    "extent": bbox,
                    "area_km2": dr["input"]["area_km2"],
                    "contour": side(up, upload.dem, upload_s),
                    "drawn": {
                        **side(dr, drawn.dem, cold_s),
                        "warm_seconds": round(warm_s, 1),
                    },
                    "agreement": {k: round(v, 3) for k, v in agree.items()},
                    "nearest_to_contour_first_m": (
                        None if nearest is None else round(nearest)
                    ),
                },
                indent=2,
            )
        )
        print(f"saved {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
