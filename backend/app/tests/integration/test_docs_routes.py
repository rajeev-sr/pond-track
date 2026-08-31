"""Every URL FastAPI publishes must survive both ways of serving the frontend.

There are two reverse proxies in this repo -- `frontend/nginx.conf` for the
container and the `server.proxy` block in `frontend/vite.config.ts` for
`npm run dev` -- and both put the API behind the same origin as the app. A route
missing from either one does **not** 404: it falls through to the SPA and returns
`index.html` with HTTP 200.

That is how `/openapi.json` broke. `/docs` was proxied and `/openapi.json` was
not, so Swagger UI loaded, fetched its spec, was handed HTML, and reported "the
provided definition does not specify a valid version field" -- an error that
points at the spec and not at the missing proxy rule. A 200 of the wrong content
type is far harder to diagnose than a 404, so the two lists are compared here
rather than trusted to stay in step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
NGINX = REPO / "frontend" / "nginx.conf"
VITE = REPO / "frontend" / "vite.config.ts"

#: The documentation URLs `create_app()` sets. Spelled out rather than read from
#: the app so that renaming one without updating both proxies fails here.
DOC_ROUTES = ("/docs", "/redoc", "/openapi.json")


def nginx_locations() -> set[str]:
    if not NGINX.exists():
        pytest.skip(f"no nginx.conf at {NGINX}")
    found = set()
    for match in re.finditer(r"^\s*location\s+([^\s{]+)", NGINX.read_text(), re.MULTILINE):
        prefix = match.group(1)
        if prefix.startswith("@"):  # a named location, not a URL
            continue
        found.add(prefix.rstrip("/") or "/")
    return found


def vite_proxy_keys() -> set[str]:
    if not VITE.exists():
        pytest.skip(f"no vite.config.ts at {VITE}")
    text = VITE.read_text()
    block = text.split("proxy:", 1)
    if len(block) < 2:
        pytest.skip("the dev server declares no proxy")
    # Quoted keys at the head of an entry: `"/openapi.json": { ... }`
    return {key.rstrip("/") or "/" for key in re.findall(r'"(/[^"]*)"\s*:\s*\{', block[1])}


class TestTheDocsAreReachableThroughBothProxies:
    @pytest.mark.parametrize("route", DOC_ROUTES)
    def test_nginx_proxies_it(self, route: str) -> None:
        assert route.rstrip("/") in nginx_locations(), (
            f"{route} is not proxied by nginx; it will fall through to the SPA "
            "and return index.html with HTTP 200"
        )

    @pytest.mark.parametrize("route", DOC_ROUTES)
    def test_the_dev_server_proxies_it(self, route: str) -> None:
        assert route.rstrip("/") in vite_proxy_keys(), (
            f"{route} is not proxied by the Vite dev server; on :5173 it will "
            "return index.html with HTTP 200 instead of the API's response"
        )

    def test_the_dev_server_covers_everything_nginx_does(self) -> None:
        """The dev server exists to behave like the container, per its own comment.

        `/` is nginx's SPA fallback, which is the dev server's default behaviour
        and needs no entry.
        """
        missing = {p for p in nginx_locations() if p != "/"} - vite_proxy_keys()
        assert not missing, (
            f"nginx proxies {sorted(missing)} but the dev server does not; each "
            "one silently returns index.html on :5173"
        )


class TestTheReDocBundleIsPinned:
    """FastAPI's default `redoc@next` now resolves to a 3.0.0-rc build that does
    not publish `bundles/redoc.standalone.js`. The script 404s, nothing is
    logged, and /redoc renders as a blank white page.
    """

    def test_the_url_names_an_exact_version(self) -> None:
        from app.main import REDOC_JS_URL

        assert "@next" not in REDOC_JS_URL, "a moving tag is what broke this once"
        assert re.search(
            r"redoc@\d+\.\d+\.\d+/", REDOC_JS_URL
        ), f"pin an exact ReDoc version, got {REDOC_JS_URL!r}"

    def test_the_route_serves_that_url(self, client) -> None:
        from app.main import REDOC_JS_URL

        response = client.get("/redoc")
        assert response.status_code == 200
        assert REDOC_JS_URL in response.text

    def test_swagger_still_has_its_own_page(self, client) -> None:
        assert client.get("/docs").status_code == 200

    def test_the_spec_is_served_as_json(self, client) -> None:
        """The content type is the whole failure: Swagger UI needs JSON, and HTML
        with HTTP 200 is what it was given."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["openapi"].startswith("3.")
