"""A minimal Chrome DevTools Protocol client.

Just enough to drive one page: navigate, poll an expression until it is true,
put a file into an ``<input type=file>``, and read back the rendered text. It
exists so the end-to-end test can use the browser a user would use, without
pulling Playwright or Puppeteer -- and the several hundred megabytes of
downloaded browser that come with them -- into a project whose real work is
hydrology.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from types import TracebackType


def _free_port() -> int:
    """A port nothing is listening on, chosen per browser.

    This was a module constant of 9222. With a fixed port a leftover browser --
    and Chrome leaked several before the process-group teardown below -- keeps
    ownership of it, the new instance fails to bind, and `_await_page` then
    happily attaches to the *stale* browser sitting on about:blank. The symptom
    is the new page never showing the app, reported as "the analysis never
    completed" in whichever suite ran last. Nothing about it points at the port.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ChromeError(RuntimeError):
    """Chrome refused a command or never came up."""


class Chrome:
    """A headless Chrome, controlled over CDP, cleaned up on exit."""

    def __init__(self, binary: str, *, window: str = "1400,900") -> None:
        self._binary = binary
        self._window = window
        self._profile = ""
        self._port = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._ws = None
        self._seq = 0
        self.events: list[dict] = []

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> Chrome:
        import websocket

        self._profile = tempfile.mkdtemp(prefix="contour-e2e-")
        self._port = _free_port()
        self._proc = subprocess.Popen(
            [
                self._binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                # MapLibre needs WebGL; software rasterisation is enough to
                # prove the canvas initialises and paints.
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
                f"--remote-debugging-port={self._port}",
                # Chrome rejects CDP websockets from unlisted origins.
                "--remote-allow-origins=*",
                f"--user-data-dir={self._profile}",
                f"--window-size={self._window}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own process group, so the whole browser can be reaped on exit.
            # Chrome forks a zygote, a GPU process and one renderer per tab, and
            # terminating only the launcher leaves those behind: after four
            # fixtures in one pytest process the machine was carrying a dozen
            # orphaned renderers at ~400 MB each, and the last browser to start
            # could not paint. It surfaced as "the analysis never completed" in
            # whichever suite happened to run last, which is a miserable thing to
            # chase in the app.
            start_new_session=True,
        )
        page = self._await_page()
        self._ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=180)
        for domain in ("Page", "Runtime", "Log", "DOM"):
            self.send(f"{domain}.enable")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._ws is not None:
            self._ws.close()
        if self._proc is not None:
            self._kill_group(self._proc)
        shutil.rmtree(self._profile, ignore_errors=True)

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """Reap the browser and every process it forked.

        `start_new_session=True` put them in their own group, so one signal to the
        group takes the lot. Falls back to signalling the launcher alone if the
        group is already gone.
        """

        def signal_group(sig: int) -> None:
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(sig)

        signal_group(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            signal_group(signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    def _await_page(self, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                targets = json.load(
                    urllib.request.urlopen(f"http://127.0.0.1:{self._port}/json", timeout=2)
                )
            except OSError:
                time.sleep(0.5)
                continue
            for target in targets:
                if target.get("type") == "page":
                    return target
            time.sleep(0.5)
        raise ChromeError("Chrome never exposed a CDP page target")

    # -- protocol -----------------------------------------------------------
    def send(self, method: str, **params: object) -> dict:
        """Issue one command, buffering the events that arrive before its reply."""
        assert self._ws is not None, "use Chrome as a context manager"
        self._seq += 1
        self._ws.send(json.dumps({"id": self._seq, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != self._seq:
                self.events.append(message)
                continue
            if "error" in message:
                raise ChromeError(f"{method}: {message['error']}")
            return message.get("result", {})

    # -- page actions -------------------------------------------------------
    def navigate(self, url: str) -> None:
        self.send("Page.navigate", url=url)

    def evaluate(self, expression: str) -> object:
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True)
        return result.get("result", {}).get("value")

    def wait_until(self, expression: str, *, timeout: float, poll: float = 0.5) -> bool:
        """Poll a JavaScript expression until it is truthy or the clock runs out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.evaluate(expression):
                return True
            time.sleep(poll)
        return False

    def attach_file(self, selector: str, path: str) -> None:
        document = self.send("DOM.getDocument")["root"]["nodeId"]
        node = self.send("DOM.querySelector", nodeId=document, selector=selector)["nodeId"]
        if not node:
            raise ChromeError(f"no element matched {selector!r}")
        self.send("DOM.setFileInputFiles", files=[path], nodeId=node)

    def click_button_matching(self, pattern: str) -> bool:
        """Click the first enabled button whose text matches, case-insensitively."""
        clicked = self.evaluate(
            "(() => { const b = [...document.querySelectorAll('button')]"
            f".find(x => /{pattern}/i.test(x.textContent) && !x.disabled);"
            " if (b) b.click(); return !!b; })()"
        )
        return bool(clicked)

    @property
    def text(self) -> str:
        return str(self.evaluate("document.body.innerText") or "")

    def console_errors(self) -> list[str]:
        return [
            event["params"]["entry"]["text"]
            for event in self.events
            if event.get("method") == "Log.entryAdded"
            and event["params"]["entry"].get("level") == "error"
        ]

    def screenshot(self, path: str) -> None:
        import base64

        data = self.send("Page.captureScreenshot", format="png")["data"]
        Path(path).write_bytes(base64.b64decode(data))
