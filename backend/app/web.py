"""The built web UI, served by the API process itself.

On the lab systems every process costs a share of 512 MB, so each one runs a
single process type -- the API -- and it serves the UI as well: no Node, no
separate web server, and a gateway in front can proxy every path to any
instance without knowing which paths are pages and which are API calls.

Off unless `FRONTEND_DIST` names a built `frontend/dist`. The dev server and the
nginx container are unchanged, and so is every test that expects a 404 for an
unknown path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.errors import NotFoundProblem

#: Paths the SPA must never answer. An unknown `/api/...` path returning the
#: app's HTML with a 200 is the failure `vite.config.ts` warns about: a client
#: expecting JSON gets a page, and reports a parse error instead of a 404.
RESERVED = ("api/", "docs", "redoc", "openapi.json", "ws/", "tiles/")

#: Vite names built assets by content hash, so a file under /assets never
#: changes: cache it for a year. index.html must always be revalidated, or a
#: redeploy is invisible until the browser's cache expires.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve `dist` at `/`, with client-side routes falling back to index.html."""
    root = dist.resolve()
    index = root / "index.html"
    assets = root / "assets"

    if assets.is_dir():
        app.mount("/assets", _ImmutableStatic(directory=assets), name="assets")

    async def spa(request: Request, path: str = "") -> Response:
        if path.startswith(RESERVED) or path in {p.rstrip("/") for p in RESERVED}:
            raise NotFoundProblem(detail=f"no route {request.url.path!r}")
        if path:
            candidate = (root / path).resolve()
            # Inside dist only: `..` segments must not walk out of it.
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate, headers={"Cache-Control": REVALIDATE})
        return FileResponse(index, headers={"Cache-Control": REVALIDATE})

    # Registered last and matched last: every API route above wins.
    app.add_api_route("/", spa, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/{path:path}", spa, methods=["GET", "HEAD"], include_in_schema=False)


class _ImmutableStatic(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE
        return response
