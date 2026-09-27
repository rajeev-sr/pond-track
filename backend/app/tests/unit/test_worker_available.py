"""Whether a Celery worker is there to take a job -- asked cheaply.

On the lab systems there is no broker, and the ping took ~6 s to fail on every
contour job submitted from the web application.
"""

from __future__ import annotations

import pytest

from app.workers import tasks


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from app.config import get_settings

    monkeypatch.setattr(tasks, "_no_worker_since", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pinging(monkeypatch: pytest.MonkeyPatch, replies: object) -> list[int]:
    calls: list[int] = []

    class Inspect:
        def ping(self) -> object:
            calls.append(1)
            if isinstance(replies, Exception):
                raise replies
            return replies

    monkeypatch.setattr(tasks.celery_app.control, "inspect", lambda timeout: Inspect())
    return calls


def test_no_broker_means_no_worker_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "")
    calls = pinging(monkeypatch, {"w1": "pong"})
    assert tasks.worker_available() is False
    assert calls == []


def test_a_failed_ping_is_trusted_for_a_while(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    calls = pinging(monkeypatch, ConnectionError("refused"))
    assert tasks.worker_available() is False
    assert tasks.worker_available() is False
    assert len(calls) == 1


def test_it_asks_again_once_that_is_old(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    calls = pinging(monkeypatch, {"w1": "pong"})
    monkeypatch.setattr(tasks, "_no_worker_since", time.monotonic() - tasks.NO_WORKER_TTL_S - 1)
    assert tasks.worker_available() is True
    assert len(calls) == 1


def test_a_worker_that_answers_is_asked_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the negative is remembered: a worker can die between two jobs."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    calls = pinging(monkeypatch, {"w1": "pong"})
    assert tasks.worker_available() is True
    assert tasks.worker_available() is True
    assert len(calls) == 2
