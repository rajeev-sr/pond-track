#!/usr/bin/env python3
"""Warm every provider cache, then prove DEMO_MODE runs on it offline (M7-10).

`DEMO_MODE=true` makes the providers refuse to touch the network: a cached window
is served, a miss degrades the tier. That is only useful if the cache has been
filled first, and only *trustworthy* if someone has checked that the filled cache
actually carries a demo with the network unavailable.

So this does both. It runs the real analyses with the network on, and then repeats
one with `DEMO_MODE` forced and asserts the answer still comes back at the same
tier. A warming script that does not verify its own output is how a demo fails in
front of an audience.

    python scripts/warm_demo.py            # warm, then verify
    python scripts/warm_demo.py --verify   # verify only, no network
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

#: The locations a demo actually visits: the bundled sheet's own area, and the
#: two rural test villages. The urban references are deliberately excluded --
#: they exist to be *rejected*, and rejection needs no warm cache.
#: Coordinates taken from `scripts/screen_sites.py`, which is where the project
#: records them, rather than re-entered here. An earlier version of this file
#: carried a guessed position for Jevra Sirsa and SoilGrids reported "no clay
#: value" there -- a real data gap at a point that was simply wrong.
LOCATIONS = [
    ("Khapri (sample contour map area)", 81.29703, 21.25170),
    ("Jevra Sirsa", 81.30610, 21.24645),
]


#: The bundled sheet. Looked for in a few places because this runs both inside
#: the container (where only `backend/` is mounted, so the repo root is absent)
#: and on the host from a checkout.
def _sample() -> Path:
    for candidate in (
        # Where compose mounts it in the container: deliberately outside /srv and
        # /data, since Docker creates a missing file-mount target on the host and
        # both of those are bind mounts of repository directories.
        Path("/opt/contour-sample/contours_1m.kml"),
        BACKEND.parent / "contours_1m.kml",
    ):
        if candidate.exists():
            return candidate
    return BACKEND.parent / "contours_1m.kml"


SAMPLE = _sample()


def _store() -> Path:
    from app.config import get_settings

    return Path(get_settings().COG_STORE_PATH)


def warm_from_sample() -> str:
    """Run the bundled analysis, which warms soil, land cover and rainfall at once."""
    from app.services.contour_analysis import (
        ContourAnalysisOptions,
        analyze_contour_map,
    )

    if not SAMPLE.exists():
        print(f"  ! no sample map at {SAMPLE}")
        return "missing"

    started = time.perf_counter()
    result = analyze_contour_map(
        SAMPLE.read_bytes(),
        SAMPLE.name,
        ContourAnalysisOptions(enrich=True, max_sites=3, include_contours=False),
    )
    tier = result.enrichment.tier
    print(
        f"  sample sheet      tier={tier:13s} {time.perf_counter() - started:5.1f}s"
        + (
            ""
            if tier == "full"
            else f"  (lost: {[f['layer'] for f in result.enrichment.failures]})"
        )
    )
    return tier


def warm_point(name: str, lon: float, lat: float) -> None:
    """Warm soil and rainfall for a village centre."""
    from app.providers.rainfall.ensemble import fetch_ensemble
    from app.providers.soil.soilgrids import fetch_soil_profile
    from app.services import provider_cache

    store = _store()
    try:
        profile = provider_cache.cached_soil(lon, lat, store, fetch=fetch_soil_profile)
        soil = f"{profile.texture_class}/HSG {profile.hydrologic_soil_group}"
    except Exception as exc:  # a warm failure is reported, never fatal
        soil = f"unavailable ({type(exc).__name__})"
    try:
        ensemble = fetch_ensemble(lon, lat, years=30)
        rain = f"{ensemble.primary.mean_annual_mm:.0f} mm/yr"
    except Exception as exc:
        rain = f"unavailable ({type(exc).__name__})"
    print(f"  {name:34s} soil {soil:22s} rain {rain}")


def verify_offline() -> bool:
    """Re-run the sample with DEMO_MODE forced, and no network permitted.

    Monkeypatching the settings rather than setting an env var, so this works in
    one process and cannot be defeated by a cached `get_settings()`.
    """
    from app.config import get_settings
    from app.services.contour_analysis import (
        ContourAnalysisOptions,
        analyze_contour_map,
    )

    if not SAMPLE.exists():
        return False

    settings = get_settings()
    original = settings.DEMO_MODE
    object.__setattr__(settings, "DEMO_MODE", True)
    try:
        started = time.perf_counter()
        result = analyze_contour_map(
            SAMPLE.read_bytes(),
            SAMPLE.name,
            ContourAnalysisOptions(enrich=True, max_sites=3, include_contours=False),
        )
        elapsed = time.perf_counter() - started
        tier = result.enrichment.tier
        lost = [f["layer"] for f in result.enrichment.failures]
        print(f"\n  DEMO_MODE run      tier={tier:13s} {elapsed:5.1f}s")
        if lost:
            print(f"    layers not in the warm cache: {lost}")
        ok = tier == "full"
        print(
            "    ✓ the demo runs entirely from cache"
            if ok
            else "    ! the cache is incomplete — some layers would degrade offline"
        )
        return ok
    finally:
        object.__setattr__(settings, "DEMO_MODE", original)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="skip warming; only check the cache")
    args = parser.parse_args()

    from app.services import provider_cache

    store = _store()
    print(f"cache store: {store}")

    if not args.verify:
        print("\nwarming (network required):")
        warm_from_sample()
        for name, lon, lat in LOCATIONS:
            warm_point(name, lon, lat)

    print("\ncache contents:")
    for kind, info in provider_cache.stats(store).items():
        print(f"  {kind:12s} {info['entries']:3d} entries, {info['bytes'] / 1024:.0f} kB")

    ok = verify_offline()
    print(
        "\nready for an offline demo."
        if ok
        else "\nnot fully warm: run again with a network connection."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
