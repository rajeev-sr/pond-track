"""Controls added after live testing, exercised in a real browser.

Each answers a question a user actually asked while using the app, and each is
the kind of thing a unit test cannot confirm: whether the control is reachable,
whether it changes what is drawn, and whether the numbers beside it stay honest
when it does.

Its own module and its own browser: these assertions *mutate* the page, so they
cannot share `test_ui_smoke`'s read-only module fixture.
"""

from __future__ import annotations

import pytest

from app.tests.e2e.cdp import Chrome

MOUNT_TIMEOUT_S = 60.0
ANALYSIS_TIMEOUT_S = 300.0
SETTLE_S = 3.0


def _settle(page: Chrome, seconds: float = SETTLE_S) -> None:
    """Let an async fetch and re-render finish. `wait_until('false')` is the
    harness's sleep — it polls a condition that never holds until it times out."""
    page.wait_until("false", timeout=seconds, poll=0.5)


def _tab(page: Chrome, label: str) -> bool:
    """Bring one findings tab to the front."""
    return bool(
        page.evaluate(
            "(() => { const b = [...document.querySelectorAll('.f-tabs button')]"
            f".find(x => x.textContent.trim().toLowerCase() === {label.lower()!r});"
            " if (!b) return false; b.click(); return true; })()"
        )
    )


def _set_range(page: Chrome, selector: str, value: object) -> bool:
    """Move a React-controlled range input and actually trigger its `onChange`.

    Assigning `.value` is not enough: React keeps its own value tracker on the
    node, sees the property already equal to what the event reports, and skips the
    handler — the thumb moves and no state changes. Going through the prototype's
    native setter updates the node *and* leaves the tracker stale, so the
    dispatched event is recognised as a real change.
    """
    return bool(
        page.evaluate(
            "(() => {"
            f" const el = document.querySelector('{selector}');"
            " if (!el) return false;"
            " const setter = Object.getOwnPropertyDescriptor("
            "   window.HTMLInputElement.prototype, 'value').set;"
            f" setter.call(el, String({value!r}));"
            " el.dispatchEvent(new Event('input', { bubbles: true }));"
            " el.dispatchEvent(new Event('change', { bubbles: true }));"
            " return true; })()"
        )
    )


@pytest.fixture(scope="module")
def app(chrome_binary, frontend_url, sample_contour_map):
    """One analysed workspace, shared but written to — assertions run in file order."""
    with Chrome(chrome_binary) as page:
        # /workspace, not the root: the root is the brief since the app gained routes.
        page.navigate(f"{frontend_url}/workspace")
        assert page.wait_until(
            "document.querySelector('#root')?.children.length > 0", timeout=MOUNT_TIMEOUT_S
        ), "React never mounted"
        assert page.wait_until(
            "!!document.querySelector('input[type=file]')", timeout=MOUNT_TIMEOUT_S
        ), "no file input on the workspace"
        page.attach_file("input[type=file]", str(sample_contour_map))
        assert page.wait_until("!!document.body.innerText.match(/contours_1m/)", timeout=15)
        assert page.click_button_matching("^run$"), "no enabled Run button"
        assert page.wait_until(
            "/[\\d,]+\\s*m\\u00b3/.test(document.body.innerText)", timeout=ANALYSIS_TIMEOUT_S
        ), "the analysis never completed"
        _settle(page, 4.0)
        yield page


