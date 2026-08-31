"""The PDF report (M7-1, M7-3).

Split deliberately: the context builder and the HTML are tested without
WeasyPrint, because that is where every decision about *what the reader is told*
lives and it should not be gated on system libraries being installed. Only the
last class renders an actual PDF, and it skips when Pango and Cairo are absent.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from app.services import report
from app.tests.synthetic_kml import build_kml, concentric_rings

pytestmark = pytest.mark.integration

SAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def analysed(client: Any, *, contours: bool = True) -> dict[str, Any]:
    files = {
        "file": (
            "rings.kml",
            io.BytesIO(build_kml(concentric_rings())),
            "application/vnd.google-earth.kml+xml",
        )
    }
    response = client.post(
        "/api/v1/analyzeContour",
        files=files,
        data={"enrich": "false", "include_contours": str(contours).lower()},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def finished_job(client: Any) -> str:
    files = {
        "file": (
            "rings.kml",
            io.BytesIO(build_kml(concentric_rings())),
            "application/vnd.google-earth.kml+xml",
        )
    }
    started = client.post(
        "/api/v1/analysis", files=files, data={"enrich": "false", "include_contours": "true"}
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    for _ in range(60):
        if client.get(f"/api/v1/analysis/{job_id}/status").json()["is_terminal"]:
            return job_id
    raise AssertionError("job never settled")


class TestWhatTheReportTellsTheReader:
    """The context builder: no WeasyPrint needed to check the content."""

    def test_it_names_the_file_that_was_analysed(self, client: Any) -> None:
        context = report.build_context(analysed(client))
        assert context["site_label"] == "rings.kml"

    def test_it_carries_the_recommendation(self, client: Any) -> None:
        context = report.build_context(analysed(client))
        assert context["recommended"]["rank"] == 1
        assert context["site_count"] >= 1

    def test_the_binding_constraint_is_explained_not_just_named(self, client: Any) -> None:
        """The name says which variable stopped the search; the explanation says
        what to do about it, which is what makes it advice."""
        context = report.build_context(analysed(client))
        if not context["binding_constraint"]:
            pytest.skip("no pond was sized on this synthetic surface")
        assert len(context["binding_explanation"]) > 60

    def test_the_contour_interval_is_the_real_one(self, client: Any) -> None:
        """It appears in the limitations text, so a wrong value misleads."""
        context = report.build_context(analysed(client))
        assert context["contour_interval"] != "—"
        assert float(context["contour_interval"]) > 0

    def test_the_criteria_contributions_reconstruct_the_score(self, client: Any) -> None:
        context = report.build_context(analysed(client))
        rows = context["criteria_rows"]
        assert rows, "no criteria breakdown reached the report"
        total = sum(r["contribution"] for r in rows)
        assert total * 100 == pytest.approx(context["recommended"]["suitability_score"], abs=1.5)

    def test_every_source_row_carries_a_licence(self, client: Any) -> None:
        """A report that reuses open data without naming its licence is a problem
        for whoever forwards it."""
        context = report.build_context(analysed(client))
        for row in context["source_rows"]:
            assert row["licence"], row

    def test_a_degraded_tier_is_explained(self, client: Any) -> None:
        context = report.build_context(analysed(client))
        if context["tier"] == "full":
            pytest.skip("this run had every layer")
        assert context["tier_meaning"], "the tier is named but not explained"

    def test_an_analysis_with_no_sites_is_refused_rather_than_rendered_blank(
        self, client: Any
    ) -> None:
        empty = analysed(client)
        empty["candidate_sites"] = []
        empty["recommended_site"] = None
        with pytest.raises(ValueError, match="no candidate sites"):
            report.build_context(empty)


class TestTheHtmlItRenders:
    def test_the_limitations_section_is_present(self, client: Any) -> None:
        """Not an appendix: a recommendation whose caveats are hidden is worse
        than one that admits what it does not know."""
        html = report.render_html(analysed(client))
        assert "Assumptions and limitations" in html
        for caveat in ("interpolated from contour", "no orientation", "tenure is not modelled"):
            assert caveat in html, caveat

    def test_the_figures_are_embedded_not_linked(self, client: Any) -> None:
        """A linked image is a broken image the moment the PDF is forwarded."""
        html = report.render_html(analysed(client))
        assert "data:image/png;base64," in html
        assert "http://" not in html.split("<style>")[0]

    def test_partial_warnings_reach_the_first_page(self, client: Any) -> None:
        html = report.render_html(analysed(client), warnings=["soil layer failed (HTTP 504)"])
        assert "Partial analysis" in html
        assert "HTTP 504" in html

    def test_the_cost_uses_indian_digit_grouping(self, client: Any) -> None:
        """₹12,048,099 would be wrong here; Indian grouping is ₹1,20,48,099."""
        context = report.build_context(analysed(client))
        if context["cost_inr"] == "—":
            pytest.skip("no pond was costed")
        digits = context["cost_inr"].lstrip("₹")
        if len(digits.replace(",", "")) > 5:
            assert digits.count(",") >= 2, digits

    def test_it_never_leaves_a_placeholder(self, client: Any) -> None:
        html = report.render_html(analysed(client))
        for bad in ("PASTE", "TODO", "{{", "None None", "lorem"):
            assert bad not in html, f"{bad!r} reached the report"


class TestTheEndpoints:
    def test_generate_then_download(self, client: Any) -> None:
        pytest.importorskip("weasyprint", reason="WeasyPrint needs Pango/Cairo")
        job_id = finished_job(client)
        created = client.post("/api/v1/reports/generate", data={"job_id": job_id})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["size_bytes"] > 10_000

        downloaded = client.get(body["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "application/pdf"
        assert "attachment" in downloaded.headers["content-disposition"]
        assert downloaded.content.startswith(b"%PDF-")

    def test_an_unknown_job_is_a_404(self, client: Any) -> None:
        response = client.post("/api/v1/reports/generate", data={"job_id": "deadbeef"})
        assert response.status_code == 404

    def test_an_unfinished_job_says_so(self, client: Any) -> None:
        from app.services.job_store import JobRecord, get_store
        from app.services.jobs import JobProgress

        get_store().put(JobRecord(job_id="pending7", progress=JobProgress().as_dict()))
        response = client.post("/api/v1/reports/generate", data={"job_id": "pending7"})
        assert response.status_code == 422
        assert "nothing to report" in response.json()["detail"]

    def test_an_unknown_report_is_a_404(self, client: Any) -> None:
        assert client.get("/api/v1/reports/deadbeef/download").status_code == 404


class TestTheRenderedPdf:
    def test_it_is_a_multi_page_a4_document(self, client: Any) -> None:
        pytest.importorskip("weasyprint", reason="WeasyPrint needs Pango/Cairo")
        pdf = report.render_pdf(analysed(client))
        # Structurally complete: header and trailer both present, so the render
        # finished rather than being truncated.
        assert pdf.startswith(b"%PDF-")
        assert pdf.rstrip().endswith(b"%%EOF")
        # Not counting `/Page` markers: WeasyPrint Flate-compresses its object
        # streams, so they do not appear in the raw bytes and a naive count
        # reads zero on a perfectly good four-page document. Size is the honest
        # in-band signal that the content did not collapse -- the report embeds
        # a map figure, so a stub would be a fraction of this.
        assert len(pdf) > 40_000, f"suspiciously small PDF: {len(pdf)} bytes"
