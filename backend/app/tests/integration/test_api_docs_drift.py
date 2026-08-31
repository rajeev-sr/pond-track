"""Every route the app serves must be documented (M7-5).

`docs/API.md` is a graded deliverable, and a hand-written reference beside a
growing API rots silently: nine of twenty-four routes had gone undocumented
before this test existed -- everything added in M5 and M6 -- and nothing failed.

The same idea as `test_schema_drift.py`, which compares the models to the
migrated database. Here the live OpenAPI schema is the source of truth and the
document is checked against it, so adding an endpoint without writing it up is a
failing test rather than a thing someone notices months later.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

DOC = Path(__file__).resolve().parents[4] / "docs" / "API.md"

#: The docs live outside the backend package. Running inside the API container
#: only `backend/` is mounted, so they are genuinely absent there -- these tests
#: skip rather than fail, because "the repository is not mounted" is not drift.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DOC.exists(), reason=f"docs not present at {DOC}"),
]

#: Routes deliberately left out of the reference, with the reason. Anything else
#: absent is a failure rather than an omission someone forgot to justify.
UNDOCUMENTED_BY_DESIGN: dict[str, str] = {}


def documented_paths() -> set[str]:
    """Every `/path` mentioned in a heading or a curl example in API.md."""
    text = DOC.read_text(encoding="utf-8")
    # Headings carry the canonical form; curl blocks carry the same paths with a
    # host and query string attached, and either counts as documented.
    return set(re.findall(r"/(?:api/v1/)?[A-Za-z0-9_{}/-]+", text))


def normalise(path: str) -> str:
    """`/api/v1/villages/{village_id}` -> `/villages/{village_id}`."""
    return path[len("/api/v1") :] if path.startswith("/api/v1") else path


#: The docs live outside the backend package. Running inside the API container
#: only `backend/` is mounted, so they are genuinely absent there -- these tests
#: skip rather than fail, because "the repository is not mounted" is not drift.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DOC.exists(), reason=f"docs not present at {DOC}"),
]


class TestTheReferenceCoversTheApi:
    def test_the_document_exists(self) -> None:
        assert DOC.exists(), f"no API reference at {DOC}"

    def test_every_route_is_written_up(self, client: Any) -> None:
        live = {normalise(p) for p in client.get("/openapi.json").json()["paths"]}
        mentioned = documented_paths()

        missing = sorted(
            path
            for path in live
            if path not in UNDOCUMENTED_BY_DESIGN
            and path not in mentioned
            and f"/api/v1{path}" not in mentioned
        )
        assert not missing, (
            "these routes are served but not in docs/API.md:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd a section for each, or list it in UNDOCUMENTED_BY_DESIGN "
            "with a reason."
        )

    def test_the_exclusion_list_is_not_a_dumping_ground(self, client: Any) -> None:
        """An exclusion needs a reason, and a stale one needs removing."""
        live = {normalise(p) for p in client.get("/openapi.json").json()["paths"]}
        for path, reason in UNDOCUMENTED_BY_DESIGN.items():
            assert path in live, f"{path} is excluded but no longer served"
            assert len(reason) > 20, f"{path} needs a real reason, got {reason!r}"

    def test_the_document_does_not_promise_routes_that_do_not_exist(self, client: Any) -> None:
        """The other direction: a reference to a removed endpoint is worse than
        no reference, because a reader will try it."""
        live = {normalise(p) for p in client.get("/openapi.json").json()["paths"]}
        # Only check paths written as an API heading, which is the form that
        # claims "this endpoint exists"; prose mentions are not a promise.
        text = DOC.read_text(encoding="utf-8")
        headed = set(re.findall(r"^#{2,3} `[A-Z]+ (/[A-Za-z0-9_{}/-]+)`", text, re.MULTILINE))
        for path in sorted(headed):
            assert (
                normalise(path) in live
            ), f"docs/API.md documents {path}, which the app does not serve"


class TestTheReadmeEndpointTableIsComplete:
    """The README lists endpoints, so the list has to be all of them.

    It rotted exactly as API.md did: 15 of 27 routes were missing by the time
    this was written — every route added after M4. A README is the first thing a
    reader sees, and a table that silently omits half the API is worse than no
    table, because it reads as complete.
    """

    README = DOC.parent.parent / "README.md"

    def test_the_readme_exists(self) -> None:
        assert self.README.exists()

    def test_every_route_is_listed(self, client: Any) -> None:
        live = set(client.get("/openapi.json").json()["paths"])
        text = self.README.read_text(encoding="utf-8")
        missing = sorted(p for p in live if p not in text)
        assert not missing, (
            "these routes are served but missing from README.md's endpoint table:\n  "
            + "\n  ".join(missing)
        )

    def test_it_maps_requirements_to_endpoints(self) -> None:
        """M7-8 asks for this specifically, and it is what a marker looks for."""
        text = self.README.read_text(encoding="utf-8")
        assert "Requirement → endpoint" in text
        for fr in [f"FR-{n}" for n in range(1, 17)]:
            assert fr in text, f"{fr} is absent from the requirement table"

    def test_the_readme_agrees_with_the_plan_on_what_is_unbuilt(self) -> None:
        """Cross-checked against the plan's burn-down, not a frozen list.

        This test first hardcoded FR-11 to FR-14 as unbuilt, and went stale the
        moment FR-12 shipped. Deriving the set from `IMPLEMENTATION_PLAN.md` §0.2
        makes it enforce the invariant that actually matters: the README and the
        plan must not disagree about what exists.
        """
        plan = (DOC.parent / "IMPLEMENTATION_PLAN.md").read_text(encoding="utf-8")
        readme = self.README.read_text(encoding="utf-8")

        rows = [line for line in plan.splitlines() if re.match(r"\| (?:\*\*)?FR-\d+", line)]
        # The guard is that the *table* is found, not that something is unbuilt.
        # It first asserted a non-empty unbuilt set, which fired the moment the
        # last requirement shipped -- a test failing because the work finished.
        assert rows, "no FR rows found in the plan -- has §0.2 moved?"

        unbuilt = {
            line.split("|")[1].strip().replace("**", "")
            for line in rows
            if line.count("`[ ]`") >= 3
        }

        for fr in sorted(unbuilt):
            line = next((ln for ln in readme.splitlines() if ln.startswith(f"| {fr} ")), None)
            assert line, f"{fr} is unbuilt in the plan but absent from the README table"
            assert (
                "`[ ]`" in line
            ), f"the plan says {fr} is unbuilt but the README claims otherwise: {line}"


class TestTheStarredEndpointsHaveExamples:
    """M7-5 asks for curl examples on the endpoints that carry the assignment."""

    STARRED = (
        "/analyzeContour",
        "/analysis",
        "/land/available",
        "/suitability/weights/ahp",
        "/export/{job_id}",
    )

    def test_each_has_a_curl_example(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        curls = re.findall(r"curl[^\n]*(?:\n\s+[^\n]*)*", text)
        blob = "\n".join(curls)
        missing = [p for p in self.STARRED if p.split("{")[0] not in blob]
        assert not missing, f"no curl example for: {missing}"
