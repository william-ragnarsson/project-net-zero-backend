"""``RunContext``: steps, function scopes, transitions, selection, logs and teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from netzero.errors import CloneError
from netzero.events import (
    DATA_CLASSES,
    TERMINAL_STATES,
    ErrorInfo,
    EventBase,
    RunState,
)
from netzero.pipeline.grammar import check_grammar
from netzero.pipeline.run import TRANSITIONS, RunContext, can_transition
from tests.conftest import FakeClock
from tests.unit.support import FID, function_info, parse_all, read_lines

CLONE = {"url": "https://example.invalid/r.git"}
WRITE = {"kind": "write", "strategy_hint": "vectorize"}
CLONE_OK = {"commit_sha": "abc", "n_files": 1, "n_py_files": 1, "size_bytes": 10}


def events_of(ctx: RunContext) -> list[EventBase]:
    return parse_all(read_lines(ctx))


def types_of(ctx: RunContext) -> list[str]:
    return [ev.type for ev in events_of(ctx)]  # type: ignore[attr-defined]


def last(ctx: RunContext, type_: str) -> EventBase:
    return [ev for ev in events_of(ctx) if ev.type == type_][-1]  # type: ignore[attr-defined]


async def to_optimizing(ctx: RunContext) -> None:
    ctx.emit_created()
    for s in ("cloning", "installing", "discovering", "triaging", "awaiting_selection"):
        ctx.transition(s)  # type: ignore[arg-type]
    ctx.emit("run.selection.confirmed", {"function_ids": [FID]})
    ctx.transition("optimizing")


async def wait_until(cond: Callable[[], bool]) -> None:
    for _ in range(100):
        if cond():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


# -- step ----------------------------------------------------------------------------


async def test_step_emits_started_then_completed(ctx: RunContext, clock: FakeClock) -> None:
    ctx.emit_created()
    async with ctx.step("run.clone.started", CLONE) as st:
        clock.advance(250)
        st.ok(**CLONE_OK)
    started, completed = events_of(ctx)[1:]
    assert (started.type, completed.type) == ("run.clone.started", "run.clone.completed")  # type: ignore[attr-defined]
    assert started.data.url == CLONE["url"]  # type: ignore[attr-defined]
    d = completed.data  # type: ignore[attr-defined]
    assert (d.ok, d.duration_ms, d.error, d.commit_sha) == (True, 250, None, "abc")
    assert completed.ts - started.ts == 250


async def test_step_copies_scope_to_both_events(ctx: RunContext) -> None:
    async with ctx.step(
        "candidate.write.started", WRITE, function_id=FID, candidate_id="B", attempt=2
    ):
        pass
    for ev in events_of(ctx):
        assert (ev.function_id, ev.candidate_id, ev.attempt) == (FID, "B", 2)
    assert not ctx.bus.open_steps()


async def test_step_exception_completes_with_error_and_reraises(
    ctx: RunContext, clock: FakeClock
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with ctx.step("run.clone.started", CLONE) as st:
            st.ok(commit_sha="partial")
            clock.advance(5)
            raise RuntimeError("boom")
    d = last(ctx, "run.clone.completed").data  # type: ignore[attr-defined]
    assert (d.ok, d.duration_ms, d.commit_sha) == (False, 5, "partial")
    assert d.error == ErrorInfo(kind="internal", message="RuntimeError: boom")
    assert types_of(ctx) == ["run.clone.started", "run.clone.completed"]


async def test_step_netzero_error_keeps_its_kind(ctx: RunContext) -> None:
    with pytest.raises(CloneError):
        async with ctx.step("run.clone.started", CLONE):
            raise CloneError("repository not found")
    err = last(ctx, "run.clone.completed").data.error  # type: ignore[attr-defined]
    assert (err.kind, err.message) == ("clone_error", "repository not found")


@pytest.mark.parametrize(
    ("reason", "kind", "message"),
    [
        (None, "cancelled", "cancelled"),
        ("user", "cancelled", "cancelled by user"),
        ("shutdown", "interrupted", "server shut down"),
    ],
)
async def test_step_cancellation(ctx: RunContext, reason, kind: str, message: str) -> None:
    entered = asyncio.Event()

    async def body() -> None:
        async with ctx.step("run.env.started", {"python_request": "3.12"}):
            async with ctx.step("run.clone.started", CLONE):
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await entered.wait()
    ctx.cancel_reason = reason
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    evs = events_of(ctx)
    assert [ev.type for ev in evs] == [  # type: ignore[attr-defined]
        "run.env.started",
        "run.clone.started",
        "run.clone.completed",  # innermost first
        "run.env.completed",
    ]
    for ev in evs[2:]:
        assert ev.data.ok is False  # type: ignore[attr-defined]
        assert ev.data.error == ErrorInfo(kind=kind, message=message)  # type: ignore[attr-defined]
    assert not ctx.bus.open_steps()


async def test_step_fail_variants(ctx: RunContext) -> None:
    async with ctx.step("run.clone.started", CLONE) as st:
        st.fail("network down")
    async with ctx.step("run.clone.started", CLONE) as st:
        st.fail(CloneError("gone"))
    async with ctx.step("run.clone.started", CLONE) as st:
        st.fail(None, commit_sha="abc")  # a domain failure with its result
    done = [ev.data for ev in events_of(ctx) if ev.type == "run.clone.completed"]  # type: ignore[attr-defined]
    assert [d.ok for d in done] == [False, False, False]
    assert done[0].error == ErrorInfo(kind="internal", message="network down")
    assert done[1].error == ErrorInfo(kind="clone_error", message="gone")
    assert (done[2].error, done[2].commit_sha) == (None, "abc")


async def test_step_invalid_fields_still_close_the_step(ctx: RunContext) -> None:
    async with ctx.step("run.clone.started", CLONE) as st:
        st.ok(not_a_field=1)
    async with ctx.step("run.clone.started", CLONE) as st:
        st.fail("own error", n_files="many")
    a, b = [ev.data for ev in events_of(ctx) if ev.type == "run.clone.completed"]  # type: ignore[attr-defined]
    name = DATA_CLASSES["run.clone.completed"].__name__
    assert (a.ok, a.error.kind, a.error.message) == (False, "internal", f"invalid {name} fields")
    assert "not_a_field" in a.error.detail
    assert (b.ok, b.error.kind, b.error.message) == (False, "internal", "own error")
    assert "n_files" in b.error.detail


async def test_step_rejects_non_step_types(ctx: RunContext) -> None:
    with pytest.raises(TypeError, match="use ctx.function"):
        async with ctx.step("function.started"):
            pass
    with pytest.raises(KeyError):
        async with ctx.step("run.created"):
            pass
    assert ctx.last_seq == 0


async def test_step_force_closed_then_raising_emits_one_completion(ctx: RunContext) -> None:
    with pytest.raises(RuntimeError):
        async with ctx.step("run.clone.started", CLONE):
            ctx.bus.close_open_steps(ErrorInfo(kind="cancelled", message="forced"))
            raise RuntimeError("late")
    assert types_of(ctx) == ["run.clone.started", "run.clone.completed"]
    assert last(ctx, "run.clone.completed").data.error.message == "forced"  # type: ignore[attr-defined]


async def test_step_force_closed_then_exiting_normally_emits_one_completion(
    ctx: RunContext,
) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1) as fs:

        async def candidate() -> None:
            async with ctx.step(
                "candidate.write.started", WRITE, function_id=FID, candidate_id="A"
            ):
                await release.wait()

        release = asyncio.Event()
        task = asyncio.create_task(candidate())
        await wait_until(lambda: ctx.bus.is_open("candidate.write.started", FID, "A"))
        fs.complete("failed", reason="gave up")
    # the function scope closed the abandoned step; now the step body finishes
    release.set()
    await task
    lines = read_lines(ctx)
    assert [t for t in types_of(ctx) if t == "candidate.write.completed"] == [
        "candidate.write.completed"
    ]
    assert check_grammar(lines, complete=False) == []


# -- function scope ------------------------------------------------------------------


async def test_function_scope_accepted(ctx: RunContext, clock: FakeClock) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1) as fs:
        clock.advance(40)
        fs.complete("accepted", winner="A", delta_pct=-12.5)
    started, completed = events_of(ctx)[-2:]
    assert started.type == "function.started" and started.function_id == FID  # type: ignore[attr-defined]
    assert (started.data.index, started.data.total, started.data.info.function_id) == (0, 1, FID)  # type: ignore[attr-defined]
    assert completed.type == "function.completed" and completed.function_id == FID  # type: ignore[attr-defined]
    d = completed.data  # type: ignore[attr-defined]
    assert (d.outcome, d.winner, d.delta_pct, d.duration_ms) == ("accepted", "A", -12.5, 40)
    assert ctx.detail.functions[0].outcome == "accepted"
    assert ctx.store.read_detail(ctx.run_id).functions_done == 1  # persisted at once


async def test_function_scope_without_outcome_fails(ctx: RunContext) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1):
        pass
    d = last(ctx, "function.completed").data  # type: ignore[attr-defined]
    assert (d.outcome, d.reason) == ("failed", "no outcome recorded")


async def test_function_scope_invalid_fields_fail_the_function_not_the_run(
    ctx: RunContext,
) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1) as fs:
        fs.complete("accepted", delta_pct="not a number")
    d = last(ctx, "function.completed").data  # type: ignore[attr-defined]
    assert (d.outcome, d.reason) == ("failed", "invalid function.completed fields")
    assert check_grammar(read_lines(ctx), complete=False) == []


async def test_function_scope_closes_abandoned_steps(ctx: RunContext) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1) as fs:
        ctx.emit("function.tests.write.started", {"kind": "write"}, function_id=FID, attempt=0)
        fs.complete("skipped_untestable")
    done = last(ctx, "function.tests.write.completed")
    assert done.data.error == ErrorInfo(kind="cancelled", message="step abandoned")  # type: ignore[attr-defined]
    assert types_of(ctx)[-2:] == ["function.tests.write.completed", "function.completed"]
    assert check_grammar(read_lines(ctx), complete=False) == []


async def test_function_scope_exception_becomes_failed(ctx: RunContext) -> None:
    await to_optimizing(ctx)
    async with ctx.function(function_info(FID), index=0, total=1) as fs:
        ctx.emit("function.tests.write.started", {"kind": "write"}, function_id=FID, attempt=0)
        fs.complete("accepted")  # ignored: the block raises
        raise ValueError("bad candidate")
    # swallowed: the run moves on to the next function
    assert types_of(ctx)[-3:] == ["log", "function.tests.write.completed", "function.completed"]
    log = last(ctx, "log")
    assert (log.data.level, log.data.source, log.function_id) == ("error", "orchestrator", FID)  # type: ignore[attr-defined]
    assert log.data.lines[-1] == "ValueError: bad candidate"  # type: ignore[attr-defined]
    tests = last(ctx, "function.tests.write.completed").data  # type: ignore[attr-defined]
    assert tests.error == ErrorInfo(
        kind="cancelled", message="function failed: ValueError: bad candidate"
    )
    d = last(ctx, "function.completed").data  # type: ignore[attr-defined]
    assert (d.outcome, d.reason) == ("failed", "ValueError: bad candidate")
    assert check_grammar(read_lines(ctx), complete=False) == []


async def test_function_scope_cancellation(ctx: RunContext) -> None:
    await to_optimizing(ctx)
    entered = asyncio.Event()

    async def body() -> None:
        async with ctx.function(function_info(FID), index=0, total=1):
            async with ctx.step(
                "function.tests.write.started", {"kind": "write"}, function_id=FID, attempt=0
            ):
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await entered.wait()
    ctx.cancel_reason = "user"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert types_of(ctx)[-2:] == ["function.tests.write.completed", "function.completed"]
    d = last(ctx, "function.completed").data  # type: ignore[attr-defined]
    assert (d.outcome, d.reason) == ("cancelled", "cancelled by user")
    assert not ctx.bus.open_steps()
    assert check_grammar(read_lines(ctx), complete=False) == []


# -- transitions ---------------------------------------------------------------------

ALL_STATES: list[str] = [*TRANSITIONS, *sorted(TERMINAL_STATES)]
FORWARD = [
    "created",
    "cloning",
    "installing",
    "discovering",
    "triaging",
    "awaiting_selection",
    "optimizing",
    "finalizing",
    "completed",
]


@pytest.mark.parametrize("frm", ALL_STATES)
@pytest.mark.parametrize("to", ALL_STATES)
def test_can_transition_table(frm: str, to: str) -> None:
    if frm in TERMINAL_STATES:
        expected = False
    elif to in ("failed", "cancelled", "interrupted"):
        expected = True
    else:
        expected = FORWARD.index(to) == FORWARD.index(frm) + 1 if to in FORWARD else False
    assert can_transition(frm, to) is expected


async def test_transition_emits_and_persists(ctx: RunContext) -> None:
    ctx.emit_created()
    ctx.transition("cloning", reason="go")
    ev = last(ctx, "run.state_changed")
    assert (ev.data.from_state, ev.data.to_state, ev.data.reason) == ("created", "cloning", "go")  # type: ignore[attr-defined]
    assert ctx.state == ctx.detail.state == "cloning"
    assert ctx.store.read_detail(ctx.run_id).state == "cloning"


@pytest.mark.parametrize(("frm", "to"), [("created", "optimizing"), ("cloning", "created")])
async def test_transition_rejects_illegal_moves(
    ctx: RunContext, frm: RunState, to: RunState
) -> None:
    ctx.emit_created()
    if frm != "created":
        ctx.transition(frm)
    seq = ctx.last_seq
    with pytest.raises(RuntimeError, match=f"invalid run transition {frm} -> {to}"):
        ctx.transition(to)
    assert ctx.state == frm and ctx.last_seq == seq


async def test_no_transition_out_of_a_terminal_state(ctx: RunContext) -> None:
    ctx.emit_created()
    ctx.transition("failed")
    assert ctx.terminal
    with pytest.raises(RuntimeError):
        ctx.transition("cancelled")


# -- selection -----------------------------------------------------------------------


async def test_wait_selection_and_select(ctx: RunContext) -> None:
    assert not ctx.awaiting_selection
    with pytest.raises(RuntimeError, match="not awaiting"):
        ctx.select([FID])
    task = asyncio.create_task(ctx.wait_selection())
    await wait_until(lambda: ctx.awaiting_selection)
    ctx.select([FID])
    assert not ctx.awaiting_selection
    assert await task == [FID]
    with pytest.raises(RuntimeError):
        ctx.select([FID])


async def test_cancelled_wait_selection_resets(ctx: RunContext) -> None:
    task = asyncio.create_task(ctx.wait_selection())
    await wait_until(lambda: ctx.awaiting_selection)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not ctx.awaiting_selection
    with pytest.raises(RuntimeError):
        ctx.select([FID])


async def _to_triaging(ctx: RunContext) -> None:
    ctx.emit_created()
    for s in ("cloning", "installing", "discovering", "triaging"):
        ctx.transition(s)  # type: ignore[arg-type]


async def test_confirm_selection_waits_for_the_user(ctx: RunContext) -> None:
    await _to_triaging(ctx)
    task = asyncio.create_task(ctx.confirm_selection([FID]))
    await wait_until(lambda: ctx.awaiting_selection)
    assert ctx.state == "awaiting_selection"
    ctx.select(["other.mod:fn"])
    assert await task == ["other.mod:fn"]
    sel = last(ctx, "run.selection.confirmed").data  # type: ignore[attr-defined]
    assert (sel.function_ids, sel.auto) == (["other.mod:fn"], False)
    assert ctx.state == "optimizing"


async def test_confirm_selection_auto(ctx: RunContext) -> None:
    await _to_triaging(ctx)
    ctx.auto_select = True
    assert await ctx.confirm_selection([FID]) == [FID]
    assert types_of(ctx)[-3:] == [
        "run.state_changed",
        "run.selection.confirmed",
        "run.state_changed",
    ]
    assert last(ctx, "run.selection.confirmed").data.auto is True  # type: ignore[attr-defined]
    assert ctx.detail.selection == [FID]


# -- logs, persistence, close --------------------------------------------------------


async def test_log_splits_text(ctx: RunContext) -> None:
    ctx.log("pytest", "one\ntwo\n")
    ctx.log("pytest", "", level="debug", stream="stderr", function_id=FID)
    a, b = events_of(ctx)
    assert a.data.lines == ["one", "two"]  # type: ignore[attr-defined]
    assert (b.data.lines, b.data.level, b.data.stream, b.function_id) == (  # type: ignore[attr-defined]
        [""],
        "debug",
        "stderr",
        FID,
    )


def test_record_mode_drops_debug_logs(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx(mode="record")
    ctx.log("pytest", "hidden", level="debug")
    ctx.log("pytest", "shown")
    sink = ctx.log_sink("pytest", level="debug")
    assert not sink.enabled
    assert [ev.data.lines for ev in events_of(ctx)] == [["shown"]]  # type: ignore[attr-defined]


def test_log_exception_keeps_the_traceback_tail(ctx: RunContext) -> None:
    try:
        raise RuntimeError("\n".join(f"line {i}" for i in range(60)))
    except RuntimeError as exc:
        ctx.log_exception("pytest", exc, function_id=FID)
    ev = last(ctx, "log")
    assert ev.data.level == "error" and ev.function_id == FID  # type: ignore[attr-defined]
    assert len(ev.data.lines) == 40  # type: ignore[attr-defined]
    assert ev.data.lines[-1] == "line 59"  # type: ignore[attr-defined]


def test_emit_created_persists_run_json(ctx: RunContext) -> None:
    ev = ctx.emit_created()
    detail = ctx.store.read_detail(ctx.run_id)
    assert detail is not None
    assert (detail.id, detail.state, detail.last_seq, detail.mode) == (
        ctx.run_id,
        "created",
        1,
        "demo",
    )
    assert detail.settings == ev.data.settings  # type: ignore[attr-defined]
    assert detail.created_ts == ev.ts


async def test_minor_events_are_persisted_with_a_debounce(ctx: RunContext) -> None:
    ctx.emit_created()
    ctx.log("pytest", "x")
    assert ctx.store.read_detail(ctx.run_id).last_seq == 1  # type: ignore[union-attr]
    ctx.close()  # flushes the pending write
    assert ctx.store.read_detail(ctx.run_id).last_seq == 2  # type: ignore[union-attr]


def test_minor_events_are_persisted_at_once_without_a_loop(ctx: RunContext) -> None:
    ctx.emit_created()
    ctx.log("pytest", "x")
    assert ctx.store.read_detail(ctx.run_id).last_seq == 2  # type: ignore[union-attr]


async def test_close(ctx: RunContext) -> None:
    ctx.emit_created()
    sub = ctx.bus.subscribe()
    sink = ctx.log_sink("pytest")
    sink.write("buffered")
    ctx.close()
    assert ctx.bus.closed
    lines = read_lines(ctx)
    assert types_of(ctx) == ["run.created", "log"]  # the sink was flushed first
    assert sub.queue.get_nowait() == (2, lines[1])  # subscribed after run.created
    assert sub.queue.get_nowait() is None  # then the close sentinel
    with pytest.raises(RuntimeError):
        ctx.emit("log", {"level": "info", "source": "x", "lines": ["late"]})
    ctx.close()  # idempotent
    assert ctx.store.read_detail(ctx.run_id).last_seq == 2  # type: ignore[union-attr]
