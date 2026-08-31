"""The app is four pages now, so routing is a thing that can break.

Two failure modes matter and neither shows up in a unit test:

* A **deep link** must work. `/method` typed into the address bar is served
  `index.html` by nginx's SPA fallback and resolved client-side; if either half is
  missing the reader gets a 404 or a blank page.
* The API's own pages must **not** be captured by the SPA. `/docs`,
  `/redoc` and `/openapi.json` are proxied past it. A missing proxy rule does not
  404 — it returns `index.html` with HTTP 200, which is how Swagger UI came to
  report "the provided definition does not specify a valid version field".

There is also state to protect: an analysis is held above the router, so
navigating away to check a formula and back must not discard a run that took
minutes.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from app.tests.e2e.cdp import Chrome

MOUNT_TIMEOUT_S = 60.0
ANALYSIS_TIMEOUT_S = 300.0

#: One marker per page that only that page carries. Deliberately not a `.stamp`:
#: those are uppercased in CSS and `innerText` returns the transformed text, so
#: "Job" never matches the "JOB" on screen.
PAGES = [
    ("/", "brief", "Where should this"),
    ("/workspace", "workspace", "Contour survey"),
    ("/method", "method", "How the proposal is arrived at"),
    ("/reference", "reference", "Principal routes"),
]


def _mounted(page: Chrome) -> bool:
    return bool(
        page.wait_until(
            "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
        )
    )


class TestEveryPageIsDeepLinkable:
    """Typed into the address bar, not reached by clicking."""

    @pytest.mark.parametrize("path,name,marker", PAGES)
    def test_the_page_renders_from_a_cold_load(
        self, chrome_binary, frontend_url, path, name, marker
    ) -> None:
        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}{path}")
            assert _mounted(page), f"{path} never mounted"
            assert marker in page.text, f"{path} rendered but {marker!r} is not on it"

    @pytest.mark.parametrize("path,name,marker", PAGES)
    def test_the_server_returns_the_app_not_a_404(self, frontend_url, path, name, marker) -> None:
        """The other half of a deep link: the SPA fallback."""
        with urllib.request.urlopen(f"{frontend_url}{path}", timeout=20) as response:
            assert response.status == 200, f"{path} answered HTTP {response.status}"
            body = response.read().decode("utf-8", "replace")
        assert '<div id="root">' in body, f"{path} did not serve the app shell"


class TestTheApiPagesAreNotCapturedBySpa:
    """A missing proxy rule returns index.html with HTTP 200, not a 404."""

    @pytest.mark.parametrize(
        "path,content_type",
        [("/docs", "text/html"), ("/redoc", "text/html"), ("/openapi.json", "application/json")],
    )
    def test_it_is_served_by_the_api(self, frontend_url, path, content_type) -> None:
        with urllib.request.urlopen(f"{frontend_url}{path}", timeout=20) as response:
            assert response.status == 200
            assert response.headers.get("content-type", "").startswith(content_type)
            body = response.read().decode("utf-8", "replace")
        assert (
            '<div id="root">' not in body
        ), f"{path} was answered by the SPA, not the API — the proxy rule is missing"

    def test_the_spec_is_parseable_json_with_a_version(self, frontend_url) -> None:
        """Exactly what Swagger UI complains about when this breaks."""
        import json

        with urllib.request.urlopen(f"{frontend_url}/openapi.json", timeout=20) as response:
            spec = json.load(response)
        assert str(spec.get("openapi", "")).startswith("3."), "no valid version field"
        assert spec["paths"], "the spec carries no paths"

    def test_the_masthead_links_out_rather_than_routing(self, chrome_binary, frontend_url) -> None:
        """It must be a real navigation. Routed, it would 404 inside the SPA."""
        with Chrome(chrome_binary) as page:
            page.navigate(frontend_url)
            assert _mounted(page)
            href = page.evaluate(
                "(() => { const a = [...document.querySelectorAll('.mh-act a')]"
                ".find(x => /api docs/i.test(x.textContent));"
                " return a ? a.getAttribute('href') : null; })()"
            )
            assert href == "/docs", f"the API docs link points at {href!r}"


class TestNavigationKeepsTheRun:
    """An analysis is held above the router on purpose.

    Held in the workspace component it would be discarded the moment someone
    opened the method page to check a formula — throwing away a run that can take
    minutes, with no warning that it had happened.
    """

    @pytest.fixture(scope="class")
    def analysed_then_navigated(self, chrome_binary, frontend_url, sample_contour_map):
        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}/workspace")
            assert _mounted(page)
            assert page.wait_until("!!document.querySelector('input[type=file]')", timeout=30)
            page.attach_file("input[type=file]", str(sample_contour_map))
            assert page.wait_until("!!document.body.innerText.match(/contours_1m/)", timeout=15)
            assert page.click_button_matching("^run$")
            assert page.wait_until(
                "/[\\d,]+\\s*m\\u00b3/.test(document.body.innerText)", timeout=ANALYSIS_TIMEOUT_S
            ), "the analysis never completed"
            page.wait_until("false", timeout=3, poll=1.0)
            yield page

    def test_the_brief_reports_the_run_rather_than_a_typed_in_figure(
        self, analysed_then_navigated
    ) -> None:
        """The brief's result block is read from the analysis.

        This is not only tidiness: the assignment forbids results specific to the
        sample sheet being written into the implementation, so the block has to be
        empty until something is analysed and populated from that run afterwards.
        """
        page = analysed_then_navigated
        assert page.evaluate(
            "(() => { const a = [...document.querySelectorAll('.mh-nav a')]"
            ".find(x => /brief/i.test(x.textContent));"
            " if (!a) return false; a.click(); return true; })()"
        ), "no Brief link in the masthead"
        assert page.wait_until(
            "/suitability/i.test(document.body.innerText)", timeout=20
        ), "the brief does not report the completed run"
        text = page.text
        assert (
            "Nothing has been analysed yet" not in text
        ), "the run was discarded by navigating to the brief"
        assert "/100" in text, "no suitability score on the brief"

    def test_returning_to_the_workspace_still_has_the_proposal(
        self, analysed_then_navigated
    ) -> None:
        page = analysed_then_navigated
        assert page.evaluate(
            "(() => { const a = [...document.querySelectorAll('.mh-nav a')]"
            ".find(x => /workspace/i.test(x.textContent));"
            " if (!a) return false; a.click(); return true; })()"
        )
        assert page.wait_until(
            "/SITE\\s*\\d/i.test(document.body.innerText)", timeout=20
        ), "the proposal was lost on the way back to the workspace"

    def test_nothing_threw_while_navigating(self, analysed_then_navigated) -> None:
        page = analysed_then_navigated
        errors = [e for e in page.console_errors() if "favicon" not in e]
        assert not errors, f"console errors while routing: {errors[:3]}"
