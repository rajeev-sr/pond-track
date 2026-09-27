"""The overload guard: one analysis at a time on a 512 MB system.

Two concurrent analyses are how uvicorn was OOM-killed on the deployment, and a
killed process answers nobody. So a synchronous analysis that finds the slot
taken is answered at once with 503 + Retry-After, and a job waits in `queued`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.providers.elevation import copernicus_aws
from app.services import capacity
from app.services.job_runner import run_area_job
from app.services.job_store import MemoryJobStore
from app.tests.area_fakes import DURG_BBOX, FakeFetch


@pytest.fixture(autouse=True)
def fresh_slots() -> Iterator[None]:
    capacity.reset_slots()
    yield
    capacity.reset_slots()


@pytest.fixture
def busy() -> Iterator[capacity.AnalysisSlots]:
    """Every slot taken, as if another analysis were running."""
    slots = capacity.get_slots()
    held = 0
    while slots.try_acquire():
        held += 1
    yield slots
    for _ in range(held):
        slots.release()


class TestTheSlots:
    def test_one_slot_by_default(self) -> None:
        slots = capacity.get_slots()
        assert slots.limit == 1
        assert slots.try_acquire()
        assert not slots.try_acquire()
        slots.release()
        assert slots.try_acquire()
        slots.release()

    def test_the_limit_comes_from_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import get_settings

        monkeypatch.setenv("MAX_CONCURRENT_ANALYSES", "3")
        get_settings.cache_clear()
        capacity.reset_slots()
        try:
            assert capacity.get_slots().limit == 3
        finally:
            get_settings.cache_clear()

    def test_a_waiter_gets_the_slot_when_it_frees(self) -> None:
        slots = capacity.AnalysisSlots(1)
        assert slots.try_acquire()
        got: list[bool] = []
        waiter = threading.Thread(target=lambda: got.append(slots.acquire(timeout=5)))
        waiter.start()
        deadline = time.time() + 2
        while slots.snapshot()["waiting"] == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert slots.snapshot() == {"limit": 1, "running": 1, "waiting": 1}
        slots.release()
        waiter.join(timeout=5)
        assert got == [True]
        assert slots.snapshot() == {"limit": 1, "running": 1, "waiting": 0}

    def test_a_wait_can_time_out(self) -> None:
        slots = capacity.AnalysisSlots(1)
        assert slots.try_acquire()
        assert slots.acquire(timeout=0.05) is False
        assert slots.snapshot()["waiting"] == 0

    def test_zero_slots_is_refused(self) -> None:
        with pytest.raises(ValueError):
            capacity.AnalysisSlots(0)


class TestSynchronousRoutesSayBusy:
    def test_analyze_area_is_503_with_retry_after(
        self, client: Any, busy: capacity.AnalysisSlots, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetch = FakeFetch()
        monkeypatch.setattr(copernicus_aws, "fetch_dem", fetch)
        response = client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False})
        assert response.status_code == 503
        assert response.headers["retry-after"] == str(capacity.RETRY_AFTER_S)
        body = response.json()
        assert body["type"] == "/errors/busy"
        assert body["analyses"]["running"] == body["analyses"]["limit"]
        assert "job" in body["detail"]  # it says what to do instead
        assert fetch.calls == 0

    def test_a_bad_box_is_still_a_400_when_busy(
        self, client: Any, busy: capacity.AnalysisSlots
    ) -> None:
        response = client.post("/api/v1/analyzeArea", json={"bbox": [81.3, 21.2, 81.2, 21.3]})
        assert response.status_code == 400

    def test_analyze_contour_is_503_when_busy(
        self, client: Any, busy: capacity.AnalysisSlots
    ) -> None:
        from app.tests.synthetic_kml import build_kml, concentric_rings

        files = {"contour_map": ("rings.kml", build_kml(concentric_rings()))}
        response = client.post("/api/v1/analyzeContour", files=files, data={"enrich": "false"})
        assert response.status_code == 503
        assert "retry-after" in response.headers

    def test_the_slot_is_given_back_after_an_analysis(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(copernicus_aws, "fetch_dem", FakeFetch())
        for _ in range(2):
            response = client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False})
            assert response.status_code == 200
        assert capacity.get_slots().snapshot()["running"] == 0

    def test_the_slot_is_given_back_after_a_failure(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def sea(*_a: Any, **_k: Any) -> Any:
            raise copernicus_aws.NoTerrainError("copernicus_dem_glo30", "open water")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", sea)
        assert client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX}).status_code == 422
        assert capacity.get_slots().snapshot()["running"] == 0


class TestJobsWaitTheirTurn:
    def test_a_job_stays_queued_until_the_slot_frees(self) -> None:
        store = MemoryJobStore()
        slots = capacity.get_slots()
        assert slots.try_acquire()  # another analysis is running
        worker = threading.Thread(
            target=run_area_job,
            args=("job-q", DURG_BBOX, {"enrich": False}),
            kwargs={"store": store, "fetch": FakeFetch()},
        )
        worker.start()
        try:
            deadline = time.time() + 5
            while store.get("job-q") is None and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            record = store.get("job-q")
            assert record is not None
            assert record.progress["state"] == "queued"
        finally:
            slots.release()
        worker.join(timeout=60)
        record = store.get("job-q")
        assert record is not None
        assert record.progress["state"] in ("done", "partial")
        assert slots.snapshot()["running"] == 0

    def test_a_full_queue_refuses_new_jobs_with_503(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(capacity, "MAX_WAITING_JOBS", 0)
        response = client.post("/api/v1/analysis/area", json={"bbox": DURG_BBOX, "enrich": False})
        assert response.status_code == 503
        assert response.json()["type"] == "/errors/busy"


class TestMemoryIsHandedBackAfterAnAnalysis:
    """An instance kept ~100 MB after each analysis; on 512 MB with Postgres
    beside it, that residue decided whether the next one fitted."""

    def test_release_memory_is_safe_to_call(self) -> None:
        from app.core.memory import release_memory

        release_memory()  # no exception, on glibc or not

    def test_a_synchronous_analysis_releases_memory(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import slots as slot_module

        calls: list[int] = []
        monkeypatch.setattr(slot_module, "release_memory", lambda: calls.append(1))
        monkeypatch.setattr(copernicus_aws, "fetch_dem", FakeFetch())
        client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False})
        assert calls == [1]

    def test_a_job_releases_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services import job_runner

        calls: list[int] = []
        monkeypatch.setattr(job_runner, "release_memory", lambda: calls.append(1))
        run_area_job(
            "job-m", DURG_BBOX, {"enrich": False}, store=MemoryJobStore(), fetch=FakeFetch()
        )
        assert calls == [1]
