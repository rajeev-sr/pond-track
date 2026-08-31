"""The job lifecycle and its store, end to end without a broker (M6-2..M6-5).

`run_analysis_job` is deliberately plain synchronous code so it can be driven
here on synthetic contour maps. The cases that matter are the terminal states:
`done` when everything worked, `partial` when only an optional layer was lost,
`failed` when a required step died -- and never a job left mid-run, because an
unsettled job is indistinguishable from a hang and gets polled for ever.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.services import jobs
from app.services.job_runner import run_analysis_job
from app.services.job_store import JobRecord, MemoryJobStore
from app.tests.synthetic_kml import build_kml, concentric_rings


@pytest.fixture
def store() -> MemoryJobStore:
    return MemoryJobStore()


@pytest.fixture
def kml() -> bytes:
    return build_kml(concentric_rings())


class TestTheStore:
    def test_a_record_round_trips(self, store: MemoryJobStore) -> None:
        record = JobRecord(job_id="abc", progress={"state": "queued"}, created_at=1.0)
        store.put(record)
        back = store.get("abc")
        assert back is not None and back.progress == {"state": "queued"}

    def test_a_missing_job_is_none(self, store: MemoryJobStore) -> None:
        assert store.get("nope") is None

    def test_delete_reports_whether_anything_went(self, store: MemoryJobStore) -> None:
        store.put(JobRecord(job_id="abc", progress={}))
        assert store.delete("abc") is True
        assert store.delete("abc") is False

    def test_an_expired_record_is_gone(self) -> None:
        expiring = MemoryJobStore(ttl_s=-1.0)
        expiring.put(JobRecord(job_id="abc", progress={}))
        assert expiring.get("abc") is None

    def test_elapsed_is_none_before_the_job_starts(self) -> None:
        assert JobRecord(job_id="a", progress={}).elapsed_s is None

    def test_elapsed_freezes_when_the_job_finishes(self) -> None:
        record = JobRecord(job_id="a", progress={}, started_at=100.0, finished_at=112.5)
        assert record.elapsed_s == pytest.approx(12.5)

    def test_elapsed_runs_while_the_job_is_live(self) -> None:
        record = JobRecord(job_id="a", progress={}, started_at=time.time() - 3.0)
        assert record.elapsed_s is not None and record.elapsed_s >= 3.0

    def test_an_unknown_field_in_a_stored_record_is_ignored(self) -> None:
        """A record written by a newer version must not break an older reader."""
        raw = {"job_id": "a", "progress": {}, "some_future_field": 1}
        assert JobRecord.from_dict(raw).job_id == "a"


class TestASuccessfulRun:
    def test_it_settles_to_done(self, store: MemoryJobStore, kml: bytes) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert record.progress["state"] == "done"

    def test_the_job_is_findable_in_the_store(self, store: MemoryJobStore, kml: bytes) -> None:
        run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert store.get("j1") is not None

    def test_it_reaches_one_hundred_percent(self, store: MemoryJobStore, kml: bytes) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert record.progress["progress_pct"] == 100
        assert record.progress["is_terminal"] is True

    def test_every_step_finished(self, store: MemoryJobStore, kml: bytes) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        outcomes = {s["name"]: s["outcome"] for s in record.progress["steps"]}
        assert all(o in ("done", "skipped") for o in outcomes.values()), outcomes

    def test_the_result_is_the_same_shape_the_sync_endpoint_returns(
        self, store: MemoryJobStore, kml: bytes
    ) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert record.result is not None
        for key in ("contour_map", "candidate_sites", "environment", "interpolated_terrain"):
            assert key in record.result, key

    def test_it_records_when_it_started_and_finished(
        self, store: MemoryJobStore, kml: bytes
    ) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert record.started_at is not None and record.finished_at is not None
        assert record.elapsed_s is not None and record.elapsed_s >= 0.0


class TestARequestedSkipIsNotADegradation:
    def test_enrich_false_settles_to_done_not_partial(
        self, store: MemoryJobStore, kml: bytes
    ) -> None:
        """The caller asked for terrain-only, so they got what they asked for.

        Reporting their own choice back as PARTIAL would tell them the answer is
        degraded when nothing failed.
        """
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert record.progress["state"] == "done"

    def test_the_skip_is_still_visible(self, store: MemoryJobStore, kml: bytes) -> None:
        record = run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        outcomes = {s["name"]: s["outcome"] for s in record.progress["steps"]}
        assert outcomes["enrichment"] == "skipped"
        assert any("skipped" in w for w in record.progress["warnings"])


class TestAFailedRun:
    def test_unusable_input_settles_to_failed(self, store: MemoryJobStore) -> None:
        record = run_analysis_job("j1", b"not a kml at all", "junk.kml", {}, store=store)
        assert record.progress["state"] == "failed"

    def test_it_names_the_step_that_died(self, store: MemoryJobStore) -> None:
        record = run_analysis_job("j1", b"not a kml at all", "junk.kml", {}, store=store)
        outcomes = {s["name"]: s["outcome"] for s in record.progress["steps"]}
        assert outcomes["parse"] == "failed", outcomes

    def test_the_error_is_problem_shaped(self, store: MemoryJobStore) -> None:
        record = run_analysis_job("j1", b"not a kml at all", "junk.kml", {}, store=store)
        error = record.progress["error"]
        assert error is not None
        for key in ("type", "title", "detail"):
            assert key in error, key

    def test_it_does_not_raise(self, store: MemoryJobStore) -> None:
        """A raise would lose the job instead of reporting it."""
        record = run_analysis_job("j1", b"", "empty.kml", {}, store=store)
        assert record.progress["is_terminal"] is True

    def test_bad_options_still_settle(self, store: MemoryJobStore, kml: bytes) -> None:
        """The throw happens outside any stage, so nothing has recorded a failure.

        Without the force-settle path this left the job unsettleable, which means
        polled for ever.
        """
        record = run_analysis_job("j1", kml, "rings.kml", {"no_such_option": 1}, store=store)
        assert record.progress["state"] == "failed"
        assert record.progress["is_terminal"] is True

    def test_a_failed_job_has_no_result(self, store: MemoryJobStore) -> None:
        record = run_analysis_job("j1", b"junk", "junk.kml", {}, store=store)
        assert record.result is None


class TestPartialWhenAProviderIsLost:
    def test_a_degraded_tier_settles_to_partial(
        self, store: MemoryJobStore, kml: bytes, monkeypatch: Any
    ) -> None:
        """Enrichment never raises for an outage -- it degrades and reports.

        So PARTIAL cannot be found by catching an exception; it is read off the
        tier. This stubs a fetch that loses soil and land cover the way a
        SoilGrids outage does.
        """
        from app.services import contour_analysis
        from app.services.enrichment import Enrichment

        def degraded(*_a: Any, **_k: Any) -> Enrichment:
            return Enrichment(
                failures=[
                    {"layer": "soil", "provider": "SoilGrids", "reason": "HTTP 504"},
                    {"layer": "land_cover", "provider": "ESA WorldCover", "reason": "timeout"},
                ]
            )

        monkeypatch.setattr(contour_analysis, "fetch_enrichment", degraded)
        record = run_analysis_job("j1", kml, "rings.kml", {}, store=store)

        assert record.progress["state"] == "partial"
        outcomes = {s["name"]: s["outcome"] for s in record.progress["steps"]}
        assert outcomes["enrichment"] == "failed"
        assert all(outcomes[n] == "done" for n in jobs.REQUIRED_STEPS), outcomes

    def test_partial_still_carries_a_result(
        self, store: MemoryJobStore, kml: bytes, monkeypatch: Any
    ) -> None:
        """The whole point of the state: a usable answer, flagged."""
        from app.services import contour_analysis
        from app.services.enrichment import Enrichment

        monkeypatch.setattr(
            contour_analysis,
            "fetch_enrichment",
            lambda *a, **k: Enrichment(
                failures=[{"layer": "soil", "provider": "SoilGrids", "reason": "HTTP 504"}]
            ),
        )
        record = run_analysis_job("j1", kml, "rings.kml", {}, store=store)
        assert record.result is not None
        assert record.result["candidate_sites"] is not None

    def test_the_warning_names_the_provider_and_what_was_lost(
        self, store: MemoryJobStore, kml: bytes, monkeypatch: Any
    ) -> None:
        from app.services import contour_analysis
        from app.services.enrichment import Enrichment

        monkeypatch.setattr(
            contour_analysis,
            "fetch_enrichment",
            lambda *a, **k: Enrichment(
                failures=[{"layer": "soil", "provider": "SoilGrids", "reason": "HTTP 504"}]
            ),
        )
        record = run_analysis_job("j1", kml, "rings.kml", {}, store=store)
        warnings = " ".join(record.progress["warnings"])
        assert "SoilGrids" in warnings
        assert "assumed soil group" in warnings


class TestProgressIsWrittenAsItGoes:
    def test_the_store_sees_more_than_one_update(self, store: MemoryJobStore, kml: bytes) -> None:
        """A bar that is only written at the end is not a progress bar."""
        seen: list[int] = []
        original = store.put

        def spy(record: JobRecord) -> None:
            seen.append(int(record.progress.get("progress_pct", 0)))
            original(record)

        store.put = spy  # type: ignore[method-assign]
        run_analysis_job("j1", kml, "rings.kml", {"enrich": False}, store=store)
        assert len(seen) > 5, f"only {len(seen)} progress writes"
        assert seen == sorted(seen), f"progress went backwards: {seen}"
        assert seen[-1] == 100
