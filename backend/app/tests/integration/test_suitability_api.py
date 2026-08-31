"""HTTP-level tests for the AHP weight endpoints (M6-7, M6-11).

The behaviour worth testing here is the refusal. Any implementation can return
an eigenvector; what the HLD specifies -- and what makes the weights auditable
rather than decorative -- is that a self-contradictory set of judgements comes
back as 400 with the number that condemned it, instead of as weights.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration

AHP = "/api/v1/suitability/weights/ahp"
WEIGHTS = "/api/v1/suitability/weights"

#: a12 = 2, a13 = 4, a23 = 2, and 2 x 2 = 4, so the weights must be (4, 2, 1) / 7.
CONSISTENT: list[list[float]] = [[1, 2, 4], [0.5, 1, 2], [0.25, 0.5, 1]]

#: A dominates B, B dominates C, C dominates A: no weight vector can say this.
CYCLIC: list[list[float]] = [[1, 9, 1 / 9], [1 / 9, 1, 9], [9, 1 / 9, 1]]


def post(client: Any, **body: Any) -> Any:
    return client.post(AHP, json=body)


class TestAConsistentMatrixIsAccepted:
    def test_it_returns_the_weights_the_ratios_imply(self, client: Any) -> None:
        r = post(client, criteria=["a", "b", "c"], matrix=CONSISTENT)
        assert r.status_code == 200, r.text
        weights = r.json()["weights"]
        assert weights["a"] == pytest.approx(4 / 7, abs=1e-4)
        assert weights["b"] == pytest.approx(2 / 7, abs=1e-4)
        assert weights["c"] == pytest.approx(1 / 7, abs=1e-4)

    def test_the_consistency_audit_travels_with_the_answer(self, client: Any) -> None:
        body = post(client, criteria=["a", "b", "c"], matrix=CONSISTENT).json()
        audit = body["consistency"]
        assert audit["consistency_ratio"] == pytest.approx(0.0, abs=1e-6)
        assert audit["is_consistent"] is True
        assert audit["threshold"] == 0.1
        assert audit["random_index"] == 0.58
        assert audit["lambda_max"] == pytest.approx(3.0, abs=1e-4)

    def test_the_weights_sum_to_one(self, client: Any) -> None:
        body = post(client, criteria=["a", "b", "c"], matrix=CONSISTENT).json()
        assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-4)

    def test_the_second_method_is_reported_as_a_cross_check(self, client: Any) -> None:
        body = post(client, criteria=["a", "b", "c"], matrix=CONSISTENT).json()
        assert body["cross_check"]["max_abs_difference"] == pytest.approx(0.0, abs=1e-6)


class TestAnInconsistentMatrixIsRefused:
    def test_a_cycle_is_a_400(self, client: Any) -> None:
        r = post(client, criteria=["a", "b", "c"], matrix=CYCLIC)
        assert r.status_code == 400, r.text

    def test_the_problem_names_the_ratio_and_the_threshold(self, client: Any) -> None:
        """A refusal that does not say how far off it was cannot be acted on."""
        body = post(client, criteria=["a", "b", "c"], matrix=CYCLIC).json()
        assert body["type"] == "/errors/validation"
        assert body["consistency_ratio"] > 0.1
        assert body["threshold"] == 0.1
        assert "trace_id" in body

    def test_it_says_what_to_do_about_it(self, client: Any) -> None:
        body = post(client, criteria=["a", "b", "c"], matrix=CYCLIC).json()
        assert "revise" in body["detail"].lower()

    def test_no_weights_are_returned_alongside_the_refusal(self, client: Any) -> None:
        """Returning weights anyway would dress a contradiction as a result."""
        assert "weights" not in post(client, criteria=["a", "b", "c"], matrix=CYCLIC).json()

    def test_it_can_be_measured_instead_of_enforced(self, client: Any) -> None:
        r = post(client, criteria=["a", "b", "c"], matrix=CYCLIC, strict=False)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["consistency"]["is_consistent"] is False
        assert body["consistency"]["consistency_ratio"] > 1.0


class TestAMalformedMatrixIsRefused:
    @pytest.mark.parametrize(
        ("matrix", "expect"),
        [
            ([[1, 2, 3], [0.5, 1, 2]], "square"),
            ([[2, 2], [0.5, 1]], "diagonal"),
            ([[1, 3], [3, 1]], "reciprocal"),
            ([[1, 20], [0.05, 1]], "Saaty"),
            ([[1, 0], [0, 1]], "positive"),
        ],
    )
    def test_each_failure_says_which_rule_broke(
        self, client: Any, matrix: list[list[float]], expect: str
    ) -> None:
        names = ["a", "b", "c"][: len(matrix)] or ["a", "b"]
        r = client.post(AHP, json={"criteria": names, "matrix": matrix})
        assert r.status_code == 400, r.text
        assert expect.lower() in r.json()["detail"].lower(), r.json()["detail"]

    def test_a_size_mismatch_with_the_names_is_refused(self, client: Any) -> None:
        r = post(client, criteria=["a", "b"], matrix=CONSISTENT)
        assert r.status_code == 400
        assert "criteria were named" in r.json()["detail"]

    def test_one_criterion_is_rejected_by_the_schema(self, client: Any) -> None:
        """Pydantic's min_length catches this before the service does.

        Still a 400: the app normalises FastAPI's own 422 into problem details
        with a per-field `errors[]`, which is what the HLD's status table
        specifies for a validation failure.
        """
        r = client.post(AHP, json={"criteria": ["a"], "matrix": [[1]]})
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["type"] == "/errors/validation"
        assert body["errors"][0]["field"] == "criteria"


class TestTheShippedWeightsAreExposedForAudit:
    def test_the_vector_sums_to_one(self, client: Any) -> None:
        body = client.get(WEIGHTS).json()
        assert body["sums_to"] == pytest.approx(1.0)
        assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_the_shipped_judgements_are_consistent(self, client: Any) -> None:
        """★ Nine hardcoded numbers, shown to be coherent rather than asserted."""
        audit = client.get(WEIGHTS).json()["audit"]["consistency"]
        assert audit["is_consistent"] is True
        assert audit["consistency_ratio"] < 0.1

    def test_the_reconstructed_matrix_is_returned_for_inspection(self, client: Any) -> None:
        body = client.get(WEIGHTS).json()
        matrix = body["reconstruction"]["matrix"]
        n = len(body["weights"])
        assert len(matrix) == n and all(len(row) == n for row in matrix)
        assert all(row[i] == 1.0 for i, row in enumerate(matrix)), "diagonal must be 1"

    def test_every_tier_renormalises_to_one(self, client: Any) -> None:
        for tier, block in client.get(WEIGHTS).json()["per_tier"].items():
            assert sum(block["weights"].values()) == pytest.approx(1.0, abs=1e-6), tier
            assert set(block["weights"]) == set(block["criteria"]), tier

    def test_a_narrower_tier_carries_fewer_criteria(self, client: Any) -> None:
        tiers = client.get(WEIGHTS).json()["per_tier"]
        assert len(tiers["terrain_only"]["criteria"]) < len(tiers["no_soil_lulc"]["criteria"])
        assert len(tiers["no_soil_lulc"]["criteria"]) < len(tiers["full"]["criteria"])

    def test_the_route_is_documented(self, client: Any) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert AHP in paths and WEIGHTS in paths
        assert "400" in paths[AHP]["post"]["responses"]


ANALYZE = "/api/v1/suitability/analyze"

#: Every criterion the model knows, so the vector is complete.
FULL_VECTOR = {
    "flow_accumulation": 0.05,
    "slope": 0.30,
    "depression_depth": 0.25,
    "soil_runoff_potential": 0.10,
    "land_availability": 0.10,
    "distance_to_stream": 0.05,
    "plan_concavity": 0.05,
    "distance_to_settlement": 0.05,
    "distance_to_waterbody": 0.05,
}


def start(client: Any, weights_json: str | None = None) -> Any:
    import io as _io

    from app.tests.synthetic_kml import build_kml, concentric_rings

    files = {
        "file": (
            "rings.kml",
            _io.BytesIO(build_kml(concentric_rings())),
            "application/vnd.google-earth.kml+xml",
        )
    }
    form: dict[str, Any] = {"enrich": "false"}
    if weights_json is not None:
        form["weights_json"] = weights_json
    return client.post(ANALYZE, files=files, data=form)


class TestRunningWithYourOwnWeights:
    def test_it_accepts_a_complete_vector(self, client: Any) -> None:
        response = start(client, json.dumps({"weights": FULL_VECTOR}))
        assert response.status_code == 202, response.text
        assert response.json()["weights_applied"] != "shipped defaults"

    def test_omitting_weights_uses_the_shipped_defaults(self, client: Any) -> None:
        assert start(client).json()["weights_applied"] == "shipped defaults"

    def test_it_offers_the_sites_url(self, client: Any) -> None:
        body = start(client).json()
        assert body["sites_url"] == f"/api/v1/suitability/{body['job_id']}/sites"

    def test_a_matrix_is_derived_rather_than_requiring_a_second_call(self, client: Any) -> None:
        """Pairwise judgements in, weights out, in one request."""
        names = list(FULL_VECTOR)
        matrix = [
            [1.0 if i == j else FULL_VECTOR[a] / FULL_VECTOR[b] for j, b in enumerate(names)]
            for i, a in enumerate(names)
        ]
        response = start(client, json.dumps({"criteria": names, "matrix": matrix}))
        assert response.status_code == 202, response.text
        assert isinstance(response.json()["weights_applied"], dict)


class TestBadWeightsAreRefusedBeforeAnyWork:
    """A 400 in milliseconds beats one after a 24-second analysis."""

    def test_an_inconsistent_matrix_is_refused(self, client: Any) -> None:
        response = start(client, json.dumps({"criteria": ["a", "b", "c"], "matrix": CYCLIC}))
        assert response.status_code == 400
        assert response.json()["consistency_ratio"] > 0.1

    def test_an_incomplete_vector_names_what_is_missing(self, client: Any) -> None:
        response = start(client, json.dumps({"weights": {"slope": 1.0}}))
        assert response.status_code == 400
        assert "flow_accumulation" in response.json()["detail"]

    def test_an_unknown_criterion_is_refused(self, client: Any) -> None:
        response = start(client, json.dumps({"weights": {**FULL_VECTOR, "vibes": 1.0}}))
        assert response.status_code == 400

    def test_a_negative_weight_is_refused(self, client: Any) -> None:
        response = start(client, json.dumps({"weights": {**FULL_VECTOR, "slope": -1.0}}))
        assert response.status_code == 400
        assert "negative" in response.json()["detail"]

    def test_weights_summing_to_zero_are_refused(self, client: Any) -> None:
        response = start(client, json.dumps({"weights": dict.fromkeys(FULL_VECTOR, 0.0)}))
        assert response.status_code == 400
        assert "zero" in response.json()["detail"]

    def test_malformed_json_is_refused(self, client: Any) -> None:
        response = start(client, "{not json")
        assert response.status_code == 400
        assert "weights_json" in str(response.json()["errors"])

    def test_a_matrix_without_criteria_is_refused(self, client: Any) -> None:
        response = start(client, json.dumps({"matrix": CONSISTENT}))
        assert response.status_code == 400
        assert "criteria" in response.json()["detail"]

    def test_a_matrix_that_leaves_criteria_unweighted_is_refused(self, client: Any) -> None:
        """Consistent, but only compares three of the nine criteria."""
        response = start(client, json.dumps({"criteria": ["a", "b", "c"], "matrix": CONSISTENT}))
        assert response.status_code == 400
        assert "unweighted" in response.json()["detail"]


class TestTheRankedSitesProjection:
    def settled(self, client: Any) -> str:
        job_id = start(client).json()["job_id"]
        for _ in range(60):
            if client.get(f"/api/v1/analysis/{job_id}/status").json()["is_terminal"]:
                return job_id
        raise AssertionError("job never settled")

    def test_it_returns_the_ranking(self, client: Any) -> None:
        body = client.get(f"/api/v1/suitability/{self.settled(client)}/sites").json()
        assert body["site_count"] >= 1
        assert [s["rank"] for s in body["sites"]] == sorted(s["rank"] for s in body["sites"])

    def test_every_site_carries_its_per_criterion_breakdown(self, client: Any) -> None:
        """A score of 72 with no breakdown is unauditable (FR-9)."""
        body = client.get(f"/api/v1/suitability/{self.settled(client)}/sites").json()
        for site in body["sites"]:
            breakdown = site["criteria_breakdown"]
            assert breakdown, f"site #{site['rank']} has no breakdown"
            for entry in breakdown:
                for key in ("criterion", "weight", "contribution", "normalised"):
                    assert key in entry, key

    def test_the_contributions_reconstruct_the_score(self, client: Any) -> None:
        body = client.get(f"/api/v1/suitability/{self.settled(client)}/sites").json()
        site = body["sites"][0]
        total = sum(c["contribution"] for c in site["criteria_breakdown"])
        assert total * 100 == pytest.approx(site["suitability_score"], abs=1.0)

    def test_the_tier_travels_with_the_ranking(self, client: Any) -> None:
        """Scores are not comparable across tiers, so the tier cannot be implicit."""
        body = client.get(f"/api/v1/suitability/{self.settled(client)}/sites").json()
        assert body["analysis_tier"] in ("full", "no_soil_lulc", "terrain_only")

    def test_the_weights_used_are_reported(self, client: Any) -> None:
        body = client.get(f"/api/v1/suitability/{self.settled(client)}/sites").json()
        assert body["criteria_weights"]
        assert sum(body["criteria_weights"].values()) == pytest.approx(1.0, abs=1e-3)

    def test_it_is_narrower_than_the_full_result(self, client: Any) -> None:
        """The point of the projection: a ranking, not the contour geometry too."""
        job_id = self.settled(client)
        sites = client.get(f"/api/v1/suitability/{job_id}/sites").json()
        assert "contour_map" not in sites
        assert "candidate_sites" not in sites

    def test_an_unfinished_job_says_so_rather_than_returning_nothing(self, client: Any) -> None:
        from app.services.job_store import JobRecord, get_store
        from app.services.jobs import JobProgress

        get_store().put(JobRecord(job_id="pending1", progress=JobProgress().as_dict()))
        response = client.get("/api/v1/suitability/pending1/sites")
        assert response.status_code == 422
        assert "queued" in response.json()["detail"]

    def test_an_unknown_job_is_a_404(self, client: Any) -> None:
        assert client.get("/api/v1/suitability/deadbeef/sites").status_code == 404
