"""How many analyses this process runs at once (`MAX_CONCURRENT_ANALYSES`).

One analysis of the sample sheet peaks at a few hundred megabytes, and a lab
system has 512 MB in all: two at once is how uvicorn was OOM-killed on the
deployment, and a killed process answers nobody. So an analysis takes a slot.

- A **synchronous** request that finds every slot taken is answered at once:
  503 with `Retry-After`. "Busy, try again in 15 s" is an answer; a dead
  process is not.
- A **job** waits for a slot, and its status says `queued` meanwhile -- which
  is what that state is for. The number allowed to wait is bounded too, because
  each waiting job holds a worker thread.
"""

from __future__ import annotations

import threading
from typing import Any

#: Jobs allowed to wait for a slot before new ones are refused with 503. Each
#: holds a threadpool worker while it waits; the pool is 40 threads, and the
#: synchronous routes need some of them too.
MAX_WAITING_JOBS = 8

#: What a busy answer tells the caller to wait: about one warm analysis of the
#: sample sheet on a lab system (13.7 s measured).
RETRY_AFTER_S = 15


class AnalysisSlots:
    """A counting semaphore that can say how busy it is."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"at least one analysis slot is needed, got {limit}")
        self.limit = limit
        self._slots = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._running = 0
        self._waiting = 0

    def try_acquire(self) -> bool:
        """Take a slot if one is free, without waiting."""
        if not self._slots.acquire(blocking=False):
            return False
        with self._lock:
            self._running += 1
        return True

    def acquire(self, timeout: float | None = None) -> bool:
        """Wait for a slot; False only if `timeout` ran out first."""
        with self._lock:
            self._waiting += 1
        try:
            # `timeout=None` blocks until a slot frees, which is the job's case.
            got = self._slots.acquire(timeout=timeout)
        finally:
            with self._lock:
                self._waiting -= 1
        if got:
            with self._lock:
                self._running += 1
        return got

    def release(self) -> None:
        with self._lock:
            self._running -= 1
        self._slots.release()

    def queue_full(self) -> bool:
        with self._lock:
            return self._waiting >= MAX_WAITING_JOBS

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"limit": self.limit, "running": self._running, "waiting": self._waiting}


_slots: AnalysisSlots | None = None
_slots_lock = threading.Lock()


def get_slots() -> AnalysisSlots:
    """This process's slots, sized from settings on first use."""
    global _slots
    with _slots_lock:
        if _slots is None:
            from app.config import get_settings

            _slots = AnalysisSlots(int(get_settings().MAX_CONCURRENT_ANALYSES))
        return _slots


def reset_slots() -> None:
    """Forget the slots so the next use re-reads settings. For tests."""
    global _slots
    with _slots_lock:
        _slots = None
