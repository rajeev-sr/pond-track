"""A disk cache for fetched OSM windows.

Public Overpass is genuinely unreliable: across one afternoon the same query
returned HTTP 504, 502, 429 and a clean 2.3 s answer, minutes apart. Without a
cache the land-availability endpoint is a coin flip, and the frontend calls it on
every analysis.

A whole *window* is the unit, not a feature or a tile: Overpass answers a bbox,
there is no partial reuse to be had, and the payload is a blob. That is why this
is a content-addressed file rather than a table -- the rainfall cache earns its
Postgres rows because it reuses individual `(cell, source, date)` tuples, and
none of that applies here.

Freshness is deliberately loose. OSM changes on the timescale of months and the
consumer buffers everything by tens of metres, so a fortnight-old building is as
good as a fresh one. The TTL exists to pick up new mapping eventually, not to
chase currency.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.providers.vector.overpass import OsmContext, OsmFeature

log = get_logger("providers.osm_cache")

#: Long, because OSM moves slowly and every consumer buffers by 5-100 m anyway.
DEFAULT_TTL_S = 14 * 24 * 3600

#: Bbox rounded before hashing, so two windows that differ in the seventh decimal
#: (about a centimetre) share an entry instead of each fetching its own.
KEY_PRECISION = 4

CACHE_VERSION = 1


def cell_for(bounds: tuple[float, float, float, float]) -> tuple[float, ...]:
    """The rounded bbox a window is keyed by."""
    return tuple(round(v, KEY_PRECISION) for v in bounds)


def cache_key(bounds: tuple[float, float, float, float]) -> str:
    import hashlib

    joined = f"v{CACHE_VERSION}|" + "|".join(f"{v:.{KEY_PRECISION}f}" for v in cell_for(bounds))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def path_for(store: Path, bounds: tuple[float, float, float, float]) -> Path:
    key = cache_key(bounds)
    return store / "osm" / key[:2] / f"{key}.json"


def _feature_as_dict(feature: OsmFeature) -> dict[str, Any]:
    return {
        "kind": feature.kind,
        "osm_type": feature.osm_type,
        "osm_id": feature.osm_id,
        "tags": feature.tags,
        # Rounded to ~1 cm. Full float precision would roughly double the file
        # for accuracy no consumer can use.
        "rings": [[[round(x, 7), round(y, 7)] for x, y in ring] for ring in feature.rings],
    }


def _feature_from_dict(raw: dict[str, Any]) -> OsmFeature:
    return OsmFeature(
        kind=raw["kind"],
        osm_type=str(raw.get("osm_type", "way")),
        osm_id=int(raw.get("osm_id", 0)),
        tags={str(k): str(v) for k, v in (raw.get("tags") or {}).items()},
        rings=tuple(tuple((float(p[0]), float(p[1])) for p in ring) for ring in raw["rings"]),
    )


def write(store: Path, bounds: tuple[float, float, float, float], context: OsmContext) -> Path:
    """Persist a fetched window. Never raises: a cache miss beats a 500."""
    target = path_for(store, bounds)
    payload = {
        "version": CACHE_VERSION,
        "fetched_at": time.time(),
        "bounds": list(cell_for(bounds)),
        "endpoint": context.endpoint,
        "features": [_feature_as_dict(f) for f in context],
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place, so a crash mid-write
        # cannot leave a half-file that later parses as an empty window.
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        log.warning("osm cache write failed", path=str(target), error=str(exc))
    return target


def read(
    store: Path,
    bounds: tuple[float, float, float, float],
    *,
    ttl_s: float = DEFAULT_TTL_S,
) -> OsmContext | None:
    """A cached window, or None on a miss, an expiry or an unreadable file."""
    target = path_for(store, bounds)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("osm cache unreadable", path=str(target), error=str(exc))
        return None

    if int(payload.get("version", 0)) != CACHE_VERSION:
        return None
    age = time.time() - float(payload.get("fetched_at", 0.0))
    if age > ttl_s:
        log.info("osm cache expired", age_days=round(age / 86400.0, 1))
        return None

    context = OsmContext(endpoint=str(payload.get("endpoint", "")))
    bucket = {
        "building": context.buildings,
        "road": context.roads,
        "track": context.tracks,
        "water": context.water,
        "landuse": context.landuse,
    }
    try:
        for raw in payload.get("features", []):
            target_list = bucket.get(str(raw.get("kind")))
            if target_list is None:
                continue
            target_list.append(_feature_from_dict(raw))
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("osm cache malformed", path=str(target), error=str(exc))
        return None

    log.info("osm cache hit", age_days=round(age / 86400.0, 1), **context.counts())
    return context


def fetch_cached(
    bounds: tuple[float, float, float, float],
    store: Path,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    fetch: Any = None,
) -> tuple[OsmContext, bool]:
    """`(context, was_cached)`, fetching only on a miss.

    `fetch` is injectable so the tests never touch Overpass.
    """
    hit = read(store, bounds, ttl_s=ttl_s)
    if hit is not None:
        return hit, True

    if fetch is None:
        from app.providers.vector.overpass import fetch_osm_context as fetch

    context = fetch(bounds)
    write(store, bounds, context)
    return context, False
