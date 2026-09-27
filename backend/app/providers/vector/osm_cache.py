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
import threading
import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.providers.base import ProviderUnavailableError
from app.providers.vector.overpass import OsmContext, OsmFeature

log = get_logger("providers.osm_cache")

#: Long, because OSM moves slowly and every consumer buffers by 5-100 m anyway.
DEFAULT_TTL_S = 14 * 24 * 3600

#: Bbox rounded before hashing, so two windows that differ in the seventh decimal
#: (about a centimetre) share an entry instead of each fetching its own.
KEY_PRECISION = 4

#: v2 adds water multipolygon relations to the fetched window.
CACHE_VERSION = 2

#: Versions still readable. A v1 entry was fetched with a ways-only query, so a
#: river mapped as a relation is absent from it -- but discarding it outright is
#: worse than reading it: the analysis may be offline, in which case dropping a
#: warm window removes OSM protection altogether rather than improving it. So v1
#: is read and simply reports `water_relations=False`, which is exactly the
#: truth: the ways are present, the relation supplement was never fetched.
READABLE_VERSIONS = frozenset({1, 2})

#: The *path* namespace, deliberately separate from `CACHE_VERSION`. The payload
#: schema version says how to interpret a file; this says where the file lives.
#: They were one value, which made bumping the schema silently repoint every key
#: at an empty path -- the warm window was not upgraded, it was unreachable. Bump
#: this only for a change that makes old entries genuinely unusable.
CACHE_KEY_VERSION = 1

#: How long a window that every mirror refused is remembered as refused. Short,
#: because the public servers recover within minutes -- but without it every run
#: over that window spent its whole 20 s enrichment budget asking again, warm or
#: not, and piled another request onto servers that were already refusing.
FAILURE_TTL_S = 10 * 60


class _Flight:
    """One window's request in progress, and what it came back with."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.context: OsmContext | None = None
        self.error: ProviderUnavailableError | None = None


#: Windows being fetched right now, so a second run over the same window waits
#: on the first request instead of sending its own.
_inflight: dict[str, _Flight] = {}
_inflight_lock = threading.Lock()


def cell_for(bounds: tuple[float, float, float, float]) -> tuple[float, ...]:
    """The rounded bbox a window is keyed by."""
    return tuple(round(v, KEY_PRECISION) for v in bounds)


def cache_key(bounds: tuple[float, float, float, float]) -> str:
    import hashlib

    joined = f"v{CACHE_KEY_VERSION}|" + "|".join(f"{v:.{KEY_PRECISION}f}" for v in cell_for(bounds))
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
        "water_relations": bool(context.water_relations),
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

    version = int(payload.get("version", 0))
    if version not in READABLE_VERSIONS:
        return None
    age = time.time() - float(payload.get("fetched_at", 0.0))
    if age > ttl_s:
        log.info("osm cache expired", age_days=round(age / 86400.0, 1))
        return None

    context = OsmContext(
        endpoint=str(payload.get("endpoint", "")),
        # Absent in v1, and False is the honest answer there.
        water_relations=bool(payload.get("water_relations", False)),
    )
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

    log.info(
        "osm cache hit",
        age_days=round(age / 86400.0, 1),
        version=version,
        water_relations=context.water_relations,
        **context.counts(),
    )
    return context


def fetch_cached(
    bounds: tuple[float, float, float, float],
    store: Path,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    fetch: Any = None,
) -> tuple[OsmContext, bool]:
    """`(context, was_cached)`, fetching only on a miss.

    One request per window at a time: a run that finds the window already being
    fetched waits for that answer (its own enrichment budget bounds the wait).
    A window every mirror just refused raises at once for `FAILURE_TTL_S`.

    `fetch` is injectable so the tests never touch Overpass.
    """
    hit = read(store, bounds, ttl_s=ttl_s)
    if hit is not None:
        return hit, True
    _raise_if_recently_refused(store, bounds)

    key = cache_key(bounds)
    with _inflight_lock:
        flight = _inflight.get(key)
        leader = flight is None
        if flight is None:
            flight = _Flight()
            _inflight[key] = flight
    if not leader:
        # Handed over in memory, not re-read from disk: the answer must reach
        # the waiters even when the store will not take the file.
        flight.done.wait()
        if flight.context is not None:
            return flight.context, True
        raise flight.error or ProviderUnavailableError("overpass", "the shared request failed")

    if fetch is None:
        from app.providers.vector.overpass import fetch_osm_context as fetch

    try:
        context: OsmContext = fetch(bounds)
        flight.context = context
    except ProviderUnavailableError as exc:
        flight.error = exc
        _remember_refusal(store, bounds, exc.detail)
        raise
    except Exception as exc:
        flight.error = ProviderUnavailableError("overpass", type(exc).__name__)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        flight.done.set()
    write(store, bounds, context)
    _refusal_path(store, bounds).unlink(missing_ok=True)
    return context, False


def _refusal_path(store: Path, bounds: tuple[float, float, float, float]) -> Path:
    target = path_for(store, bounds)
    return target.with_name(f"{target.stem}.refused.json")


def _remember_refusal(store: Path, bounds: tuple[float, float, float, float], reason: str) -> None:
    target = _refusal_path(store, bounds)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"refused_at": time.time(), "reason": reason[:500]}))
    except OSError as exc:
        log.warning("osm refusal not recorded", path=str(target), error=str(exc))


def _raise_if_recently_refused(store: Path, bounds: tuple[float, float, float, float]) -> None:
    try:
        payload = json.loads(_refusal_path(store, bounds).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    age = time.time() - float(payload.get("refused_at", 0.0))
    if age > FAILURE_TTL_S:
        return
    raise ProviderUnavailableError(
        "overpass",
        f"every mirror refused this area {int(age // 60)} min ago; it is asked again "
        f"after {FAILURE_TTL_S // 60} min rather than on every run",
    )
