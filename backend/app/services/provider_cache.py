"""Disk cache for the soil and land-cover fetches, and what `DEMO_MODE` means (M7-10).

Rainfall already has a Postgres cache (per `(source, cell, date)`) and OSM a
content-addressed disk cache. Soil and land cover had neither, which left two
gaps that matter for different reasons:

* **A demo needs to be deterministic.** SoilGrids latency is erratic enough that
  a live fetch during a presentation is a coin flip.
* **`DEMO_MODE` was declared and did nothing.** It sat in `config.py`, was echoed
  by `/health`, and was documented in `INSTALL.md` as "serve warmed fixtures
  instead of live providers" — a claim nothing in the code supported.

So `DEMO_MODE=true` now means something precise and testable: **providers may not
touch the network.** A cache hit is served; a miss raises
`ProviderUnavailableError`, which the enrichment layer already handles by
degrading the tier and saying which layer was lost. That is the behaviour that
makes the release checklist's "works with the network unplugged" true — and it
fails *honestly* rather than hanging on a socket timeout.

The cache is keyed on the arguments, so land cover resampled onto a different DEM
grid is a different entry. That is not wasteful: the array is grid-aligned, and an
entry for the wrong grid would be silently misaligned data, which is worse than a
miss.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.providers.base import ProviderUnavailableError
from app.providers.landcover.worldcover import LandCover
from app.providers.soil.soilgrids import SoilProfile

log = get_logger("services.provider_cache")

#: Soil and land cover are physical facts about the ground; they do not change
#: between demos. Long enough to be useful, finite so a re-survey is eventually
#: picked up.
DEFAULT_TTL_S = 180 * 24 * 3600

CACHE_VERSION = 1


def _key(*parts: object) -> str:
    joined = f"v{CACHE_VERSION}|" + "|".join(repr(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _path(store: Path, kind: str, key: str, suffix: str) -> Path:
    return store / "providers" / kind / key[:2] / f"{key}{suffix}"


def _fresh(path: Path, ttl_s: float) -> bool:
    if not path.exists():
        return False
    import time

    return (time.time() - path.stat().st_mtime) <= ttl_s


def _blocked(kind: str) -> ProviderUnavailableError:
    return ProviderUnavailableError(
        kind,
        "DEMO_MODE is on and this window is not in the warmed cache, so the "
        "network was not used. Run `make demo-warm` with a network connection "
        "first, or unset DEMO_MODE.",
    )


# ── soil ──────────────────────────────────────────────────────────────────────


def cached_soil(
    lon: float,
    lat: float,
    store: Path,
    *,
    demo_mode: bool = False,
    ttl_s: float = DEFAULT_TTL_S,
    fetch: Callable[[float, float], SoilProfile] | None = None,
) -> SoilProfile:
    """A soil profile from cache, or fetched and cached.

    Rounded to four decimals — about 11 m — before keying. SoilGrids' own
    resolution is 250 m, so two points a metre apart cannot have different
    answers and should not have different cache entries.
    """
    key = _key("soil", round(lon, 4), round(lat, 4))
    path = _path(store, "soil", key, ".json")

    if _fresh(path, ttl_s):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            log.info("soil cache hit", lon=round(lon, 4), lat=round(lat, 4))
            return SoilProfile(**raw)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("soil cache unreadable", error=str(exc))

    if demo_mode:
        raise _blocked("soilgrids")

    fetcher = fetch
    if fetcher is None:
        from app.providers.soil.soilgrids import fetch_soil_profile

        fetcher = fetch_soil_profile

    profile = fetcher(lon, lat)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(vars(profile)), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        # A cache write failing must not cost the caller their answer.
        log.warning("soil cache write failed", error=str(exc))
    return profile


# ── land cover ────────────────────────────────────────────────────────────────


def cached_landcover(
    bounds: tuple[float, float, float, float],
    shape: tuple[int, int],
    transform: tuple[float, ...],
    epsg: int,
    store: Path,
    *,
    demo_mode: bool = False,
    ttl_s: float = DEFAULT_TTL_S,
    fetch: Callable[..., LandCover] | None = None,
) -> LandCover:
    """Land cover from cache, or fetched and cached.

    The grid is part of the key. Reusing an entry across grids would hand back a
    class raster that does not line up with the DEM, and misaligned land cover is
    worse than absent land cover: it produces a plausible composite curve number
    from the wrong cells.
    """
    key = _key(
        "landcover",
        tuple(round(b, 5) for b in bounds),
        tuple(shape),
        tuple(round(t, 6) for t in transform),
        epsg,
    )
    npz = _path(store, "landcover", key, ".npz")
    meta = _path(store, "landcover", key, ".json")

    if _fresh(npz, ttl_s) and _fresh(meta, ttl_s):
        try:
            with np.load(npz) as bundle:
                codes = bundle["codes"]
            extra = json.loads(meta.read_text(encoding="utf-8"))
            log.info("landcover cache hit", shape=list(shape))
            return LandCover(
                codes=codes.astype(np.uint8),
                fractions=extra["fractions"],
                dominant_class=extra["dominant_class"],
                tiles_used=extra["tiles_used"],
            )
        except (OSError, ValueError, KeyError) as exc:
            log.warning("landcover cache unreadable", error=str(exc))

    if demo_mode:
        raise _blocked("worldcover")

    fetcher = fetch
    if fetcher is None:
        from app.providers.landcover.worldcover import fetch_landcover

        fetcher = fetch_landcover

    cover = fetcher(bounds, shape, transform, epsg)
    try:
        npz.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(npz, codes=cover.codes)
        meta.write_text(
            json.dumps(
                {
                    "fractions": cover.fractions,
                    "dominant_class": cover.dominant_class,
                    "tiles_used": list(cover.tiles_used),
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("landcover cache write failed", error=str(exc))
    return cover


def stats(store: Path) -> dict[str, Any]:
    """What the warmed cache holds, for `make demo-warm` to report."""
    root = store / "providers"
    out: dict[str, Any] = {}
    for kind in ("soil", "landcover"):
        directory = root / kind
        # Entries are counted by their JSON sidecar (one per entry), but the
        # size has to include the .npz beside it -- counting only the sidecar
        # reported a cached land-cover raster as 0 kB.
        sidecars = list(directory.rglob("*.json")) if directory.exists() else []
        every = list(directory.rglob("*")) if directory.exists() else []
        out[kind] = {
            "entries": len(sidecars),
            "bytes": sum(f.stat().st_size for f in every if f.is_file()),
        }
    return out
