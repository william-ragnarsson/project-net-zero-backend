"""``/api/health`` and ``/api/capabilities``."""

from __future__ import annotations

import platform
import shutil
import sys

from fastapi import APIRouter, Request

from netzero import __version__, paths
from netzero.api.schemas import Capabilities, Health, Tools
from netzero.bench.probe import load_cached
from netzero.config import Settings
from netzero.events import RunMode, RunSummary

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
async def health() -> Health:
    return Health(ok=True, version=__version__)


@router.get("/capabilities")
async def capabilities(request: Request) -> Capabilities:
    settings: Settings = request.app.state.settings
    manager = getattr(request.app.state, "manager", None)
    active: RunSummary | None = manager.active_summary() if manager is not None else None
    modes: list[RunMode] = ["demo"]
    if settings.has_api_key or settings.fake_pipeline:
        modes.insert(0, "live")
    return Capabilities(
        version=__version__,
        has_api_key=settings.has_api_key,
        modes=modes,
        active_run=active,
        platform=f"{platform.system().lower()}-{platform.machine()}",
        python=sys.version.split()[0],
        tools=Tools(git=shutil.which("git") is not None, uv=shutil.which("uv") is not None),
        power=load_cached(),
        models=settings.stages.models(),
        demo_available=paths.DEMO_REPO.is_dir() or settings.fake_pipeline,
    )
