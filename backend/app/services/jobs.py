"""Analysis job state machine and progress model (HLD §3.7, M6-2..M6-4).

Three things live here, all of them pure logic so they can be tested without a
broker, a worker or a database:

* **The state machine.** Which transitions are legal. Without this, "progress"
  is whatever the last writer happened to set, and a job can go from `done` back
  to `running` because two workers raced.
* **Progress weighted by measured cost.** Steps are not equal. On the Durg sheet
  enrichment is 20.0 s of a 24.3 s run -- 82 % of it -- so a bar that gave each
  of the seven steps an equal share would race to 57 % and then sit motionless
  for twenty seconds, which is worse than no bar because it reads as a hang.
  The weights below are measured fractions, not guesses.
* **What `PARTIAL` means.** A required step failing is a failure. An *optional*
  step failing -- SoilGrids unreachable, Overpass rate-limited -- must still
  return the answer that terrain and rainfall can support, flagged. That is the
  difference between a demo and a tool (HLD NFR-5), and it is a decision about
  which steps are optional, so it belongs in one table rather than scattered
  across exception handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

JobState = Literal["queued", "running", "retrying", "partial", "done", "failed", "cancelled"]

#: Once a job reaches one of these it never moves again. Polling can stop.
TERMINAL_STATES: frozenset[JobState] = frozenset({"done", "partial", "failed", "cancelled"})

#: Exactly the arrows in the HLD §3.7 diagram, and nothing else.
LEGAL_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    # `retrying` is reachable from running and returns to it; a job may also end
    # directly from running when a required step fails outright.
    "running": frozenset({"retrying", "done", "partial", "failed", "cancelled"}),
    "retrying": frozenset({"running", "failed", "cancelled"}),
    "done": frozenset(),
    "partial": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

STATE_MEANING: dict[JobState, str] = {
    "queued": "accepted, waiting for a free worker",
    "running": "executing; the step and percentage advance",
    "retrying": "a provider failed transiently; backing off before another attempt",
    "partial": "the core steps succeeded but an optional enrichment did not -- "
    "the result is usable, and the warnings say what is missing",
    "done": "every step succeeded",
    "failed": "a required step failed after its retries",
    "cancelled": "abandoned by the caller",
}


class IllegalTransitionError(RuntimeError):
    """A move the state machine does not allow, named so the log is useful."""

    def __init__(self, current: JobState, target: JobState) -> None:
        allowed = sorted(LEGAL_TRANSITIONS[current])
        super().__init__(
            f"cannot move a job from {current!r} to {target!r}; "
            + (f"legal moves are {allowed}" if allowed else f"{current!r} is terminal")
        )
        self.current = current
        self.target = target


@dataclass(frozen=True)
class Step:
    """One stage of the pipeline.

    `weight` is its measured share of a cold run, so the reported percentage
    tracks elapsed time rather than step count. `optional` decides whether its
    failure ends the job or only degrades it.
    """

    name: str
    label: str
    weight: float
    optional: bool = False
    #: What is lost if this step is skipped -- shown in the PARTIAL warning
    #: rather than a bare "enrichment failed".
    degrades_to: str | None = None


#: Measured on the 6.7 MB Durg sheet (650 x 527 at 5 m), cold caches:
#: parse 0.229 · interpolate 1.367 · condition 0.801 · flow_routing 0.309 ·
#: enrichment 20.016 · siting 0.184 · catchments 1.375 = 24.281 s.
#:
#: Deliberately the *cold* figures. A warm run finishes in about four seconds and
#: the bar's shape stops mattering; the cold run is the one a person waits
#: through, so it is the one the weights are for.
STEPS: tuple[Step, ...] = (
    Step("parse", "Reading the contour map", 0.0094),
    Step("interpolate", "Interpolating terrain", 0.0563),
    Step("condition", "Conditioning the surface", 0.0330),
    Step("flow_routing", "Routing flow", 0.0127),
    Step(
        "enrichment",
        "Fetching soil, land cover and rainfall",
        0.8244,
        optional=True,
        degrades_to="terrain-only scoring, with an assumed soil group",
    ),
    Step("siting", "Scoring candidate sites", 0.0076),
    Step("catchments", "Delineating catchments and sizing ponds", 0.0566),
)

STEPS_BY_NAME: dict[str, Step] = {s.name: s for s in STEPS}

#: The steps a usable answer cannot be produced without.
REQUIRED_STEPS: tuple[str, ...] = tuple(s.name for s in STEPS if not s.optional)
OPTIONAL_STEPS: tuple[str, ...] = tuple(s.name for s in STEPS if s.optional)


def can_transition(current: JobState, target: JobState) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


def transition(current: JobState, target: JobState) -> JobState:
    """`target` if the move is legal, else raise. Same-state writes are allowed.

    A repeated write of the same state is idempotent rather than an error: a
    worker reporting `running` twice is normal, and refusing it would turn a
    harmless duplicate into a crash.
    """
    if current == target:
        return target
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)
    return target


StepOutcome = Literal["pending", "running", "done", "failed", "skipped"]


@dataclass
class JobProgress:
    """Mutable progress for one job, and the source of its reported state."""

    state: JobState = "queued"
    #: step name -> outcome
    outcomes: dict[str, StepOutcome] = field(
        default_factory=lambda: {s.name: "pending" for s in STEPS}
    )
    current_step: str | None = None
    attempt: int = 1
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    # ── transitions ──────────────────────────────────────────────────────────

    def to(self, target: JobState) -> None:
        self.state = transition(self.state, target)

    def start(self) -> None:
        self.to("running")

    def start_step(self, name: str) -> None:
        self._check(name)
        if self.state == "retrying" or self.state == "queued":
            self.to("running")
        self.outcomes[name] = "running"
        self.current_step = name

    def finish_step(self, name: str) -> None:
        self._check(name)
        self.outcomes[name] = "done"
        if self.current_step == name:
            self.current_step = None

    def skip_step(self, name: str, reason: str) -> None:
        """Record a step the caller asked not to run.

        Distinct from `fail_step` on purpose. `PARTIAL` means an optional step
        *failed*; a caller passing `enrich=false` got exactly what they asked
        for, and reporting their own choice back to them as a degraded result
        would be wrong. A skip earns the step's progress share and settles to
        `done`.
        """
        self._check(name)
        self.outcomes[name] = "skipped"
        self.warnings.append(f"{STEPS_BY_NAME[name].label.lower()} skipped ({reason})")
        if self.current_step == name:
            self.current_step = None

    def fail_step(self, name: str, reason: str) -> None:
        """Record a step failure. Whether the job survives depends on the table."""
        self._check(name)
        step = STEPS_BY_NAME[name]
        self.outcomes[name] = "failed"
        if step.optional:
            lost = step.degrades_to or "reduced detail"
            self.warnings.append(f"{step.label.lower()} failed ({reason}); continuing with {lost}")
        if self.current_step == name:
            self.current_step = None

    def retry(self, name: str, reason: str) -> None:
        self._check(name)
        self.attempt += 1
        self.warnings.append(f"retrying {name} after {reason} (attempt {self.attempt})")
        self.to("retrying")

    def cancel(self) -> None:
        self.to("cancelled")

    def _check(self, name: str) -> None:
        if name not in STEPS_BY_NAME:
            raise KeyError(f"unknown step {name!r}; expected one of {sorted(STEPS_BY_NAME)}")

    # ── derived values ───────────────────────────────────────────────────────

    @property
    def progress_pct(self) -> int:
        """Weighted completion, 0-100.

        A failed optional step counts as *finished* rather than as outstanding
        work: the pipeline is not going to come back to it, so leaving its 82 %
        unclaimed would strand the bar near zero for the rest of an analysis
        that is in fact nearly done.
        """
        if self.state in ("done", "partial"):
            return 100
        earned = sum(
            STEPS_BY_NAME[name].weight
            for name, outcome in self.outcomes.items()
            if outcome in ("done", "failed", "skipped")
        )
        return max(0, min(99, int(round(100.0 * earned))))

    @property
    def failed_required(self) -> tuple[str, ...]:
        return tuple(n for n in REQUIRED_STEPS if self.outcomes.get(n) == "failed")

    @property
    def failed_optional(self) -> tuple[str, ...]:
        return tuple(n for n in OPTIONAL_STEPS if self.outcomes.get(n) == "failed")

    def settle(self) -> JobState:
        """Move to the terminal state the step outcomes imply (M6-4).

        This is the whole point of the optional/required split: `failed` when a
        required step is gone, `partial` when only enrichment is, `done` when
        nothing is.
        """
        if self.state in TERMINAL_STATES:
            return self.state
        if self.failed_required:
            self.to("failed")
        elif self.failed_optional:
            self.to("partial")
        else:
            unfinished = [n for n, o in self.outcomes.items() if o not in ("done", "skipped")]
            if unfinished:
                raise RuntimeError(
                    f"cannot settle a job with steps still outstanding: {unfinished}"
                )
            self.to("done")
        return self.state

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "state_meaning": STATE_MEANING[self.state],
            "progress_pct": self.progress_pct,
            "current_step": self.current_step,
            "current_step_label": (
                None if self.current_step is None else STEPS_BY_NAME[self.current_step].label
            ),
            "attempt": self.attempt,
            "is_terminal": self.state in TERMINAL_STATES,
            "steps": [
                {
                    "name": s.name,
                    "label": s.label,
                    "weight": s.weight,
                    "optional": s.optional,
                    "outcome": self.outcomes.get(s.name, "pending"),
                }
                for s in STEPS
            ],
            "warnings": list(self.warnings),
            "error": self.error,
        }
