"""Run commands and records: create, list, read, select, cancel, raw log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from netzero.api.schemas import ApiError, CreateRunRequest, SelectRequest
from netzero.events import RunDetail, RunSummary
from netzero.pipeline.orchestrator import RunManager, RunRejected
from netzero.pipeline.store import read_complete_lines

router = APIRouter(prefix="/api/runs", tags=["runs"])

ERRORS = {
    400: {"model": ApiError, "description": "bad request or invalid GitHub URL"},
    404: {"model": ApiError, "description": "unknown run"},
    409: {
        "model": ApiError,
        "description": "a run is already active, or the run is in the wrong state",
    },
    412: {"model": ApiError, "description": "missing API key, or the demo repo is not installed"},
}


def get_manager(request: Request) -> RunManager:
    manager: RunManager | None = getattr(request.app.state, "manager", None)
    if manager is None:
        raise RunRejected("not_ready", "the run manager is not running")
    return manager


@router.post("", status_code=201, responses=ERRORS)
async def create_run(
    body: CreateRunRequest, manager: RunManager = Depends(get_manager)
) -> RunSummary:
    """Start a run on a GitHub repo (``github_url``) or the bundled demo (``demo``)."""
    return await manager.create(body)


@router.get("")
async def list_runs(manager: RunManager = Depends(get_manager)) -> list[RunSummary]:
    return manager.summaries()


@router.get("/{run_id}", responses={404: ERRORS[404]})
async def get_run(run_id: str, manager: RunManager = Depends(get_manager)) -> RunDetail:
    detail = manager.detail(run_id)
    if detail is None:
        raise RunRejected("not_found", f"no run {run_id}")
    return detail


@router.post("/{run_id}/select", responses=ERRORS)
async def select_functions(
    run_id: str, body: SelectRequest, manager: RunManager = Depends(get_manager)
) -> RunSummary:
    """Confirm which functions to optimize (only while ``awaiting_selection``)."""
    return manager.select(run_id, body.function_ids)


@router.post("/{run_id}/cancel", responses={404: ERRORS[404], 409: ERRORS[409]})
async def cancel_run(run_id: str, manager: RunManager = Depends(get_manager)) -> RunSummary:
    """Cancel the run; returns once it has ended (or after ~5 s). Idempotent."""
    return await manager.cancel(run_id)


@router.get(
    "/{run_id}/events.jsonl",
    response_class=Response,
    responses={200: {"content": {"application/x-ndjson": {}}}, 404: ERRORS[404]},
)
async def events_jsonl(run_id: str, manager: RunManager = Depends(get_manager)) -> Response:
    """The persisted event log so far (complete lines only)."""
    if manager.detail(run_id) is None:
        raise RunRejected("not_found", f"no run {run_id}")
    # Built in memory, not a FileResponse: a live log grows while it is sent.
    lines, _ = read_complete_lines(manager.store.paths(run_id).events)
    body = "".join(line + "\n" for line in lines)
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
