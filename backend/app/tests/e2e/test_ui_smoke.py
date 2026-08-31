"""The whole system, driven the way a user drives it.

Everything else in this suite tests a layer in isolation. This test uploads the
contour map through the browser's own file input, at the origin nginx serves,
and asserts the numbers land on screen. It is the only test that would notice
nginx capping the request body below the API's limit, a bundle that fails to
mount, or a response field the UI reads under a name the API stopped using.

It is skipped unless the compose stack is up and a Chrome is on PATH; see
``conftest.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.tests.e2e.cdp import Chrome

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.network]

MOUNT_TIMEOUT_S = 60.0
ANALYSIS_TIMEOUT_S = 240.0


#: The findings pane, in the order the tabs appear. Every one is opened once by
#: the fixture so that assertions about *what the analysis reported* can read a
#: single text blob, while assertions about *how something is drawn* open the
#: tab they need. Before the redesign every one of these was on one page.
FINDINGS_TABS = ("Proposal", "Candidates", "Hydrology", "Yield", "Caveats")


def open_tab(page: Chrome, label: str) -> bool:
    """Bring one findings tab to the front. False if there is no such tab."""
    return bool(
        page.evaluate(
            "(() => { const b = [...document.querySelectorAll('.f-tabs button')]"
            f".find(x => x.textContent.trim().toLowerCase() === {label.lower()!r});"
            " if (!b) return false; b.click(); return true; })()"
        )
    )


@pytest.fixture(scope="module")
def analysed(chrome_binary, frontend_url, sample_contour_map, cdp, tmp_path_factory):
    """Run the sample sheet through the workspace and hand back the page.

    Module-scoped: one browser and one analysis serve every assertion below,
    because the analysis is the slow part.

    The upload lives at /workspace, not at the root: the root is the brief. That
    is the whole reason this fixture is worth reading — the previous version
    navigated to `frontend_url` and every test in the module errored at setup
    with "no file input rendered" once the app gained routes.

    It yields `(page, text, shot)` where `text` is the concatenation of all five
    findings tabs, so an assertion that some figure *reached the screen* does not
    have to know which tab carries it. Anything asserting geometry opens its own
    tab first.
    """
    with Chrome(chrome_binary) as page:
        page.navigate(f"{frontend_url}/workspace")

        assert page.wait_until(
            "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
        ), "React never mounted -- the bundle failed to load or threw on start"

        assert page.wait_until(
            "!!document.querySelector('input[type=file]')", timeout=MOUNT_TIMEOUT_S
        ), "no file input rendered on the workspace"

        page.attach_file("input[type=file]", str(sample_contour_map))
        assert page.wait_until(
            "!!document.body.innerText.match(/contours_1m/)", timeout=15
        ), "the chosen file never appeared in the job sheet"

        assert page.click_button_matching("^run$"), "no enabled Run button to click"

        assert page.wait_until(
            "/[\\d,]+\\s*m\\u00b3/.test(document.body.innerText)",
            timeout=ANALYSIS_TIMEOUT_S,
        ), "no pond volume ever rendered -- the analysis did not complete"

        # The map frames the data over ~900 ms; let it settle before the shot.
        page.wait_until("false", timeout=4, poll=1.0)
        shot = tmp_path_factory.mktemp("e2e") / "ui.png"
        page.screenshot(str(shot))

        collected = []
        for tab in FINDINGS_TABS:
            assert open_tab(page, tab), f"no {tab} tab in the findings pane"
            page.wait_until("false", timeout=1.2, poll=0.4)
            collected.append(page.text)
        # Back to where a reader starts, so a test that opens no tab sees the
        # proposal rather than whatever ran last.
        open_tab(page, "Proposal")
        page.wait_until("false", timeout=1.2, poll=0.4)

        yield page, "\n".join(collected), shot


class TestTheAppLoads:
    def test_the_map_canvas_initialises(self, analysed):
        page, _, _ = analysed
        assert page.evaluate(
            "!!document.querySelector('.maplibregl-canvas')"
        ), "MapLibre never created its canvas"
        assert page.evaluate(
            "(document.querySelector('.maplibregl-canvas')?.width || 0) > 100"
        ), "the map canvas has no drawing surface"

    def test_nothing_throws_in_the_console(self, analysed):
        page, _, _ = analysed
        assert page.console_errors() == []

    def test_every_layer_has_a_toggle(self, analysed):
        page, text, _ = analysed
        assert page.evaluate("document.querySelectorAll('input[type=checkbox]').length") >= 4
        for layer in ("Contours", "Catchment", "Candidate sites", "Survey extent"):
            assert layer in text


class TestTheAnalysisReachesTheScreen:
    """Each assertion names a value that only a real end-to-end run can produce."""

    def test_it_reports_what_it_read_from_the_file(self, analysed):
        _, text, _ = analysed
        assert "Placemark name" in text, "the elevation strategy is not shown"
        assert re.search(r"1,355", text), "the parsed contour-line count is not shown"
        assert "EPSG:32644" in text, "the derived working CRS is not shown"

    def test_it_reports_a_catchment_with_an_area(self, analysed):
        _, text, _ = analysed
        assert "Catchment" in text
        assert re.search(r"\d[\d,.]*\s*(ha|km²)", text), "no catchment area with a unit"

    def test_it_sizes_a_pond(self, analysed):
        _, text, _ = analysed
        assert re.search(r"[\d,]+\s*m³", text), "no pond capacity"
        assert re.search(r"design depth", text, re.I), "no pond depth reported"
        assert re.search(r"[\d.]+\s*m\b", text), "the depth carries no unit"
        assert re.search(r"binding constraint", text, re.I), "the binding constraint is not named"

    def test_no_cost_figure_is_shown(self, analysed):
        """The UI deliberately carries no cost.

        This asserted the opposite: that an indicative cost appeared, grouped in
        lakhs and crores. It was dropped from the interface on purpose — the
        question here is where a pond should go and how big the ground will let it
        be, and a figure derived from a unit rate invites a budget conversation
        the terrain cannot support. Inverted rather than deleted, so the row
        cannot quietly return.
        """
        _, text, _ = analysed
        assert "\u20b9" not in text, "a rupee figure is on screen"
        assert not re.search(r"indicative cost", text, re.I), "a cost row is on screen"

    def test_it_ranks_more_than_one_site(self, analysed):
        _, text, _ = analysed
        assert "#1" in text and "#2" in text, "only one candidate site reached the screen"
        assert re.search(r"\d+\.\d/100", text), "no suitability score"


class TestItIsHonestAboutWhatItKnows:
    def test_it_states_which_tier_the_answer_came_from(self, analysed):
        _, text, _ = analysed
        tiers = ("Full", "No soil or land cover", "Terrain only")
        assert any(t in text for t in tiers), f"no tier banner; expected one of {tiers}"
        # Whichever tier it is, the banner explains what that means.
        assert "—" in text

    def test_a_provider_failure_is_named_not_hidden(self, analysed):
        """If a provider was down, the reason is on screen rather than a blank field.

        The tier is read from the banner element rather than by splitting the page
        text on its first em-dash. That is how this was written first, and it broke
        as soon as the layer panel gained hints like " -- search for a village":
        those sit *above* the results in the DOM, so the split cut long before the
        banner and the skip never fired -- the test then demanded a failure notice
        on a run where nothing had failed.
        """
        page, text, _ = analysed
        # The tier and its meaning are reported in the Caveats pane — the place a
        # reader goes to find out what was *not* available.
        open_tab(page, "Caveats")
        page.wait_until("!!document.querySelector('.tier')", timeout=6)
        banner = str(page.evaluate("document.querySelector('.tier')?.innerText") or "")
        assert banner, "no tier banner rendered"
        tier = banner.split("\u2014")[0].strip().lower()
        if tier.startswith("full"):
            pytest.skip(f"every layer was available on this run ({tier!r})")
        assert "unavailable" in text, (
            f"the answer degraded to {tier!r} but the UI does not say which layer "
            "was lost or why"
        )

    def test_the_render_is_captured_for_the_report(self, analysed):
        _, _, shot = analysed
        assert Path(shot).stat().st_size > 20_000, "screenshot is suspiciously small"


class TestTheStageStorageCurveReads:
    """The stage-storage chart and the pond footprint (M5-13)."""

    @staticmethod
    def _figure(page):
        """Open the tab the curve lives on, then check it is there.

        The chart is in the Hydrology pane since the findings were split into
        tabs. Probing for `.chart-line` without opening it skips every assertion
        in this class on a run that produced a perfectly good curve — a false
        pass, which is worse than a failure.
        """
        open_tab(page, "Hydrology")
        page.wait_until("!!document.querySelector('.chart-line')", timeout=6)
        if not page.evaluate("document.querySelectorAll('.chart-line').length"):
            pytest.skip("no pond was sized on this run, so no stage-storage curve")

    def test_it_draws_the_curve(self, analysed):
        page, _, _ = analysed
        self._figure(page)
        points = page.evaluate(
            "[...document.querySelectorAll('.chart--stage .chart-line')]"
            ".reduce((n,l)=>n+l.getAttribute('points').trim().split(/\\s+/).length,0)"
        )
        assert int(points) >= 2, "a curve needs at least two points"

    def test_storage_never_falls_as_depth_rises(self, analysed):
        """The invariant that broke: a capped flood fill reported storage of
        79,473 m3 at 2.00 m against 45,336 m3 at 4.25 m, and the descending line
        was visible the moment it was plotted. Asserted on what reaches the
        screen, not only in the unit test, because this is where it showed.
        """
        page, _, _ = analysed
        self._figure(page)
        rows = page.evaluate(
            # textContent, not innerText: the table lives inside a closed
            # <details>, so it is not rendered and innerText returns nothing.
            "JSON.stringify([...document.querySelectorAll('.chart--stage tbody tr')]"
            ".map(tr => [...tr.children].map(c => c.textContent.replace(/,/g,''))))"
        )
        table = json.loads(str(rows))
        assert table, "no stage-storage table rendered"
        volumes = [float(r[1]) for r in table]
        assert volumes == sorted(volumes), f"storage fell as depth rose: {volumes}"

    def test_the_uncontained_part_is_marked_by_more_than_colour(self, analysed):
        """Colour alone must never carry the distinction (WCAG 1.4.1)."""
        page, text, _ = analysed
        self._figure(page)
        dashed = int(page.evaluate("document.querySelectorAll('.chart-line--dashed').length") or 0)
        if not dashed:
            pytest.skip("terrain contained the water over the whole curve on this run")
        assert "not contained" in text, "no legend entry for the uncontained segment"
        assert "terrain stops holding the water" in text, "no explanation of why it stops"

    def test_the_pond_footprint_is_drawn_on_the_map(self, analysed):
        page, _, _ = analysed
        drawn = page.evaluate(
            "(() => { const m = document.querySelector('.maplibregl-canvas'); return !!m; })()"
        )
        assert drawn, "no map canvas"
        # The toggle is the observable part: the layer only offers itself when a
        # design exists, and it is dashed because the orientation is indicative.
        assert page.evaluate(
            "!!document.body.innerText.match(/Pond footprint/)"
        ), "no pond footprint layer toggle"

    def test_available_land_is_offered_but_not_forced(self, analysed):
        """It costs a WorldCover read and an Overpass call, so it is on request."""
        page, text, _ = analysed
        assert "Available land" in text, "no available-land panel"
        assert re.search(r"available land", text, re.I), "no way to load the parcels"


class TestTheWaterBalanceReads:
    """The rainfall chart and runoff tiles.

    The chart only exists when rainfall was fetched, so every assertion here
    skips on a terrain-only run rather than failing for a missing provider.
    """

    @staticmethod
    def _chart(page):
        """Open the Yield tab, which is where the rainfall chart now lives."""
        open_tab(page, "Yield")
        page.wait_until("!!document.querySelector('.chart--rainfall path')", timeout=6)
        bars = page.evaluate("document.querySelectorAll('.chart--rainfall path').length")
        if not bars:
            pytest.skip("no rainfall on this run, so no water-balance chart")
        return int(bars)

    def test_it_draws_one_column_per_month(self, analysed):
        page, _, _ = analysed
        assert self._chart(page) == 12, "a monthly chart that is not twelve columns"

    def test_the_columns_carry_two_fills_not_twelve(self, analysed):
        """One measure means one hue; the monsoon is emphasis, not a second series.

        Twelve fills would mean the identity channel had been spent re-encoding
        what the column heights already show.
        """
        page, _, _ = analysed
        self._chart(page)
        fills = page.evaluate(
            "JSON.stringify([...new Set([...document.querySelectorAll('.chart--rainfall path')]"
            ".map(p => p.getAttribute('fill')))])"
        )
        assert len(json.loads(str(fills))) == 2, f"expected an emphasis pair, got {fills}"

    def test_it_labels_the_extreme_only(self, analysed):
        """A number on every column goes unread; the axis and tooltip carry the rest."""
        page, _, _ = analysed
        self._chart(page)
        labels = page.evaluate("document.querySelectorAll('.chart--rainfall .chart-value').length")
        assert int(labels) == 1, f"{labels} direct labels on a twelve-column chart"

    def test_no_month_label_collides_with_its_neighbour(self, analysed):
        page, _, _ = analysed
        self._chart(page)
        gap = page.evaluate(
            "(() => { const l=[...document.querySelectorAll('.chart--rainfall .chart-month')]"
            ".map(t=>t.getBoundingClientRect()); let w=1e9;"
            " for(let i=1;i<l.length;i++) w=Math.min(w,l[i].left-l[i-1].right);"
            " return w; })()"
        )
        assert float(gap) > 0, f"month labels overlap by {-float(gap):.1f}px"

    def test_the_values_are_also_available_as_a_table(self, analysed):
        """The recessive fill sits below 3:1, which obliges a readable fallback."""
        page, _, _ = analysed
        self._chart(page)
        rows = page.evaluate(
            "document.querySelectorAll('.chart--rainfall .chart-table tbody tr').length"
        )
        assert int(rows) == 12, "no twelve-row table view behind the chart"

    def test_the_runoff_headlines_are_tiles(self, analysed):
        page, text, _ = analysed
        self._chart(page)
        tiles = int(page.evaluate("document.querySelectorAll('.kpi').length") or 0)
        if not tiles:
            pytest.skip("runoff was not estimated on this run")
        # The tile label is upper-cased in CSS, and Chrome's innerText reflects
        # text-transform, so match without regard to case.
        assert re.search(r"annual runoff", text, re.I), "the runoff tile is not labelled"
        assert re.search(r"C = \d\.\d+", text, re.I), "no runoff coefficient beside the volume"


class TestTheProgressBarTracksTheWork:
    """The live analysis progress bar (M6-12).

    Drives its own session because the bar only exists while a job is running,
    and the module-scoped `analysed` fixture has already finished by the time any
    assertion sees it. Samples the bar as the job goes so the *sequence* can be
    checked, not just that a bar appeared.
    """

    @pytest.fixture(scope="class")
    def samples(self, chrome_binary, frontend_url, sample_contour_map, cdp):
        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}/workspace")
            assert page.wait_until(
                "!!document.querySelector('input[type=file]')", timeout=MOUNT_TIMEOUT_S
            )
            page.attach_file("input[type=file]", str(sample_contour_map))
            assert page.wait_until("!!document.body.innerText.match(/contours_1m/)", timeout=15)
            assert page.click_button_matching("^run$")

            if not page.wait_until("!!document.querySelector('.job-track')", timeout=30):
                pytest.skip("the analysis finished before the bar could be sampled")

            seen: list[dict] = []
            for _ in range(60):
                raw = page.evaluate(
                    "(() => { const j = document.querySelector('.job');"
                    " if (!j) return null;"
                    " const bar = j.querySelector('[role=progressbar]');"
                    " return JSON.stringify({"
                    "   pct: Number(bar.getAttribute('aria-valuenow')),"
                    "   fill: j.querySelector('.job-fill').style.width,"
                    "   step: (j.querySelector('.job-head span')||{}).textContent,"
                    "   steps: [...j.querySelectorAll('.job-step')].length,"
                    " }); })()"
                )
                if raw is None:
                    break
                sample = json.loads(str(raw))
                if not seen or seen[-1] != sample:
                    seen.append(sample)
                page.wait_until("false", timeout=1, poll=0.5)

            assert page.wait_until(
                "/[\\d,]+\\s*m\u00b3/.test(document.body.innerText)",
                timeout=ANALYSIS_TIMEOUT_S,
            ), "the analysis never finished"
            yield seen

    def test_the_bar_reports_a_percentage(self, samples) -> None:
        assert samples, "no progress samples captured"
        assert all(0 <= s["pct"] <= 100 for s in samples), samples

    def test_it_never_goes_backwards(self, samples) -> None:
        percentages = [s["pct"] for s in samples]
        assert percentages == sorted(percentages), percentages

    def test_the_fill_width_matches_the_reported_value(self, samples) -> None:
        """A bar whose width disagrees with its own aria value lies to someone."""
        for sample in samples:
            assert sample["fill"] == f"{sample['pct']}%", sample

    def test_it_names_the_step_it_is_on(self, samples) -> None:
        """Every label the bar shows must be a real pipeline stage.

        Deliberately not asserting that the *enrichment* step is seen. It was,
        while that step took twenty seconds on a cold cache — but once the soil
        and land-cover caches are warm it finishes in well under the one-second
        poll interval, and demanding it here would fail for the good reason that
        the cache is working.
        """
        known = {
            "Reading the contour map",
            "Interpolating terrain",
            "Conditioning the surface",
            "Routing flow",
            "Fetching soil, land cover and rainfall",
            "Scoring candidate sites",
            "Delineating catchments and sizing ponds",
            # Shown between the last step finishing and the job settling.
            "Finishing up",
            "Waiting for a worker",
        }
        steps = {s["step"] for s in samples if s["step"]}
        assert steps, "the bar never named a step"
        unknown = steps - known
        assert not unknown, f"the bar showed a label that is not a pipeline stage: {unknown}"

    def test_every_pipeline_stage_is_drawn(self, samples) -> None:
        # Seven stages, so a reader can see how much is left rather than only
        # how far it has come.
        assert all(s["steps"] == 7 for s in samples), [s["steps"] for s in samples]

    def test_the_slow_step_dominates_the_bar(self, samples) -> None:
        """Enrichment is 82 % of a cold run and the bar has to show that.

        If the four cheap stages before it pushed the bar past a third, the
        remaining twenty seconds would read as a stall.
        """
        before_fetch = [
            s["pct"] for s in samples if s["step"] and "oil" not in s["step"] and s["pct"] < 90
        ]
        if before_fetch:
            assert max(before_fetch) < 35, (
                f"the bar reached {max(before_fetch)}% before the slow step; "
                "the weighting is not being applied"
            )


class TestItFailsGracefully:
    """A rejected upload must explain itself, not blank the page.

    The API answers bad input with RFC 7807 problem details. That contract is
    only worth having if the `detail` sentence reaches the person who chose the
    file, so this drives its own browser session and reads what is on screen.
    """

    @pytest.fixture(scope="class")
    def rejected(self, chrome_binary, frontend_url, cdp, tmp_path_factory):
        bad = tmp_path_factory.mktemp("bad") / "not-a-contour-map.kml"
        bad.write_text("this is not a contour map at all\n", encoding="utf-8")

        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}/workspace")
            assert page.wait_until(
                "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
            )
            assert page.wait_until(
                "!!document.querySelector('input[type=file]')", timeout=MOUNT_TIMEOUT_S
            )
            page.attach_file("input[type=file]", str(bad))
            assert page.wait_until(
                "!!document.body.innerText.match(/not-a-contour-map/)", timeout=15
            )
            assert page.click_button_matching("^run$")
            assert page.wait_until(
                "!!document.querySelector('[role=alert]')", timeout=90
            ), "the upload was rejected but nothing was shown to the user"
            yield str(page.evaluate("document.querySelector('[role=alert]')?.innerText") or "")

    def test_the_reason_is_the_api_s_own_words(self, rejected):
        # Not a generic "something went wrong" -- the API said what was wrong
        # with this file, and that sentence is what the user reads.
        assert "XML" in rejected or "KML" in rejected, rejected

    def test_a_trace_id_is_offered(self, rejected):
        assert re.search(r"trace\s+[0-9a-f]{6,}", rejected, re.I), rejected

    def test_the_user_can_dismiss_it_and_try_again(self, rejected):
        assert re.search(r"dismiss", rejected, re.I), rejected


class TestVillageSearchInTheBrowser:
    """The search path, driven through the real input (M2-8).

    Everything here failed at least once during development in a way no unit or
    API test could see: an invalid `line-dasharray` expression that MapLibre
    dropped without a word, and a click that reopened the very list it had just
    closed.
    """

    @pytest.fixture(scope="class")
    def searched(self, chrome_binary, frontend_url, cdp, tmp_path_factory):
        """Type a misspelled village name and choose the first suggestion."""
        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}/workspace")
            assert page.wait_until(
                "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
            )
            if not page.wait_until(
                "!!document.querySelector('input[type=search]')", timeout=MOUNT_TIMEOUT_S
            ):
                pytest.skip("no village search input rendered")

            # Set the value the way a browser does, so React's onChange fires.
            page.evaluate(
                """
                (() => {
                  const input = document.querySelector('input[type=search]');
                  const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, 'kutelabhata');
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                })()
                """
            )
            if not page.wait_until(
                "document.querySelectorAll('[role=option]').length > 0", timeout=60
            ):
                pytest.skip("no suggestions returned; is the village index seeded?")

            options = str(
                page.evaluate(
                    "[...document.querySelectorAll('[role=option]')]"
                    ".map(o => o.innerText).join('\\n')"
                )
                or ""
            )
            page.evaluate("document.querySelector('[role=option] button')?.click()")
            page.wait_until("false", timeout=5, poll=1.0)  # let fitBounds settle
            shot = tmp_path_factory.mktemp("village") / "search.png"
            page.screenshot(str(shot))
            yield page, options

    def test_a_misspelling_finds_the_registers_spelling(self, searched) -> None:
        """`kutelabhata` typed, `Kutelabhatha` found — the fold, end to end."""
        _, options = searched
        assert "Kutelabhatha" in options, options

    def test_the_best_match_is_first(self, searched) -> None:
        _, options = searched
        assert options.splitlines()[0].strip().startswith("Kutelabhatha"), options

    def test_each_suggestion_shows_where_it_is(self, searched) -> None:
        """Eight villages named Kutela; the place is what separates them."""
        _, options = searched
        assert "Durg" in options.splitlines()[0] or "Durg" in options.splitlines()[1]

    def test_choosing_one_closes_the_list(self, searched) -> None:
        """It reopened itself once: `choose` closed it, and the resulting query
        change immediately searched again and reopened it, so clicking a result
        appeared to do nothing."""
        page, _ = searched
        assert page.evaluate("document.querySelectorAll('[role=option]').length") == 0

    def test_the_map_says_the_outline_is_not_the_village(self, searched) -> None:
        """The caveat is the API refusing to overstate what it returned."""
        page, _ = searched
        text = page.text
        assert "not the village boundary" in text, text[:400]

    def test_the_outline_layer_is_offered_as_a_toggle(self, searched) -> None:
        page, _ = searched
        assert "Village outline" in page.text

    def test_the_map_reported_no_style_errors(self, searched) -> None:
        """The regression this exists for.

        `line-dasharray` is a cross-faded property whose expressions accept only
        `zoom`, so a `["case", ["get", ...]]` there is invalid — and MapLibre
        dropped it silently, with nothing in the console and no exception. The
        map now forwards its own `error` event to console.error, which is what
        makes this assertion able to see it at all.
        """
        page, _ = searched
        maplibre_errors = [e for e in page.console_errors() if "maplibre" in e.lower()]
        assert maplibre_errors == [], maplibre_errors


class TestClickToDelineate:
    """M3's exit criterion: three clicks, three visibly different catchments.

    That is the evidence the flow routing is doing something rather than being
    taken on trust. It is also the one assertion here that cannot be made against
    the API alone -- it needs the click to reach the map, the map to hand a
    coordinate to the request, and the result to reach the panel.
    """

    #: Fractions of the map viewport. Spread out enough that a shared catchment
    #: would be a genuine finding rather than an artefact of clicking twice in
    #: the same place.
    SPOTS = ((0.45, 0.40), (0.55, 0.62), (0.38, 0.55))

    @pytest.fixture(scope="class")
    def clicked(self, chrome_binary, frontend_url, sample_contour_map, cdp):
        import json as _json
        import time as _time

        with Chrome(chrome_binary) as page:
            page.navigate(f"{frontend_url}/workspace")
            assert page.wait_until(
                "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
            )
            page.attach_file("input[type=file]", str(sample_contour_map))
            page.wait_until("!!document.body.innerText.match(/contours_1m/)", timeout=15)
            assert page.click_button_matching("^run$")
            assert page.wait_until(
                "/[\\d,]+\\s*m\\u00b3/.test(document.body.innerText)",
                timeout=ANALYSIS_TIMEOUT_S,
            )
            if not page.wait_until("!!document.querySelector('#explored-heading')", timeout=20):
                pytest.skip("the click-to-delineate panel did not appear")

            # Let the map frame the survey, or the clicks land outside it.
            page.wait_until("false", timeout=7, poll=1.0)

            box = _json.loads(
                str(
                    page.evaluate(
                        "JSON.stringify((r => ({x:r.left, y:r.top, w:r.width, h:r.height}))"
                        "(document.querySelector('.map').getBoundingClientRect()))"
                    )
                )
            )

            areas: list[str] = []
            for fraction_x, fraction_y in self.SPOTS:
                x = int(box["x"] + box["w"] * fraction_x)
                y = int(box["y"] + box["h"] * fraction_y)
                for kind in ("mousePressed", "mouseReleased"):
                    page.send(
                        "Input.dispatchMouseEvent",
                        type=kind,
                        x=x,
                        y=y,
                        button="left",
                        clickCount=1,
                    )

                # Wait for the panel to *change*. After the first click it always
                # contains an area, so a containment check returns before the new
                # result arrives and every click appears to give the same answer.
                previous = areas[-1] if areas else ""
                deadline = _time.time() + 45
                found = ""
                while _time.time() < deadline:
                    text = str(
                        page.evaluate(
                            # The whole note, not the heading's parent: the
                            # heading now sits in a title bar beside the collapse
                            # control, so `parentElement` reads the bar alone and
                            # never the area below it.
                            "document.querySelector('#explored-heading')"
                            "?.closest('.drawing-note')?.innerText"
                        )
                        or ""
                    )
                    candidate = next(
                        (
                            line.strip()
                            for line in text.splitlines()
                            if "ha" in line or "km²" in line
                        ),
                        "",
                    )
                    if candidate and candidate != previous:
                        found = candidate
                        break
                    _time.sleep(0.5)
                areas.append(found)
            yield page, areas

    def test_every_click_returns_a_catchment(self, clicked) -> None:
        _, areas = clicked
        assert all(areas), f"a click produced no catchment: {areas}"

    def test_three_clicks_give_three_different_catchments(self, clicked) -> None:
        """The exit criterion. Identical areas would mean the pour point is not
        reaching the routing, or the routing is not using it."""
        _, areas = clicked
        assert len(set(areas)) == len(areas), areas

    def test_the_areas_carry_units(self, clicked) -> None:
        _, areas = clicked
        assert all("ha" in a or "km²" in a for a in areas), areas

    def test_the_clicked_catchment_is_offered_as_its_own_layer(self, clicked) -> None:
        """Styled and toggled apart from the analysis' catchment, so the two are
        never mistaken for each other."""
        page, _ = clicked
        assert "Clicked catchment" in page.text

    def test_nothing_throws(self, clicked) -> None:
        page, _ = clicked
        noise = ("429", "503", "rest.isric.org", "open-meteo")
        errors = [e for e in page.console_errors() if not any(n in e for n in noise)]
        assert errors == [], errors


class TestTwoRainfallSourcesKeepTheAnalysisAlive:
    """The robustness the ensemble bought.

    Open-Meteo enforces a daily request limit and it does get hit. Before a second
    source existed, that dropped the whole analysis to `terrain_only` -- no runoff,
    no pond volume, no cross-check. NASA POWER now answers instead, so a rainfall
    tier is reached either way and the response names which source supplied it.
    """

    def test_a_rainfall_tier_is_reached(self, analysed) -> None:
        page, text, _ = analysed
        # The tier and its meaning are reported in the Caveats pane — the place a
        # reader goes to find out what was *not* available.
        open_tab(page, "Caveats")
        page.wait_until("!!document.querySelector('.tier')", timeout=6)
        banner = str(page.evaluate("document.querySelector('.tier')?.innerText") or "")
        tier = banner.split("—")[0].strip().lower()
        if tier.startswith("terrain only"):
            pytest.skip("no rainfall source answered on this run")
        assert re.search(
            r"rainfall", text, re.I
        ), f"tier is {tier!r}, which claims rainfall, but none is shown"

    def test_the_runoff_figure_survives_one_source_being_down(self, analysed) -> None:
        """One reanalysis being rate-limited is a reason to report less
        confidence, not to refuse an answer."""
        page, text, _ = analysed
        # The tier and its meaning are reported in the Caveats pane — the place a
        # reader goes to find out what was *not* available.
        open_tab(page, "Caveats")
        page.wait_until("!!document.querySelector('.tier')", timeout=6)
        banner = str(page.evaluate("document.querySelector('.tier')?.innerText") or "")
        if banner.split("—")[0].strip().lower().startswith("terrain only"):
            pytest.skip("no rainfall source answered on this run")
        assert re.search(
            r"runoff", text, re.I
        ), "a rainfall tier was reached but no runoff is reported"
