"""Run outputs: the ``.patch``, the optimized repo ``.zip`` and per-function diffs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from netzero.api.routes_runs import ERRORS, get_manager
from netzero.events import RunDetail
from netzero.pipeline.orchestrator import RunManager, RunRejected

router = APIRouter(prefix="/api/runs/{run_id}/artifacts", tags=["artifacts"])


def _detail(run_id: str, manager: RunManager) -> RunDetail:
    detail = manager.detail(run_id)
    if detail is None:
        raise RunRejected("not_found", f"no run {run_id}")
    return detail


def _file(path: Path, what: str) -> Path:
    if not path.is_file():
        raise RunRejected("not_found", f"no {what} for this run (yet)")
    return path


@router.get("/patch", response_class=FileResponse, responses={404: ERRORS[404]})
async def patch(run_id: str, manager: RunManager = Depends(get_manager)) -> FileResponse:
    """``git diff`` from the cloned commit to the optimized trunk."""
    _detail(run_id, manager)
    path = _file(manager.store.paths(run_id).out / f"{run_id}.patch", "patch")
    return FileResponse(path, media_type="text/x-diff", filename=f"netzero-{run_id}.patch")


@router.get("/zip", response_class=FileResponse, responses={404: ERRORS[404]})
async def zip_archive(run_id: str, manager: RunManager = Depends(get_manager)) -> FileResponse:
    """The optimized repository, plus ``netzero-report.json`` and the generated tests."""
    _detail(run_id, manager)
    path = _file(manager.store.paths(run_id).out / f"{run_id}.zip", "zip")
    return FileResponse(path, media_type="application/zip", filename=f"netzero-{run_id}.zip")


@router.get(
    "/functions/{function_id}/diff", response_class=FileResponse, responses={404: ERRORS[404]}
)
async def function_diff(
    run_id: str, function_id: str, manager: RunManager = Depends(get_manager)
) -> FileResponse:
    """The merge commit of one accepted function."""
    detail = _detail(run_id, manager)
    if not any(f.function_id == function_id for f in detail.functions):
        raise RunRejected("not_found", f"{function_id} was not optimized in this run")
    path = _file(manager.store.paths(run_id).fn(function_id) / "merge.diff", "diff")
    return FileResponse(path, media_type="text/x-diff", headers={"Cache-Control": "no-store"})
