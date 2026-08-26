"""RFC 7807 problem-details shape and handler behaviour (HLD 5.1)."""

from __future__ import annotations

import pytest

from app.core.errors import (
    AoiTooLargeProblem,
    NotConfiguredProblem,
    NotFoundProblem,
    ProblemError,
    ProviderUnavailableProblem,
    RateLimitedProblem,
    UnanswerableProblem,
    ValidationProblem,
)

ALL = [
    (ValidationProblem, 400),
    (NotFoundProblem, 404),
    (AoiTooLargeProblem, 413),
    (UnanswerableProblem, 422),
    (RateLimitedProblem, 429),
    (ProviderUnavailableProblem, 503),
    (NotConfiguredProblem, 503),
]


class TestProblemShape:
    @pytest.mark.parametrize(("cls", "status"), ALL)
    def test_status_codes(self, cls: type[ProblemError], status: int) -> None:
        assert cls("x").status == status

    @pytest.mark.parametrize(("cls", "_status"), ALL)
    def test_required_rfc7807_members_present(self, cls: type[ProblemError], _status: int) -> None:
        body = cls("something went wrong").to_problem("/api/v1/thing", "abc123")
        for field in ("type", "title", "status", "detail", "instance", "trace_id"):
            assert field in body, f"{cls.__name__} problem body is missing {field!r}"

    @pytest.mark.parametrize(("cls", "_status"), ALL)
    def test_type_is_a_stable_uri_reference(self, cls: type[ProblemError], _status: int) -> None:
        assert cls("x").type.startswith("/errors/")

    def test_detail_and_instance_round_trip(self) -> None:
        body = NotFoundProblem("no such village").to_problem("/api/v1/villages/9", "t1")
        assert body["detail"] == "no such village"
        assert body["instance"] == "/api/v1/villages/9"
        assert body["trace_id"] == "t1"

    def test_extra_context_is_merged(self) -> None:
        body = AoiTooLargeProblem("too big", limit_km2=100, requested_km2=850).to_problem("/x", "t")
        assert body["limit_km2"] == 100
        assert body["requested_km2"] == 850

    def test_422_is_distinct_from_400(self) -> None:
        # HLD 5.1: 400 = malformed, 422 = well-formed but unanswerable. Keeping
        # them apart is what lets the UI give a useful message.
        assert ValidationProblem("x").status != UnanswerableProblem("y").status

    def test_is_an_exception(self) -> None:
        with pytest.raises(ProblemError):
            raise UnanswerableProblem("this point receives negligible runoff")
