"""The analysis job state machine (HLD §3.7).

Two things carry the weight here. `PARTIAL` -- a required step failing is a
failure, an optional one failing is a degraded but usable answer -- and the
progress weighting, because a bar that reaches 57 % and then sits still for
twenty seconds reads as a hang, which is worse than showing nothing.
"""

from __future__ import annotations

import pytest

from app.services import jobs
from app.services.jobs import IllegalTransitionError, JobProgress


def run_everything(p: JobProgress, *, skip: tuple[str, ...] = ()) -> None:
    for step in jobs.STEPS:
        if step.name in skip:
            continue
        p.start_step(step.name)
        p.finish_step(step.name)


class TestTheStepTable:
    def test_the_weights_sum_to_one(self) -> None:
        assert sum(s.weight for s in jobs.STEPS) == pytest.approx(1.0, abs=1e-9)

    def test_enrichment_dominates_a_cold_run(self) -> None:
        """20.0 s of 24.3 s measured. The bar is shaped by this fact or it lies."""
        assert jobs.STEPS_BY_NAME["enrichment"].weight > 0.75

    def test_enrichment_is_the_only_optional_step(self) -> None:
        # It is the only one that talks to third-party providers, so it is the
        # only one whose failure the analysis can absorb.
        assert jobs.OPTIONAL_STEPS == ("enrichment",)

    def test_every_required_step_is_local_computation(self) -> None:
        assert set(jobs.REQUIRED_STEPS) == {
            "parse",
            "interpolate",
            "condition",
            "flow_routing",
            "siting",
            "catchments",
        }

    def test_an_optional_step_says_what_is_lost_without_it(self) -> None:
        """ "enrichment failed" tells a reader nothing they can act on."""
        for name in jobs.OPTIONAL_STEPS:
            assert jobs.STEPS_BY_NAME[name].degrades_to


class TestLegalTransitions:
    def test_the_happy_path(self) -> None:
        p = JobProgress()
        assert p.state == "queued"
        p.start()
        assert p.state == "running"
        run_everything(p)
        assert p.settle() == "done"

    def test_a_terminal_state_never_moves_again(self) -> None:
        for terminal in sorted(jobs.TERMINAL_STATES):
            assert jobs.LEGAL_TRANSITIONS[terminal] == frozenset(), terminal

    def test_a_done_job_cannot_go_back_to_running(self) -> None:
        """Two workers racing must not resurrect a finished job."""
        with pytest.raises(IllegalTransitionError, match="terminal"):
            jobs.transition("done", "running")

    def test_queued_cannot_jump_straight_to_done(self) -> None:
        with pytest.raises(IllegalTransitionError):
            jobs.transition("queued", "done")

    def test_the_same_state_written_twice_is_idempotent(self) -> None:
        """A worker reporting `running` again is normal, not an error."""
        assert jobs.transition("running", "running") == "running"

    def test_a_job_can_be_cancelled_from_any_live_state(self) -> None:
        for live in ("queued", "running", "retrying"):
            assert jobs.can_transition(live, "cancelled"), live

    def test_retrying_returns_to_running_or_gives_up(self) -> None:
        assert jobs.LEGAL_TRANSITIONS["retrying"] == frozenset({"running", "failed", "cancelled"})

    def test_every_state_has_a_stated_meaning(self) -> None:
        assert set(jobs.STATE_MEANING) == set(jobs.LEGAL_TRANSITIONS)


