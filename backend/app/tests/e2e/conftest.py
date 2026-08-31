"""Guards for the browser end-to-end test.

The test drives a real Chrome against the running compose stack, so it needs
things a unit test never does: a browser on the host, the frontend and API
answering on their ports, and a contour map to upload. Any of those missing is
a skip, not a failure -- `pytest` on a bare checkout must stay green.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FRONTEND_URL = "http://localhost:8080"
REPO_ROOT = Path(__file__).resolve().parents[4]
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return bool(200 <= response.status < 400)
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def chrome_binary() -> str:
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    pytest.skip(f"no Chrome on PATH (looked for {', '.join(CHROME_NAMES)})")


@pytest.fixture(scope="session")
def frontend_url() -> str:
    if not _reachable(FRONTEND_URL):
        pytest.skip(f"frontend not serving at {FRONTEND_URL}; run `docker compose up -d`")
    if not _reachable(f"{FRONTEND_URL}/api/v1/health"):
        pytest.skip("frontend is up but its /api proxy is not reaching the API")
    return FRONTEND_URL


@pytest.fixture(scope="session")
def sample_contour_map() -> Path:
    path = REPO_ROOT / "contours_1m.kml"
    if not path.exists():
        pytest.skip(f"no sample contour map at {path}")
    return path


@pytest.fixture(scope="session")
def cdp():
    """The websocket client the CDP driver needs, or a skip."""
    return pytest.importorskip("websocket", reason="pip install -r requirements-dev.txt")
