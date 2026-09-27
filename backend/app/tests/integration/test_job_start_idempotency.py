"""A retried job start gets the job the first attempt started, not a second one.

The page retries a start whose connection stalled; the first attempt may have
arrived and only lost its answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import idempotency
from app.tests.area_fakes import DURG_BBOX

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_work(monkeypatch: pytest.MonkeyPatch) -> Any:
    # The job itself is not under test: only whether one was started.
    monkeypatch.setattr("app.services.job_runner.run_area_job", lambda *a, **k: None)
    monkeypatch.setattr("app.services.job_runner.run_analysis_job", lambda *a, **k: None)
    idempotency.clear()
    yield
    idempotency.clear()


def start_area(client: Any, key: str | None) -> Any:
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/v1/analysis/area", json={"bbox": DURG_BBOX}, headers=headers)


def test_the_same_key_is_the_same_job(client: Any) -> None:
    first = start_area(client, "run-0123456789abcdef")
    second = start_area(client, "run-0123456789abcdef")
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.headers["Location"] == first.json()["status_url"]


def test_without_a_key_each_start_is_a_job(client: Any) -> None:
    assert start_area(client, None).json()["job_id"] != start_area(client, None).json()["job_id"]


def test_a_malformed_key_is_ignored_not_refused(client: Any) -> None:
    first, second = start_area(client, "x"), start_area(client, "x")
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]


def test_a_contour_upload_is_covered_too(client: Any) -> None:
    headers = {"Idempotency-Key": "upload-0123456789abcdef"}
    kml = b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"></kml>'
    ids = [
        client.post(
            "/api/v1/analysis",
            files={"contour_map": ("a.kml", kml, "application/vnd.google-earth.kml+xml")},
            headers=headers,
        ).json()["job_id"]
        for _ in range(2)
    ]
    assert ids[0] == ids[1]


def test_an_old_key_starts_a_new_job(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    first = start_area(client, "late-0123456789abcdef").json()["job_id"]
    monkeypatch.setattr(idempotency, "TTL_S", -1)
    assert start_area(client, "late-0123456789abcdef").json()["job_id"] != first
