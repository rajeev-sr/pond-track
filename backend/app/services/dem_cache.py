"""Parsed contour uploads, keyed by `dem_id`.

Lifted out of `api/v1/contour.py` because there are now two callers. The
synchronous endpoint registered the DEM itself and stamped `dem_id` onto its
response body; the async job path did not, so an analysis run as a job came back
without one -- and `dem_id` is what every follow-up call needs. Streams, terrain
tiles, click-to-delineate and available-land all silently stopped working in the
browser the moment the upload flow moved to jobs.

Keeping the registry in the API module made that mistake easy: a service cannot
import an endpoint module without inverting the layering, so the job runner had
nowhere to register from.

In memory up to `DEM_CACHE_LIMIT` grids, most recently used kept; every grid is
also written to disk as it is registered. On a 512 MB lab system the limit is 1,
and an in-memory-only registry meant the second user's run evicted the first
user's terrain: their next click on the map answered 404. Now an evicted entry is
read back from disk, and so is one from before a restart. The disk copy holds the
grid, its bounds and its source -- what the follow-up routes read. The parsed
contour lines are not kept, so the contours route traces them from the grid for
an entry that was read back.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from app.core.logging import get_logger

log = get_logger("services.dem_cache")

#: Enough for a session's worth of uploads; each entry holds a full DEM grid.
#:
#: Overridable because "enough" depends on how much memory there is, and 16 live
#: DEM grids is a lot of it. The compose stack has room; a 512 MB container does
#: not, and there the cost is not a slow cache -- it is the OOM killer taking
#: uvicorn out mid-analysis, which surfaces in the browser as a bare 500 with no
#: problem details. Lowering this trades away follow-up calls on older uploads:
#: `dem_id` is what /hydrology/streams, terrain tiles, click-to-delineate and
#: available-land look up, so only the last DEM_CACHE_LIMIT uploads stay live.
CACHE_LIMIT = int(os.environ.get("DEM_CACHE_LIMIT", "16"))

#: Grids kept on disk before the oldest are removed. A 5 m sheet is ~1.4 MB and
#: a 100 km² area ~0.5 MB, so this is at most a few hundred megabytes of the
#: 16 GB a lab system has.
MAX_DISK_ENTRIES = 200

#: What a `dem_id` looks like. Checked before one is used as a filename: it
#: arrives in a request, and `../` must not reach the filesystem.
_DEM_ID = re.compile(r"^[0-9a-f]{16}$")

_entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()


def remember(
    parsed: Any,
    dem: Any,
    report: Any,
    *,
    bounds: Any = None,
    source: str = "uploaded_contour_map",
) -> str:
    """Register a DEM and return its `dem_id`.

    `parsed` and `report` are the contour upload's; a drawn area has neither,
    so it passes `None` and gives `bounds` itself. Every follow-up route reads
    `bounds` from here rather than off `parsed`, so both kinds of `dem_id` work
    for streams, tiles, click-to-delineate and available land alike.

    Thread-safe: a background job writes here while a request handler reads,
    and dict mutation from two threads can lose an entry.
    """
    if bounds is None:
        if parsed is None:
            raise ValueError("a DEM without parsed contours must be registered with its bounds")
        bounds = parsed.bounds
    dem_id = uuid.uuid4().hex[:16]
    entry = {"parsed": parsed, "dem": dem, "report": report, "bounds": bounds, "source": source}
    _keep(dem_id, entry)
    _write(dem_id, entry)
    return dem_id


def remember_analysis(analysis: Any) -> str:
    """Register the DEM behind a finished analysis, whichever way it came in.

    One call for the synchronous routes and the job runner alike, so the two can
    never disagree about what a `dem_id` holds -- the mistake this module was
    created to fix.
    """
    return remember(
        analysis.parsed,
        analysis.dem,
        analysis.interpolation,
        bounds=analysis.source.grid_bounds,
        source=analysis.source.kind,
    )


def get(dem_id: str) -> dict[str, Any] | None:
    """The entry for `dem_id`, from memory or, failing that, from disk."""
    with _lock:
        entry = _entries.get(dem_id)
        if entry is not None:
            _entries.move_to_end(dem_id)
            return entry
    entry = _read(dem_id)
    if entry is not None:
        _keep(dem_id, entry)
    return entry


def clear() -> None:
    """Drop the in-memory entries. For tests; the disk copies stay."""
    with _lock:
        _entries.clear()


def _keep(dem_id: str, entry: dict[str, Any]) -> None:
    with _lock:
        _entries[dem_id] = entry
        _entries.move_to_end(dem_id)
        while len(_entries) > max(1, CACHE_LIMIT):
            _entries.popitem(last=False)


def _store() -> Path:
    from app.config import get_settings

    return Path(get_settings().COG_STORE_PATH) / "dem_ids"


def _write(dem_id: str, entry: dict[str, Any]) -> None:
    """Best effort: a disk that will not take the grid costs only persistence."""
    dem = entry["dem"]
    b = entry["bounds"]
    meta = {
        "transform": [float(v) for v in dem.transform],
        "epsg": int(dem.epsg),
        "cell_size_m": float(dem.cell_size_m),
        "bounds": [float(b.min_lon), float(b.min_lat), float(b.max_lon), float(b.max_lat)],
        "source": entry["source"],
        "provenance": dem.provenance,
    }
    try:
        store = _store()
        store.mkdir(parents=True, exist_ok=True)
        tmp = store / f"{dem_id}.npz.tmp"
        # Uncompressed: a sheet is 1.4 MB either way to within a factor of two,
        # and compressing it would add a tenth of a second to every analysis.
        with tmp.open("wb") as handle:
            np.savez(handle, elevation=dem.elevation, meta=json.dumps(meta, default=str))
        tmp.replace(store / f"{dem_id}.npz")
        _prune(store)
    except OSError as exc:
        log.warning("dem_id not persisted", dem_id=dem_id, error=str(exc))


def _read(dem_id: str) -> dict[str, Any] | None:
    if not _DEM_ID.match(dem_id):
        return None
    path = _store() / f"{dem_id}.npz"
    if not path.is_file():
        return None
    try:
        from app.providers.elevation.base import Bounds, DemGrid

        with np.load(path, allow_pickle=False) as npz:
            meta = json.loads(str(npz["meta"]))
            dem = DemGrid(
                elevation=np.asarray(npz["elevation"], dtype=np.float32),
                transform=tuple(meta["transform"]),
                epsg=int(meta["epsg"]),
                cell_size_m=float(meta["cell_size_m"]),
                provenance=meta.get("provenance") or {},
            )
        return {
            "parsed": None,
            "dem": dem,
            "report": None,
            "bounds": Bounds(*meta["bounds"]),
            "source": meta.get("source", "uploaded_contour_map"),
        }
    except Exception as exc:  # a damaged file is a miss, not a failure
        log.warning("dem_id unreadable", dem_id=dem_id, error=str(exc))
        return None


def _prune(store: Path) -> None:
    files = sorted(store.glob("*.npz"), key=lambda f: f.stat().st_mtime)
    for old in files[: max(0, len(files) - MAX_DISK_ENTRIES)]:
        old.unlink(missing_ok=True)


def size() -> int:
    with _lock:
        return len(_entries)
