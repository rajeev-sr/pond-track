"""Select an area on the map, in a real browser (final submission).

The brief's final requirements, exercised the way a grader would: switch to the
area mode, drag a rectangle on the map, run it, and read the pond location, the
catchment area and the water collected off the map itself. The drag is real
input through the DevTools protocol, not a call into the app's state, so these
also prove the map hands drags to the draw tool and back.

Runs with enrichment off: this checks the area path and the overlays, not the
soil and rainfall providers, which the upload tests already cover.
"""

from __future__ import annotations

import json

import pytest

from app.tests.e2e.cdp import Chrome
from app.tests.e2e.urls import urlopen

MOUNT_TIMEOUT_S = 60.0
ANALYSIS_TIMEOUT_S = 180.0

#: What "Use the sample area" draws: the sample sheet's own extent.
SAMPLE_BBOX = (81.2814, 21.2398, 81.3126, 21.2636)


def _settle(page: Chrome, seconds: float = 2.0) -> None:
    page.wait_until("false", timeout=seconds, poll=0.5)


def _map_box(page: Chrome) -> dict[str, float]:
    box = page.evaluate(
        "(() => { const r = document.querySelector('.drawing .map').getBoundingClientRect();"
        " return {x: r.x, y: r.y, w: r.width, h: r.height}; })()"
    )
    assert isinstance(box, dict), "no map on the page"
    return box


def _drag(page: Chrome, start: tuple[float, float], end: tuple[float, float]) -> None:
    """Press, move in steps, release — as a hand would, so MapLibre sees a drag."""
    x0, y0 = start
    x1, y1 = end
    page.send("Input.dispatchMouseEvent", type="mouseMoved", x=x0, y=y0)
    page.send(
        "Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0, button="left", clickCount=1
    )
    steps = 8
    for i in range(1, steps + 1):
        page.send(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=x0 + (x1 - x0) * i / steps,
            y=y0 + (y1 - y0) * i / steps,
            button="left",
            buttons=1,
        )
    page.send(
        "Input.dispatchMouseEvent", type="mouseReleased", x=x1, y=y1, button="left", clickCount=1
    )


def _click_at(page: Chrome, x: float, y: float) -> None:
    for kind in ("mousePressed", "mouseReleased"):
        page.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)


def _press_escape(page: Chrome) -> None:
    for kind in ("keyDown", "keyUp"):
        page.send(
            "Input.dispatchKeyEvent",
            type=kind,
            key="Escape",
            code="Escape",
            windowsVirtualKeyCode=27,
        )


