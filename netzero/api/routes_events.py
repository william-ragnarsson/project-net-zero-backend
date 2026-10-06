"""``GET /api/runs/{id}/events``: the run's events over SSE.

* The first frame sets ``retry: 2000``.
* The stream starts after ``max(Last-Event-ID, ?after)``: it replays the
  persisted log, then follows the live bus. Every event carries ``id: seq``.
* ``event: heartbeat`` frames (no id) are sent when nothing happened for
  ``sse_heartbeat_s``.
* The stream ends after the terminal event. A slow client whose queue
  overflowed is disconnected and resumes from its last id.
* 404 for an unknown run; 204 (EventSource stops reconnecting) when the run
  has ended and the client has everything.
* A run driven by another process (the CLI) is followed by tailing its file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from netzero.api.routes_runs import ERRORS, get_manager
from netzero.events import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_STATES,
    Heartbeat,
    RunState,
    line_seq,
    line_type,
)
from netzero.pipeline.bus import now_ms
from netzero.pipeline.follow import follow
from netzero.pipeline.orchestrator import RunManager, RunRejected
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import read_complete_lines

router = APIRouter(prefix="/api/runs", tags=["events"])

RETRY_MS = 2000
TAIL_INTERVAL_S = 0.25  # polling a file another process writes
OWNER_CHECK_S = 2.0  # how often a tailed run's owner is checked


@dataclass
class StreamStart:
    run_id: str
    after: int
    manager: RunManager


def _seq_param(value: str | None) -> int:
    try:
        return max(0, int(value)) if value is not None else 0
    except ValueError:
        return 0


def stream_start(
    run_id: str,
    after: str | None = Query(
        None, description="Resume after this seq (EventSource uses Last-Event-ID)."
    ),
    last_event_id: str | None = Header(None),
    manager: RunManager = Depends(get_manager),
) -> StreamStart:
    """Resolved before the stream starts, so it can still answer 404 / 204."""
    start = max(_seq_param(last_event_id), _seq_param(after))
    if manager.live(run_id) is None:
        detail = manager.detail(run_id)  # recovers an orphaned run first
        if detail is None:
            raise RunRejected("not_found", f"no run {run_id}")
        if detail.state in TERMINAL_STATES and start >= detail.last_seq:
            raise HTTPException(status_code=204)
    return StreamStart(run_id=run_id, after=start, manager=manager)


@router.get(
    "/{run_id}/events",
    response_class=EventSourceResponse,
    responses={
        204: {"description": "the run has ended and the client is caught up"},
        404: ERRORS[404],
    },
)
async def stream_events(
    request: Request, start: StreamStart = Depends(stream_start)
) -> AsyncIterator[ServerSentEvent]:
    """Server-sent events: ``RunEvent`` frames (``id: seq``) and ``heartbeat`` frames."""
    yield ServerSentEvent(comment="netzero", retry=RETRY_MS)
    ctx = start.manager.live(start.run_id)
    frames = (
        _follow_live(start.manager, ctx, start.after)
        if ctx
        else _tail_file(start.manager, start.run_id, start.after)
    )
    async for frame in frames:
        yield frame


def _event(seq: int, line: str) -> ServerSentEvent:
    return ServerSentEvent(raw_data=line, id=str(seq))


def _heartbeat(run_id: str, last_seq: int, state: RunState) -> ServerSentEvent:
    hb = Heartbeat(run_id=run_id, last_seq=last_seq, state=state, server_ts=now_ms())
    return ServerSentEvent(event="heartbeat", raw_data=hb.model_dump_json())


async def _follow_live(
    manager: RunManager, ctx: RunContext, after: int
) -> AsyncIterator[ServerSentEvent]:
    """A run this process drives: replay the log, then follow the bus."""
    async for item in follow(ctx, after, idle_s=manager.settings.sse_heartbeat_s):
        if item is None:
            yield _heartbeat(ctx.run_id, ctx.last_seq, ctx.state)
        else:
            yield _event(*item)


async def _tail_file(
    manager: RunManager, run_id: str, after: int
) -> AsyncIterator[ServerSentEvent]:
    """Follow a run this process does not drive by polling its ``events.jsonl``."""
    store = manager.store
    path = store.paths(run_id).events
    offset, last = 0, after
    idle_s = owner_s = 0.0
    while True:
        lines, offset = read_complete_lines(path, offset)
        for line in lines:
            seq = line_seq(line)
            if seq <= last:
                continue
            yield _event(seq, line)
            last = seq
            idle_s = 0.0
            if line_type(line) in TERMINAL_EVENT_TYPES:
                return
        await asyncio.sleep(TAIL_INTERVAL_S)
        idle_s += TAIL_INTERVAL_S
        owner_s += TAIL_INTERVAL_S
        if idle_s >= manager.settings.sse_heartbeat_s:
            idle_s = 0.0
            summary = store.read_summary(run_id)
            yield _heartbeat(run_id, last, summary.state if summary else "created")
        if owner_s >= OWNER_CHECK_S:
            owner_s = 0.0
            if not store.is_owned(run_id):
                # the owner finished or died: recover (no-op if it ended
                # cleanly), send what is left and end the stream
                manager.recover(run_id)
                lines, offset = read_complete_lines(path, offset)
                for line in lines:
                    seq = line_seq(line)
                    if seq > last:
                        yield _event(seq, line)
                        last = seq
                return
