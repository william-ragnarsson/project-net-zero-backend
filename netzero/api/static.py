"""Serve the built SPA (``web/dist``) with an ``index.html`` fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>net-zero</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0a;color:#e5e5e5;
       font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
  main{max-width:34rem;padding:2rem}
  b{color:#00ff88;font-weight:500}
  code{color:#00ff88}
</style></head>
<body><main>
  <p><b>net-zero</b> api is running.</p>
  <p>the web ui is not built yet. run <code>make run</code> (or <code>make dev</code> for hot reload).</p>
  <p>api: <code>/api/health</code>, <code>/api/capabilities</code>, <code>/api/runs</code></p>
</main></body></html>
"""


class SPAStaticFiles(StaticFiles):
    """Static files; unknown extension-less paths fall back to ``index.html``."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/") or "." in Path(path).name:
                raise
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.media_type == "text/html" or path in ("", ".", "index.html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Mount after the API routers so ``/api/*`` always wins."""
    if (dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def placeholder(path: str) -> Response:
        if path.startswith("api/"):
            return Response(status_code=404)
        if path == "favicon.ico":
            return Response(status_code=204)
        return HTMLResponse(PLACEHOLDER, headers={"Cache-Control": "no-cache"})
