#!/usr/bin/env python3
"""Stage a state's terrain tiles and rainfall for systems that cannot fetch them.

The lab systems cannot reach S3 or NASA POWER reliably, so what a drawn area
needs is fetched here, where the internet works, and copied over:

    # in the API environment (the dev image), repo mounted
    python scripts/stage_region.py --iso IN-CT --tiles-out data/cache/copernicus --power

1. The Copernicus GLO-30 tiles (1 deg) that touch the state, plus 5 km, are
   downloaded to --tiles-out and opened once to prove they are whole. On a
   server they go in `$COG_STORE_PATH/copernicus/`, where the elevation
   provider reads them before trying S3.
2. With --power, NASA POWER's 30-year daily series for every POWER cell over
   the state is fetched through the provider, which writes the configured
   database's `rainfall_cache`. Copy those rows to the server's database:

       \\copy (select source, cell_key, cell_lat, cell_lon, observed_on,
               precipitation_mm, temperature_c, et0_mm from rainfall_cache
               where source = 'nasa_power') to 'power.csv' with (format csv)

   then load them into a temporary table there and
   `INSERT ... SELECT gen_random_uuid(), ... ON CONFLICT DO NOTHING`.

Chhattisgarh (IN-CT) is 26 tiles, 1.08 GB, and 65 cells, 712,270 days.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOUNDARIES = REPO / "data" / "seed" / "geoboundaries-IND-ADM1.geojson"
BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"
#: About 5 km, so a rectangle drawn on the border still finds its terrain.
MARGIN_DEG = 0.05
#: NASA POWER's grid (MERRA-2): 0.5 deg north-south, 0.625 deg east-west.
POWER_STEP = (0.5, 0.625)


def region(iso: str, path: Path):  # type: ignore[no-untyped-def]
    from shapely.geometry import shape
    from shapely.ops import unary_union

    features = json.loads(path.read_text())["features"]
    parts = [shape(f["geometry"]) for f in features if f["properties"].get("shapeISO") == iso]
    if not parts:
        raise SystemExit(f"no feature with shapeISO {iso} in {path}")
    return unary_union(parts).buffer(MARGIN_DEG)


def tiles(area) -> list[str]:  # type: ignore[no-untyped-def]
    from shapely.geometry import box

    min_x, min_y, max_x, max_y = area.bounds
    names = []
    for lat in range(math.floor(min_y), math.floor(max_y) + 1):
        for lon in range(math.floor(min_x), math.floor(max_x) + 1):
            if area.intersects(box(lon, lat, lon + 1, lat + 1)):
                ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
                names.append(
                    f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"
                )
    return names


def power_cells(area) -> list[tuple[float, float]]:  # type: ignore[no-untyped-def]
    from shapely.geometry import box

    dlat, dlon = POWER_STEP
    min_x, min_y, max_x, max_y = area.bounds
    cells = []
    lat = round(min_y / dlat) * dlat
    while lat <= max_y + dlat / 2:
        lon = round(min_x / dlon) * dlon
        while lon <= max_x + dlon / 2:
            if area.intersects(box(lon - dlon / 2, lat - dlat / 2, lon + dlon / 2, lat + dlat / 2)):
                cells.append((round(lat, 4), round(lon, 4)))
            lon += dlon
        lat += dlat
    return cells


def download(name: str, out: Path) -> str:
    target = out / f"{name}.tif"
    if target.is_file():
        return f"have {name}"
    part = target.with_suffix(".tif.part")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(f"{BUCKET}/{name}/{name}.tif", timeout=300) as r:
                part.write_bytes(r.read())
            break
        except OSError as exc:
            if attempt == 5:
                return f"FAILED {name}: {exc}"
            time.sleep(5 * (attempt + 1))
    import rasterio

    with rasterio.open(part) as src:  # a truncated COG fails here, not on a server
        src.read(1, out_shape=(64, 64))
    part.rename(target)
    return f"got {name} ({target.stat().st_size / 2**20:.0f} MB)"


def fetch_power(cell: tuple[float, float]) -> str:
    from app.providers.rainfall import nasa_power

    lat, lon = cell
    for attempt in range(4):
        try:
            stats = nasa_power.fetch_rainfall(lon, lat)
            return f"ok {lat},{lon} mean {stats.mean_annual_mm:.0f} mm"
        except Exception as exc:  # reported, then retried
            error = repr(exc)[:120]
            time.sleep(5 * (attempt + 1))
    return f"FAILED {lat},{lon}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iso", default="IN-CT", help="shapeISO of the state, e.g. IN-CT")
    parser.add_argument("--boundaries", type=Path, default=BOUNDARIES)
    parser.add_argument("--tiles-out", type=Path, help="download the terrain tiles here")
    parser.add_argument("--power", action="store_true", help="fetch NASA POWER into the DB")
    args = parser.parse_args()

    area = region(args.iso, args.boundaries)
    names, cells = tiles(area), power_cells(area)
    print(f"{args.iso}: {len(names)} terrain tiles, {len(cells)} NASA POWER cells", flush=True)
    failed = 0
    if args.tiles_out:
        args.tiles_out.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(4) as pool:
            for job in as_completed([pool.submit(download, n, args.tiles_out) for n in names]):
                line = job.result()
                failed += line.startswith("FAILED")
                print(" ", line, flush=True)
    if args.power:
        with ThreadPoolExecutor(3) as pool:  # POWER asks for restraint, not a quota
            for job in as_completed([pool.submit(fetch_power, c) for c in cells]):
                line = job.result()
                failed += line.startswith("FAILED")
                print(" ", line, flush=True)
    print("done" if not failed else f"{failed} failed")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
