"""A Run survives a dropped connection instead of leaving the page frozen.

From the campus Wi-Fi about half of all new connections to the lab address hang.
The page used to send the job start once, with no deadline, and show nothing at
all while the browser waited ~90 s on a dead connection: the gateway's log held
no trace of the Run. The start request is failed here the way a dead connection
ends, and the page must say so, send it again with the same key, and finish.
"""

from __future__ import annotations

import time

import pytest

from app.tests.e2e.cdp import Chrome

MOUNT_TIMEOUT_S = 60.0
ANALYSIS_TIMEOUT_S = 180.0


def paused(page: Chrome, timeout: float) -> dict:
    """The next request held by the Fetch domain, read from the event buffer."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.evaluate("0")  # reading a reply drains the events queued before it
        for event in page.events:
            if event.get("method") == "Fetch.requestPaused":
                page.events.remove(event)
                return event["params"]
        time.sleep(0.2)
    raise AssertionError("the job start was never sent")


@pytest.fixture(scope="module")
def page(chrome_binary, frontend_url, cdp):  # type: ignore[no-untyped-def]
    with Chrome(chrome_binary) as chrome:
        chrome.navigate(f"{frontend_url}/workspace")
        assert chrome.wait_until(
            "!!document.querySelector('.drawing .map[data-ready=true]')", timeout=MOUNT_TIMEOUT_S
        ), "the map never loaded"
        yield chrome


def test_a_dropped_start_is_said_retried_and_finished(page: Chrome) -> None:
    assert page.click_button_matching("^draw area on map$")
    assert page.click_button_matching("^use the sample area$")
    page.send(
        "Fetch.enable",
        patterns=[{"urlPattern": "*/api/v1/analysis/area*", "requestStage": "Request"}],
    )
    assert page.click_button_matching("^run$"), "Run was not enabled"

    first = paused(page, timeout=10)
    page.send("Fetch.failRequest", requestId=first["requestId"], errorReason="ConnectionFailed")
    assert page.wait_until(
        "/connection stalled/i.test(document.body.innerText)", timeout=10
    ), "the page did not say it was sending the request again"

    second = paused(page, timeout=15)
    key = {k.lower(): v for k, v in first["request"]["headers"].items()}.get("idempotency-key")
    again = {k.lower(): v for k, v in second["request"]["headers"].items()}.get("idempotency-key")
    assert key and key == again, "a retried start must carry the first attempt's key"
    page.send("Fetch.continueRequest", requestId=second["requestId"])
    page.send("Fetch.disable")

    assert page.wait_until(
        "document.querySelectorAll('.site-rank').length > 0", timeout=ANALYSIS_TIMEOUT_S
    ), "the run never finished after the retry"
    assert not page.wait_until(
        "/connection stalled|Contacting the server/i.test(document.body.innerText)", timeout=1
    ), "the connection note outlived the run"


def test_before_the_server_answers_the_page_says_it_is_contacting_it(page: Chrome) -> None:
    page.send(
        "Fetch.enable",
        patterns=[{"urlPattern": "*/api/v1/analysis/area*", "requestStage": "Request"}],
    )
    assert page.click_button_matching("^run$"), "Run was not enabled"
    held = paused(page, timeout=10)
    try:
        assert page.wait_until(
            "/Contacting the server/.test(document.body.innerText)", timeout=5
        ), "a pressed Run with nothing on screen is the bug this replaces"
    finally:
        page.send("Fetch.continueRequest", requestId=held["requestId"])
        page.send("Fetch.disable")
    assert page.wait_until(
        "document.querySelectorAll('.site-rank').length > 0", timeout=ANALYSIS_TIMEOUT_S
    )
