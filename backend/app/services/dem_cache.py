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

In-process and bounded, as before: this is a local single-node deployment, and
the DEM held here is a live NumPy grid rather than something serialisable, so it
cannot go in Redis alongside the job record.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

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

_entries: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def remember(parsed: Any, dem: Any, report: Any) -> str:
    """Register a parsed upload and return its `dem_id`.

    Thread-safe: a background job writes here while a request handler reads,
    and dict mutation from two threads can lose an entry.
    """
    dem_id = uuid.uuid4().hex[:16]
    with _lock:
        while len(_entries) >= CACHE_LIMIT:
            _entries.pop(next(iter(_entries)))
        _entries[dem_id] = {"parsed": parsed, "dem": dem, "report": report}
    return dem_id


def get(dem_id: str) -> dict[str, Any] | None:
    with _lock:
        return _entries.get(dem_id)


def clear() -> None:
    """Drop everything. For tests."""
    with _lock:
        _entries.clear()


def size() -> int:
    with _lock:
        return len(_entries)
