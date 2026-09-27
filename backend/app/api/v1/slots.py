"""The overload guard as the routes use it (see `services/capacity.py`)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.core.errors import BusyProblem
from app.core.memory import release_memory
from app.services import capacity


def _busy(detail: str) -> BusyProblem:
    slots = capacity.get_slots()
    return BusyProblem(
        detail=detail,
        headers={"Retry-After": str(capacity.RETRY_AFTER_S)},
        retry_after_s=capacity.RETRY_AFTER_S,
        analyses=slots.snapshot(),
    )


@contextmanager
def analysis_slot() -> Iterator[None]:
    """Hold one of this instance's analysis slots, or answer 503 at once."""
    slots = capacity.get_slots()
    if not slots.try_acquire():
        plural = "" if slots.limit == 1 else "es"
        raise _busy(
            f"this server is already running {slots.limit} analysis{plural}, which is all "
            f"its memory allows. Try again in about {capacity.RETRY_AFTER_S} s -- or start "
            "it as a job (POST /api/v1/analysis or /api/v1/analysis/area), which waits "
            "for a free slot instead of being refused."
        )
    try:
        yield
    finally:
        slots.release()
        release_memory()


def refuse_when_queue_full() -> None:
    """A job may wait for a slot, but not in an unbounded queue."""
    if capacity.get_slots().queue_full():
        raise _busy(
            f"{capacity.MAX_WAITING_JOBS} analyses are already waiting on this server. "
            f"Try again in about {capacity.RETRY_AFTER_S} s."
        )