class TestTheDrainageNetworkExtentCanBeSwitched:
    """The network was clipped to the recommended site's catchment with no way to
    see the rest, which read as missing data rather than as a choice.

    Both are kept, because both are right for different questions: the site
    catchment is what drainage *density* describes (a basin property), and the
    whole sheet is what you read the terrain with.
    """

    def test_both_extents_are_offered(self, app) -> None:
        labels = app.evaluate(
            "[...document.querySelectorAll('.extent button')]"
            ".map(b => b.textContent.trim()).join('|')"
        )
        assert "Site catchment" in str(labels)
        assert "Whole sheet" in str(labels)

    def test_it_starts_on_the_site_catchment(self, app) -> None:
        pressed = app.evaluate(
            "[...document.querySelectorAll('.extent button')]"
            ".filter(b => b.getAttribute('aria-pressed') === 'true')"
            ".map(b => b.textContent.trim()).join(',')"
        )
        assert "Site catchment" in str(
            pressed
        ), f"expected the site catchment selected, got {pressed!r}"

    def test_the_state_is_not_carried_by_colour_alone(self, app) -> None:
        """A pressed chip must be announceable, not merely tinted (WCAG 1.4.1)."""
        assert app.evaluate(
            "[...document.querySelectorAll('.extent button')]"
            ".filter(b => /catchment|sheet/i.test(b.textContent))"
            ".every(b => b.hasAttribute('aria-pressed'))"
        )

    @staticmethod
    def _channels(page) -> int:
        """The channel count, read from its own table cell.

        Not by regexing the pane text: the row reads "Channels / above 1.00 ha /
        60", and a non-greedy match after the label picks up the 1 from the
        threshold. Both extents then reported "1 channel" and the comparison
        below passed for the wrong reason until it didn't.
        """
        _tab(page, "Hydrology")
        page.wait_until("/channels/i.test(document.body.innerText)", timeout=8)
        raw = page.evaluate(
            "(() => {"
            " const row = [...document.querySelectorAll('.f-body table.svy tr')]"
            "   .find(r => /^\\s*Channels/i.test(r.cells[0]?.textContent || ''));"
            " if (!row) return '0';"
            " const cell = row.querySelector('td.n');"
            " return cell ? cell.textContent.replace(/[^0-9]/g, '') : '0'; })()"
        )
        return int(str(raw) or 0)

    def test_switching_to_the_whole_sheet_draws_more_channels(self, app) -> None:
        """The point of the control: measurably more network, not just a label."""
        before = self._channels(app)
        assert before > 0, "no channel count on screen to compare against"

        assert app.evaluate(
            "(() => { const b = [...document.querySelectorAll('.extent button')]"
            ".find(x => /whole sheet/i.test(x.textContent));"
            " if (!b) return false; b.click(); return true; })()"
        ), "no 'Whole sheet' control to click"
        _settle(app, 8.0)

        after = self._channels(app)
        assert after > before, (
            f"the whole sheet reported {after} channels against {before} for the site "
            "catchment; the switch did not change what was fetched"
        )

    def test_the_pane_says_which_extent_is_shown(self, app) -> None:
        _tab(app, "Hydrology")
        _settle(app, 1.5)
        assert "whole sheet" in app.text.lower()

    def test_density_is_withheld_rather_than_computed_on_a_rectangle(self, app) -> None:
        """Drainage density is length per unit *basin* area.

        Over the survey rectangle it would average unrelated catchments, so the
        API returns null and the pane must show that rather than a plausible
        number.
        """
        _tab(app, "Hydrology")
        _settle(app, 1.5)
        assert (
            "not defined over the whole sheet" in app.text
        ), "the whole-sheet view must say why drainage density is unavailable"

    def test_switching_back_restores_the_site_catchment(self, app) -> None:
        assert app.evaluate(
            "(() => { const b = [...document.querySelectorAll('.extent button')]"
            ".find(x => /site catchment/i.test(x.textContent));"
            " if (!b) return false; b.click(); return true; })()"
        )
        _settle(app, 6.0)
        _tab(app, "Hydrology")
        _settle(app, 1.5)
        assert "km/km" in app.text, "density should be reported again for a basin"