class TestPartialIsWhatMakesItUsable:
    def test_a_failed_optional_step_settles_to_partial(self) -> None:
        p = JobProgress()
        p.start()
        for step in jobs.STEPS:
            p.start_step(step.name)
            if step.name == "enrichment":
                p.fail_step(step.name, "SoilGrids timed out")
            else:
                p.finish_step(step.name)
        assert p.settle() == "partial"

    def test_a_failed_required_step_settles_to_failed(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("parse")
        p.fail_step("parse", "the KML has no elevations")
        assert p.settle() == "failed"

    def test_partial_carries_a_warning_naming_the_cause_and_the_cost(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.fail_step("enrichment", "SoilGrids timed out")
        assert len(p.warnings) == 1
        warning = p.warnings[0]
        assert "SoilGrids timed out" in warning, "the cause is missing"
        assert "assumed soil group" in warning, "what was lost is missing"

    def test_a_required_failure_adds_no_degradation_warning(self) -> None:
        """There is nothing to continue with, so promising less would be false."""
        p = JobProgress()
        p.start()
        p.start_step("interpolate")
        p.fail_step("interpolate", "no contours in range")
        assert p.warnings == []

    def test_both_kinds_failing_is_a_failure_not_a_partial(self) -> None:
        p = JobProgress()
        p.start()
        for name in ("enrichment", "siting"):
            p.start_step(name)
            p.fail_step(name, "boom")
        assert p.settle() == "failed"

    def test_settling_with_work_outstanding_is_refused(self) -> None:
        """Reporting `done` with steps still pending would be a silent lie."""
        p = JobProgress()
        p.start()
        p.start_step("parse")
        p.finish_step("parse")
        with pytest.raises(RuntimeError, match="outstanding"):
            p.settle()

    def test_settling_twice_is_stable(self) -> None:
        p = JobProgress()
        p.start()
        run_everything(p)
        assert p.settle() == "done"
        assert p.settle() == "done"


class TestProgressTracksTimeNotStepCount:
    def test_it_starts_at_zero_and_ends_at_one_hundred(self) -> None:
        p = JobProgress()
        assert p.progress_pct == 0
        p.start()
        run_everything(p)
        p.settle()
        assert p.progress_pct == 100

    def test_it_never_reports_one_hundred_before_settling(self) -> None:
        """100 % on a job that is still running invites the client to stop polling."""
        p = JobProgress()
        p.start()
        run_everything(p)
        assert p.progress_pct == 99
        p.settle()
        assert p.progress_pct == 100

    def test_the_four_steps_before_enrichment_are_worth_little(self) -> None:
        """Equal weighting would put this at 57 %, then freeze for 20 seconds."""
        p = JobProgress()
        p.start()
        for name in ("parse", "interpolate", "condition", "flow_routing"):
            p.start_step(name)
            p.finish_step(name)
        assert p.progress_pct < 15, f"{p.progress_pct}% overstates four cheap steps"

    def test_finishing_enrichment_is_most_of_the_bar(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.finish_step("enrichment")
        assert p.progress_pct > 80

    def test_a_failed_optional_step_still_advances_the_bar(self) -> None:
        """The pipeline is not coming back to it, so its share is not outstanding.

        Left unclaimed, a job whose enrichment failed would sit near zero for the
        rest of a run that is in fact almost finished.
        """
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.fail_step("enrichment", "provider down")
        assert p.progress_pct > 80

    def test_it_is_monotonic_across_a_whole_run(self) -> None:
        p = JobProgress()
        p.start()
        seen = [p.progress_pct]
        for step in jobs.STEPS:
            p.start_step(step.name)
            seen.append(p.progress_pct)
            p.finish_step(step.name)
            seen.append(p.progress_pct)
        p.settle()
        seen.append(p.progress_pct)
        assert seen == sorted(seen), f"progress went backwards: {seen}"

    def test_it_stays_within_bounds(self) -> None:
        p = JobProgress()
        for step in jobs.STEPS:
            p.start_step(step.name)
            p.fail_step(step.name, "boom")
            assert 0 <= p.progress_pct <= 99


class TestRetries:
    def test_a_retry_raises_the_attempt_and_says_so(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.retry("enrichment", "HTTP 504")
        assert p.state == "retrying"
        assert p.attempt == 2
        assert "HTTP 504" in p.warnings[0]

    def test_the_next_step_resumes_running(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.retry("enrichment", "HTTP 504")
        p.start_step("enrichment")
        assert p.state == "running"


class TestTheStatusPayload:
    def test_it_lists_every_step_with_its_outcome(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("parse")
        p.finish_step("parse")
        p.start_step("interpolate")
        block = p.as_dict()
        assert [s["name"] for s in block["steps"]] == [s.name for s in jobs.STEPS]
        outcomes = {s["name"]: s["outcome"] for s in block["steps"]}
        assert outcomes["parse"] == "done"
        assert outcomes["interpolate"] == "running"
        assert outcomes["siting"] == "pending"

    def test_it_labels_the_current_step_for_a_human(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        block = p.as_dict()
        assert block["current_step"] == "enrichment"
        assert block["current_step_label"] == "Fetching soil, land cover and rainfall"

    def test_it_says_whether_polling_can_stop(self) -> None:
        p = JobProgress()
        assert p.as_dict()["is_terminal"] is False
        p.start()
        run_everything(p)
        p.settle()
        assert p.as_dict()["is_terminal"] is True

    def test_it_explains_the_state_rather_than_only_naming_it(self) -> None:
        p = JobProgress()
        p.start()
        p.start_step("enrichment")
        p.fail_step("enrichment", "down")
        for step in jobs.STEPS:
            if p.outcomes[step.name] == "pending":
                p.start_step(step.name)
                p.finish_step(step.name)
        p.settle()
        block = p.as_dict()
        assert block["state"] == "partial"
        assert "usable" in block["state_meaning"]


class TestUnknownSteps:
    def test_starting_an_unknown_step_is_refused(self) -> None:
        with pytest.raises(KeyError, match="unknown step"):
            JobProgress().start_step("magic")

    def test_failing_an_unknown_step_is_refused(self) -> None:
        with pytest.raises(KeyError):
            JobProgress().fail_step("magic", "boom")
