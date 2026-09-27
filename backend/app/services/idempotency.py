"""Answer a repeated job submission with the job it already started.

The web page retries a job start whose connection stalls: from the campus Wi-Fi
about half of all new connections to the lab address hang, and a browser would
otherwise wait ~90 s on one. A retry may follow a request that did arrive and
only lost its answer, so each start carries an `Idempotency-Key`, and a key
seen in the last ten minutes gets the original `202` back instead of a second
job. Per instance: the gateway's affinity sends a browser's retries to the
instance that holds its first attempt.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any

#: How long a key is remembered. A retry comes within a minute; ten is ample.
TTL_S = 10 * 60
#: Bound on remembered keys, oldest dropped first.
MAX_KEYS = 1000

_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_lock = threading.Lock()
_seen: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()


def usable(key: str | None) -> bool:
    """A key the page could have sent; anything else is ignored, not refused."""
    return bool(key) and bool(_KEY.match(key or ""))


def recall(key: str | None) -> dict[str, Any] | None:
    """The response a recent start with this key was given, or None."""
    if not usable(key):
        return None
    now = time.monotonic()
    with _lock:
        entry = _seen.get(key)  # type: ignore[arg-type]
        if entry is None:
            return None
        if now - entry[0] > TTL_S:
            del _seen[key]  # type: ignore[arg-type]
            return None
        return dict(entry[1])


def remember(key: str | None, body: dict[str, Any]) -> None:
    if not usable(key):
        return
    with _lock:
        _seen[key] = (time.monotonic(), dict(body))  # type: ignore[index]
        _seen.move_to_end(key)  # type: ignore[arg-type]
        while len(_seen) > MAX_KEYS:
            _seen.popitem(last=False)


def clear() -> None:
    """Forget every key. For tests."""
    with _lock:
        _seen.clear()