class TestHowManySitesAreShown:
    """Three markers appeared with no hint that the number was a setting.

    Two separate things, deliberately: the job sheet's *candidate sites* asks how
    many to compute, and this filters how many of those are drawn — capped at how
    many the terrain actually yielded.
    """

    def test_the_control_is_on_the_candidates_pane(self, app) -> None:
        assert _tab(app, "Candidates"), "no Candidates tab"
        _settle(app, 1.5)
        assert app.evaluate("!!document.querySelector('#shown-sites')"), "no sites-shown control"

    def test_it_cannot_ask_for_more_than_were_found(self, app) -> None:
        _tab(app, "Candidates")
        _settle(app, 1.5)
        bounds = app.evaluate(
            "(() => { const el = document.querySelector('#shown-sites');"
            " return el ? el.min + ':' + el.max + ':' + el.value : null; })()"
        )
        assert bounds, "no sites-shown slider rendered"
        low, high, value = (int(part) for part in str(bounds).split(":"))
        assert low == 1
        assert value <= high, "the slider cannot start above its own maximum"

        found = app.evaluate(
            "(() => { const m = document.body.innerText.match"
            "(/(\\d+)\\s+candidates?\\s+cleared/i); return m ? Number(m[1]) : null; })()"
        )
        if found:
            assert high == found, (
                f"slider maximum {high} does not match the {found} candidates found; "
                "the cap must be the terrain's answer, not a constant"
            )

    def test_reducing_it_removes_sites_from_the_list_and_the_map(self, app) -> None:
        """The list and the map must agree, which is why the analysis is trimmed
        once in the provider rather than filtered in each component."""
        _tab(app, "Candidates")
        _settle(app, 1.5)
        rows_before = int(
            app.evaluate("document.querySelectorAll('.picklist tbody tr').length") or 0
        )
        pins_before = int(app.evaluate("document.querySelectorAll('button.site-rank').length") or 0)
        assert rows_before > 1, f"need more than one candidate to trim, saw {rows_before}"
        assert pins_before == rows_before, (
            f"the sheet shows {pins_before} markers for {rows_before} listed candidates; "
            "they are drawn from one trimmed analysis and must not diverge"
        )

        assert _set_range(app, "#shown-sites", 1), "no sites-shown slider to move"
        _settle(app, 2.0)

        rows_after = int(
            app.evaluate("document.querySelectorAll('.picklist tbody tr').length") or 0
        )
        pins_after = int(app.evaluate("document.querySelectorAll('button.site-rank').length") or 0)
        assert rows_after == 1, f"expected one row after trimming to 1, got {rows_after}"
        assert pins_after == 1, f"expected one marker after trimming to 1, got {pins_after}"

    def test_raising_it_again_restores_every_site(self, app) -> None:
        """The filter is a view, not a destructive edit — the sites are still held."""
        _tab(app, "Candidates")
        _settle(app, 1.5)
        total = int(app.evaluate("(() => document.querySelector('#shown-sites')?.max ?? 0)()") or 0)
        assert total > 1
        assert _set_range(app, "#shown-sites", total)
        _settle(app, 2.0)
        assert (
            int(app.evaluate("document.querySelectorAll('.picklist tbody tr').length") or 0)
            == total
        )

    def test_it_never_hides_the_recommended_site(self, app) -> None:
        """At the floor of 1 the top-ranked site must still be on screen: the
        whole answer is that site."""
        _tab(app, "Proposal")
        _settle(app, 1.5)
        assert "SITE 1" in app.text.upper()
        assert "m³" in app.text, "the recommended pond must still be reported"


class TestTheRasterLayersRender:
    """Slope and shaded relief are COGs served by TiTiler, and the tile URL carries
    the COG's path — which has to be TiTiler's path, not the API's.

    When the API runs on the host those differ, so every tile answered HTTP 500
    "No such file or directory" while the API reported success and the layers were
    silently blank. `TILER_STORE_PATH` translates the prefix.
    """

    def test_the_toggles_are_live_not_greyed(self, app) -> None:
        state = app.evaluate(
            "(() => {"
            " const rows = [...document.querySelectorAll('.legendbox label')];"
            " const row = rows.find(r => /shaded relief/i.test(r.textContent));"
            " if (!row) return 'missing';"
            " const box = row.querySelector('input[type=checkbox]');"
            " return box ? (box.disabled ? 'disabled' : 'enabled') : 'no-box'; })()"
        )
        if state == "disabled":
            pytest.skip("no tile server on this run; the layer is correctly greyed out")
        assert state == "enabled", f"shaded relief toggle is {state}"

    def test_no_tile_request_failed(self, app) -> None:
        """The failure this guards was invisible: the layer simply did not draw.

        Chrome logs a failed subresource as a console error, so a 500 from every
        tile shows up here even though the page keeps working.
        """
        offenders = [
            line for line in app.console_errors() if "/tiles/" in line or "cog/tiles" in line
        ]
        assert (
            not offenders
        ), "tile requests failed — the raster layers are blank:\n  " + "\n  ".join(offenders[:5])