def _readout(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.area-readout')?.innerText ?? ''"))


def _run_enabled(page: Chrome) -> bool:
    return bool(
        page.evaluate(
            "(() => { const b = [...document.querySelectorAll('.jobsheet button')]"
            ".find(x => x.textContent.trim().toLowerCase() === 'run');"
            " return !!b && !b.disabled; })()"
        )
    )


@pytest.fixture(scope="module")
def page(chrome_binary, frontend_url):
    with Chrome(chrome_binary) as chrome:
        chrome.navigate(f"{frontend_url}/workspace")
        assert chrome.wait_until(
            "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
        ), "React never mounted"
        # Loaded, not merely mounted: the canvas exists well before the map
        # takes drags, and a drag before `load` goes to nobody.
        assert chrome.wait_until(
            "!!document.querySelector('.drawing .map[data-ready=true]')", timeout=MOUNT_TIMEOUT_S
        ), "the map never loaded"
        yield chrome


@pytest.fixture(scope="module")
def frontend(frontend_url: str) -> str:
    return frontend_url


class TestSelectingAnArea:
    """In file order: each step leaves the page where the next one starts."""

    def test_the_area_mode_is_one_click_away(self, page: Chrome) -> None:
        assert page.click_button_matching("^draw area on map$"), "no area-mode switch"
        assert page.wait_until("!!document.querySelector('.area-readout')", timeout=5)
        assert "No area yet" in _readout(page)
        assert not _run_enabled(page), "Run must wait for an area"

    def test_a_rectangle_far_too_large_is_refused_before_any_request(self, page: Chrome) -> None:
        """At the opening zoom a small drag spans hundreds of kilometres."""
        assert page.click_button_matching("^draw a rectangle$")
        assert page.wait_until("document.body.innerText.includes('Press and drag')", timeout=5)
        box = _map_box(page)
        cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        _drag(page, (cx - 60, cy - 40), (cx + 60, cy + 40))
        assert page.wait_until("/over the 100 km/i.test(document.body.innerText)", timeout=5)
        assert not _run_enabled(page), "Run stayed enabled over the cap"

    def test_escape_cancels_drawing(self, page: Chrome) -> None:
        assert page.click_button_matching("^redraw the rectangle$")
        assert page.wait_until("document.body.innerText.includes('Press and drag')", timeout=5)
        _press_escape(page)
        assert page.wait_until("!document.body.innerText.includes('Press and drag')", timeout=5)
        # What was there before is kept.
        assert "over the 100" in _readout(page).lower()

    def test_the_sample_area_is_one_click_and_frames_the_map(self, page: Chrome) -> None:
        assert page.click_button_matching("^use the sample area$")
        assert page.wait_until(
            "/8[.,]5\\d\\s*km²/.test(document.querySelector('.area-readout')?.innerText ?? '')",
            timeout=5,
        ), _readout(page)
        assert _run_enabled(page)
        _settle(page, 1.5)  # the map flies to it

    def test_a_real_drag_draws_a_rectangle_with_a_live_size(self, page: Chrome) -> None:
        assert page.click_button_matching("^redraw the rectangle$")
        box = _map_box(page)
        cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        _drag(page, (cx - 90, cy - 60), (cx + 90, cy + 60))
        assert page.wait_until(
            "!document.body.innerText.includes('Press and drag')", timeout=5
        ), "draw mode did not end on release"
        text = _readout(page)
        assert "km²" in text and "\u00d7" in text, text  # the width-by-height line
        assert "limit" not in text.lower() and "too small" not in text.lower(), text
        assert _run_enabled(page)

    def test_map_drags_pan_again_after_drawing(self, page: Chrome) -> None:
        """Leaving draw mode must hand drags back to the map."""
        before = _readout(page)
        box = _map_box(page)
        cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        _drag(page, (cx, cy), (cx + 40, cy + 30))
        _settle(page, 0.5)
        assert _readout(page) == before, "a plain drag redrew the rectangle"


class TestRunningTheSampleArea:
    @pytest.fixture(scope="class")
    def ran(self, page: Chrome) -> Chrome:
        assert page.click_button_matching("^use the sample area$")
        # Enrichment off: this is about the area path and the overlays.
        page.evaluate(
            "(() => { const c = [...document.querySelectorAll('.jobsheet input[type=checkbox]')]"
            ".find(x => x.closest('label')?.innerText.includes('Fetch soil'));"
            " if (c && c.checked) c.click(); })()"
        )
        assert page.click_button_matching("^run$"), "Run was not enabled"
        assert page.wait_until(
            "document.querySelectorAll('.site-rank').length > 0", timeout=ANALYSIS_TIMEOUT_S
        ), f"no sites came back: {page.text[-600:]}"
        _settle(page)
        return page

    def test_the_selected_marker_carries_the_water_it_collects(self, ran: Chrome) -> None:
        """On the map itself, not only in a panel: the recommended site is selected
        after a run, and its label reads `#1 · <volume> m³`."""
        label = str(ran.evaluate("document.querySelector('.site-rank--selected')?.textContent"))
        assert label.startswith("#1 · ") and label.endswith("m³"), label

    def test_every_other_marker_has_its_volume_one_hover_away(self, ran: Chrome) -> None:
        titles = ran.evaluate(
            "[...document.querySelectorAll('.site-rank:not(.site-rank--selected)')]"
            ".map(e => e.title)"
        )
        assert isinstance(titles, list)
        assert all("m³ a year" in str(t) for t in titles), titles

    def test_the_recommended_site_is_not_hidden_under_the_legend(self, ran: Chrome) -> None:
        """Framed clear of the legend: the first screenshots had #1 underneath it."""
        overlap = ran.evaluate(
            "(() => { const m = document.querySelector('.site-rank--selected')"
            "?.getBoundingClientRect();"
            " const l = document.querySelector('.legendbox:not(.is-collapsed)')"
            "?.getBoundingClientRect();"
            " if (!m || !l) return false;"
            " return !(m.right < l.left || m.left > l.right"
            "   || m.bottom < l.top || m.top > l.bottom);"
            " })()"
        )
        assert overlap is False

    def test_the_catchment_is_labelled_with_its_area(self, ran: Chrome) -> None:
        label = ran.evaluate("document.querySelector('.catchment-label')?.textContent ?? ''")
        assert "ha catchment" in str(label), label

    def test_the_readback_and_title_block_name_the_terrain(self, ran: Chrome) -> None:
        text = ran.text
        assert "Copernicus GLO-30" in text
        assert "8.53 km²" in text

    def test_the_proposal_gives_all_three_results(self, ran: Chrome) -> None:
        text = ran.text
        for words in ("Pond location", "Catchment", "Water collected"):
            assert words in text, f"{words!r} missing from the proposal"

    def test_every_site_is_inside_the_rectangle(self, ran: Chrome, frontend: str) -> None:
        """Read the job back through the export link the page itself offers."""
        href = ran.evaluate(
            "[...document.querySelectorAll('a')].find(a => a.textContent.includes('GeoJSON'))?.href"
        )
        assert href and "/api/v1/export/" in str(href), href
        # The link carries `?client=` for the gateway's affinity -- and this read
        # must carry it too: behind the gateway, a call without it is routed by
        # address and can reach an instance that never ran the job.
        path, _, query = str(href).partition("?")
        job_id = path.rsplit("/", 1)[-1]
        assert "client=" in query, "the export link lost its client id"
        url = f"{frontend}/api/v1/analysis/{job_id}/result?{query}"
        with urlopen(url, timeout=60) as r:
            result = json.load(r)["result"]
        assert result["terrain_source"]["kind"] == "copernicus_glo30"
        w, s, e, n = SAMPLE_BBOX
        for site in result["candidate_sites"]:
            loc = site["location"]
            assert w <= loc["lon"] <= e and s <= loc["lat"] <= n, f"#{site['rank']} at {loc}"

    def test_the_export_link_downloads(self, ran: Chrome) -> None:
        href = ran.evaluate(
            "[...document.querySelectorAll('a')].find(a => a.textContent.includes('GeoJSON'))?.href"
        )
        with urlopen(str(href), timeout=60) as r:
            assert r.status == 200
            assert json.load(r)["features"]

    def test_clicking_a_marker_opens_the_three_results(self, ran: Chrome) -> None:
        assert ran.evaluate(
            "(() => { const m = document.querySelector('.site-rank'); if (!m) return false;"
            " m.click(); return true; })()"
        )
        assert ran.wait_until("!!document.querySelector('.site-popup')", timeout=5)
        popup = str(ran.evaluate("document.querySelector('.site-popup').innerText"))
        for words in ("Pond location", "Catchment", "Collects", "Inflow"):
            assert words in popup, popup

    def test_drawing_does_not_delineate_and_leaving_it_does(self, ran: Chrome) -> None:
        ran.evaluate(
            "document.querySelector('.site-popup .maplibregl-popup-close-button')?.click()"
        )
        _settle(ran, 0.5)
        # Somewhere inside the analysed area where nothing sits on top of the map:
        # a click on a marker or a label selects a site instead of delineating.
        spot = ran.evaluate(
            "(() => { const r = document.querySelector('.drawing .map').getBoundingClientRect();"
            " const ys = [0.55, 0.45, 0.62, 0.38, 0.5], xs = [0.45, 0.55, 0.38, 0.62, 0.5];"
            " for (const fy of ys) for (const fx of xs) {"
            "   const x = r.x + r.width * fx, y = r.y + r.height * fy;"
            "   const el = document.elementFromPoint(x, y);"
            "   if (el && el.classList.contains('maplibregl-canvas')) return [x, y]; }"
            " return null; })()"
        )
        assert isinstance(spot, list), "no clear spot on the map to click"
        assert ran.click_button_matching("^redraw the rectangle$")
        _click_at(ran, *spot)
        _settle(ran)
        assert "drains to that point" not in ran.text, "a click in draw mode delineated"
        _press_escape(ran)
        assert ran.wait_until("!document.body.innerText.includes('Press and drag')", timeout=5)
        _click_at(ran, *spot)
        assert ran.wait_until(
            "document.body.innerText.includes('drains to that point')", timeout=30
        ), "click-to-delineate did not come back after drawing"

    def test_the_brief_reports_the_area_run(self, ran: Chrome) -> None:
        """Client-side navigation keeps the run; the brief must not assume a sheet."""
        assert ran.evaluate(
            "(() => { const a = [...document.querySelectorAll('a')]"
            ".find(x => x.getAttribute('href') === '/');"
            " if (!a) return false; a.click(); return true; })()"
        )
        assert ran.wait_until(
            "document.body.innerText.includes('This area, read end to end')", timeout=10
        ), ran.text[:800]
        # A stamp label: CSS uppercases it, and innerText reports what is shown.
        assert "water collected" in ran.text.lower()

    def test_nothing_was_logged_as_an_error(self, ran: Chrome) -> None:
        # ERR_NETWORK_CHANGED is the host's network changing under the browser
        # (a Docker network created mid-run), not something the app did.
        noise = ("favicon", "ERR_NETWORK_CHANGED")
        errors = [e for e in ran.console_errors() if not any(n in e for n in noise)]
        assert not errors, errors
