"""HTTP from the test process to the site under test, retried as a browser would.

A browser opens another connection when one does not open; urllib does not. From
a laptop on campus Wi-Fi about half of all new connections to the lab address
hang, so a plain `urlopen` failed tests with nothing wrong in the app. Only a
failure to connect is retried: an HTTP error is an answer, and fails the test.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any


def urlopen(url: str, *, timeout: float = 20.0, tries: int = 4) -> Any:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(1 + attempt)
    assert last is not None
    raise last
