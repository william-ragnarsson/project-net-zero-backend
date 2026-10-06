"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from netzero import __version__, paths
from netzero.api import routes_artifacts, routes_events, routes_meta, routes_runs
from netzero.api.schemas import ApiError
from netzero.api.static import mount_spa
from netzero.config import Settings, get_settings
from netzero.pipeline.orchestrator import RunManager, RunRejected

logger = logging.getLogger("netzero")

STATUS = {
    "bad_request": 400,
    "invalid_url": 400,
    "not_found": 404,
    "run_active": 409,
    "bad_state": 409,
    "no_api_key": 412,
    "not_ready": 412,
}


def _error(status: int, err: ApiError) -> JSONResponse:
    return JSONResponse(err.model_dump(mode="json"), status_code=status)


async def run_rejected_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RunRejected)
    return _error(
        STATUS.get(exc.code, 400),
        ApiError(detail=exc.detail, code=exc.code, active_run=exc.active_run),  # type: ignore[arg-type]
    )


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    parts = []
    for e in exc.errors()[:5]:
        loc = ".".join(str(x) for x in e.get("loc", ())[1:])
        parts.append(f"{loc}: {e.get('msg', 'invalid')}" if loc else str(e.get("msg", "invalid")))
    return _error(400, ApiError(detail="; ".join(parts) or "invalid request", code="bad_request"))


def create_app(settings: Settings | None = None, *, manager: RunManager | None = None) -> FastAPI:
    """``manager`` is for tests; by default the lifespan creates one from ``settings``."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        m = manager or RunManager(settings)
        m.start()
        app.state.manager = m
        try:
            yield
        finally:
            # an active run ends `interrupted`; open SSE streams then close
            await m.shutdown()

    app = FastAPI(title="net-zero", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.manager = None
    app.add_exception_handler(RunRejected, run_rejected_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.include_router(routes_meta.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_events.router)
    app.include_router(routes_artifacts.router)
    mount_spa(app, paths.WEB_DIST)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory`` (used by ``serve --reload``)."""
    return create_app()
