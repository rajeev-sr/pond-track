"""HTTP-level tests for the async analysis job endpoints (M6-1, M6-3, M6-5).

The synchronous `POST /analyzeContour` is covered elsewhere and must keep
working; these cover the job shape HLD 5.1 specifies for a long operation --
`202` with a poll URL, a status document that says when to stop polling, and a
result that is served for `partial` as well as `done`.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.tests.synthetic_kml import build_kml, concentric_rings

pytestmark = pytest.mark.integration

START = "/api/v1/analysis"


def upload(client: Any, **form: Any) -> Any:
    data = build_kml(concentric_rings())
    files = {"file": ("rings.kml", io.BytesIO(data), "application/vnd.google-earth.kml+xml")}
    return client.post(START, files=files, data={"enrich": "false", **form})


def run_to_completion(client: Any, **form: Any) -> tuple[str, dict[str, Any]]:
    """Start a job and poll until it is terminal.

    The in-process executor runs the job in a FastAPI background task, which
    TestClient completes before returning from the request, so a single poll
    already finds it settled.
    """
    started = upload(client, **form)
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    for _ in range(60):
        status = client.get(f"{START}/{job_id}/status").json()
        if status["is_terminal"]:
            return job_id, status
    raise AssertionError(f"job {job_id} never settled: {status}")


class TestStartingAJob:
    def test_it_answers_202_not_200(self, client: Any) -> None:
        """HLD 5.1: a long operation is accepted, not completed, in the response."""
        assert upload(client).status_code == 202

    def test_it_returns_a_job_id_and_where_to_poll(self, client: Any) -> None:
        body = upload(client).json()
        assert body["job_id"]
        assert body["status_url"] == f"{START}/{body['job_id']}/status"
        assert body["result_url"] == f"{START}/{body['job_id']}/result"

    def test_the_location_header_points_at_the_status_url(self, client: Any) -> None:
        response = upload(client)
        assert response.headers["location"] == response.json()["status_url"]

    def test_it_says_which_executor_took_the_work(self, client: Any) -> None:
        """A queue nothing is draining is the one failure an async API must avoid.

        With no Celery worker up the request runs in this process instead, and
        says so rather than pretending it was queued.
        """
        assert upload(client).json()["executor"] in ("celery", "in_process")

    def test_the_first_poll_finds_the_job(self, client: Any) -> None:
        """A browser polls immediately; a 404 there would look like a lost job."""
        job_id = upload(client).json()["job_id"]
        assert client.get(f"{START}/{job_id}/status").status_code == 200

    def test_unusable_input_is_still_rejected_up_front(self, client: Any) -> None:
        files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
        assert client.post(START, files=files).status_code in (400, 413, 415, 422)


class TestPollingTheStatus:
    def test_it_settles_and_says_so(self, client: Any) -> None:
        _, status = run_to_completion(client)
        assert status["state"] in ("done", "partial")
        assert status["is_terminal"] is True
        assert status["progress_pct"] == 100

    def test_it_lists_every_step_with_an_outcome(self, client: Any) -> None:
        _, status = run_to_completion(client)
        outcomes = {s["name"]: s["outcome"] for s in status["steps"]}
        assert set(outcomes) == {
            "parse",
            "interpolate",
            "condition",
            "flow_routing",
            "enrichment",
            "siting",
            "catchments",
        }
        assert all(o in ("done", "skipped") for o in outcomes.values()), outcomes

    def test_the_steps_carry_their_weights_so_a_client_can_draw_them(self, client: Any) -> None:
        _, status = run_to_completion(client)
        assert sum(s["weight"] for s in status["steps"]) == pytest.approx(1.0, abs=1e-6)

    def test_it_explains_the_state_rather_than_only_naming_it(self, client: Any) -> None:
        _, status = run_to_completion(client)
        assert status["state_meaning"]

    def test_it_offers_the_result_url_only_once_there_is_one(self, client: Any) -> None:
        job_id, status = run_to_completion(client)
        assert status["result_url"] == f"{START}/{job_id}/result"

    def test_an_unknown_job_is_a_404_that_explains_itself(self, client: Any) -> None:
        response = client.get(f"{START}/deadbeef/status")
        assert response.status_code == 404
        body = response.json()
        assert "deadbeef" in body["detail"]
        assert body["job_id"] == "deadbeef"


class TestFetchingTheResult:
    def test_it_serves_the_same_document_the_sync_endpoint_returns(self, client: Any) -> None:
        job_id, _ = run_to_completion(client)
        body = client.get(f"{START}/{job_id}/result").json()
        assert body["state"] in ("done", "partial")
        for key in ("contour_map", "candidate_sites", "environment", "recommended_site"):
            assert key in body["result"], key

    def test_it_carries_the_warnings_alongside_the_result(self, client: Any) -> None:
        job_id, _ = run_to_completion(client)
        assert isinstance(client.get(f"{START}/{job_id}/result").json()["warnings"], list)

    def test_an_unknown_job_is_a_404(self, client: Any) -> None:
        assert client.get(f"{START}/deadbeef/result").status_code == 404


class TestAbandoningAJob:
    def test_delete_answers_204(self, client: Any) -> None:
        job_id, _ = run_to_completion(client)
        assert client.delete(f"{START}/{job_id}").status_code == 204

    def test_the_record_is_gone_afterwards(self, client: Any) -> None:
        job_id, _ = run_to_completion(client)
        client.delete(f"{START}/{job_id}")
        assert client.get(f"{START}/{job_id}/status").status_code == 404

    def test_deleting_an_unknown_job_is_a_404(self, client: Any) -> None:
        assert client.delete(f"{START}/deadbeef").status_code == 404


class TestTheSyncEndpointIsUntouched:
    def test_analyze_contour_still_answers_200_with_the_whole_document(self, client: Any) -> None:
        """Replacing it with a job id would make the simple case worse."""
        data = build_kml(concentric_rings())
        files = {"file": ("rings.kml", io.BytesIO(data), "application/vnd.google-earth.kml+xml")}
        response = client.post("/api/v1/analyzeContour", files=files, data={"enrich": "false"})
        assert response.status_code == 200
        assert "candidate_sites" in response.json()


class TestTheRoutesAreDocumented:
    def test_the_job_endpoints_are_in_the_schema(self, client: Any) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert START in paths
        assert "202" in paths[START]["post"]["responses"]
        assert f"{START}/{{job_id}}/status" in paths
        assert f"{START}/{{job_id}}/result" in paths
