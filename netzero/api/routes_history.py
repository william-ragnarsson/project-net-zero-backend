"""Run history from Postgres: projects, runs, functions over time, and the stored log.

Routes are plain ``def`` so FastAPI runs the blocking queries in its threadpool.
All of them except ``/status`` answer 503 ``no_database`` while history is
off (no ``NETZERO_DATABASE_URL``) or the database can't be reached.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from netzero.api.schemas import (
    ActivityPage,
    ApiError,
    FunctionDiff,
    HistoryStatus,
    ProjectDetail,
    ProjectSummary,
)
from netzero.history import queries
from netzero.history.service import History, HistoryUnavailable
from netzero.pipeline.orchestrator import RunRejected

router = APIRouter(prefix="/api/history", tags=["history"])

OFF = "history is off: set NETZERO_DATABASE_URL to a Postgres database (see `make db`)"

ERRORS = {
    404: {"model": ApiError, "description": "unknown project, run or function"},
    503: {"model": ApiError, "description": "history is off or the database is unreachable"},
}


def get_history(request: Request) -> History:
    history: History | None = getattr(request.app.state, "history", None)
    if history is None:
        raise HistoryUnavailable(OFF)
    return history


@router.get("/status")
def status(request: Request) -> HistoryStatus:
    """Whether history is on, and what the database holds. Always 200."""
    history: History | None = getattr(request.app.state, "history", None)
    if history is None:
        return HistoryStatus(enabled=False, ok=False, detail=OFF)
    return history.status()


@router.get("/projects", responses={503: ERRORS[503]})
def projects(history: History = Depends(get_history)) -> list[ProjectSummary]:
    """Every repository with a stored run, most recently run first."""
    return history.read(queries.projects)


@router.get("/projects/{project_id}", responses=ERRORS)
def project(project_id: str, history: History = Depends(get_history)) -> ProjectDetail:
    detail = history.read(lambda conn: queries.project_detail(conn, project_id))
    if detail is None:
        raise RunRejected("not_found", f"no project {project_id}")
    return detail


@router.get("/activity", responses={400: {"model": ApiError}, 503: ERRORS[503]})
def activity(
    project: str | None = None,
    before: str | None = Query(None, description="the previous page's `next`"),
    limit: int = Query(30, ge=1, le=100),
    history: History = Depends(get_history),
) -> ActivityPage:
    """Notable events across all runs, newest first."""
    try:
        cursor = queries.parse_cursor(before)
    except ValueError as exc:
        raise RunRejected("bad_request", str(exc)) from None
    return history.read(
        lambda conn: queries.activity(conn, project_id=project, before=cursor, limit=limit)
    )


@router.get(
    "/runs/{run_id}/events.jsonl",
    response_class=Response,
    responses={200: {"content": {"application/x-ndjson": {}}}, **ERRORS},
)
def run_events(run_id: str, history: History = Depends(get_history)) -> Response:
    """A run's log as stored in Postgres, byte for byte what the run wrote."""
    lines = history.read(lambda conn: queries.run_lines(conn, run_id))
    if not lines:
        raise RunRejected("not_found", f"no stored run {run_id}")
    return Response(
        content="".join(line + "\n" for line in lines),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/runs/{run_id}/diff", responses=ERRORS)
def function_diff(
    run_id: str, function_id: str, history: History = Depends(get_history)
) -> FunctionDiff:
    """The diff a run merged (or proposed) for one function."""
    diff = history.read(lambda conn: queries.function_diff(conn, run_id, function_id))
    if diff is None:
        raise RunRejected("not_found", f"run {run_id} has no result for {function_id}")
    return diff
