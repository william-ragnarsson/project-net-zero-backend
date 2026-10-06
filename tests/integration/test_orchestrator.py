"""RunManager end to end with scripted pipelines: lifecycle, commands, recovery."""

from __future__ import annotations

import asyncio
import json

import pytest

from netzero.api.schemas import CreateRunRequest
from netzero.events import ErrorInfo, RunSource
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.orchestrator import RunManager, RunRejected
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import FileLock, RunStore
from tests.integration.helpers import (
    DEMO,
    DEMO_AUTO,
    event_lines,
    events,
    make_settings,
    types,
    wait_until,
)
from tests.integration.scripted import FAST, IO, PRESELECTED, SLOW, Scripted


async def finish(m: RunManager, run_id: str) -> None:
    await asyncio.wait_for(m.wait(run_id), 5)


def terminal(m: RunManager, run_id: str) -> dict:
    last = events(m, run_id)[-1]
    assert last["type"].startswith("run.")
    return last


# -- happy paths --------------------------------------------------------------------


async def test_auto_select_runs_to_completed(manager_factory):
    m = manager_factory(Scripted())
    s = await m.create(DEMO_AUTO)
    assert s.state == "created" and s.mode == "demo"
    await finish(m, s.id)

    lines = event_lines(m, s.id)
    assert_grammar(lines)
    evs = [json.loads(line) for line in lines]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(next(iter(e)) == "seq" for e in evs)  # seq is the first key
    sel = next(e for e in evs if e["type"] == "run.selection.confirmed")
    assert sel["data"] == {"function_ids": PRESELECTED, "auto": True}
    states = [e["data"]["to_state"] for e in evs if e["type"] == "run.state_changed"]
    assert "awaiting_selection" in states  # auto still passes through it

    done = evs[-1]
    assert done["type"] == "run.completed"
    summary = done["data"]["summary"]
    assert summary["functions_total"] == 2 and summary["functions_done"] == 2
    assert summary["counts_by_outcome"] == {"no_significant_win": 2}

    # run.json agrees with the log, and nothing is left locked
    d = m.detail(s.id)
    assert d is not None and d.state == "completed" and d.last_seq == len(evs)
    assert [f.outcome for f in d.functions] == ["no_significant_win"] * 2
    assert m.active is None and m.live(s.id) is None
    assert not FileLock.is_locked(m.store.paths(s.id).lock)
    assert not FileLock.is_locked(m.store.active_lock_path)
    assert [x.id for x in m.summaries()] == [s.id]


async def test_manual_selection(manager_factory):
    m = manager_factory(Scripted())
    s = await m.create(DEMO)
    ctx = m.live(s.id)
    assert ctx is not None
    await wait_until(lambda: ctx.awaiting_selection)
    assert ctx.state == "awaiting_selection"

    with pytest.raises(RunRejected) as e:
        m.select(s.id, ["pkg.mod:nope"])
    assert e.value.code == "bad_request"
    with pytest.raises(RunRejected) as e:
        m.select(s.id, [FAST, IO])
    assert e.value.code == "bad_request" and "skipped" in e.value.detail

    m.select(s.id, [SLOW, SLOW])  # duplicates collapse, order is kept
    await finish(m, s.id)
    evs = events(m, s.id)
    assert_grammar(event_lines(m, s.id))
    sel = next(e for e in evs if e["type"] == "run.selection.confirmed")
    assert sel["data"] == {"function_ids": [SLOW], "auto": False}
    assert [e["function_id"] for e in evs if e["type"] == "function.started"] == [SLOW]
    assert evs[-1]["type"] == "run.completed"


async def test_select_in_the_wrong_state(manager_factory):
    pipe = Scripted(hold="clone")
    m = manager_factory(pipe)
    s = await m.create(DEMO)
    await asyncio.wait_for(pipe.reached.wait(), 5)
    with pytest.raises(RunRejected) as e:
        m.select(s.id, [FAST])
    assert e.value.code == "bad_state"
    pipe.release.set()
    await wait_until(lambda: m.live(s.id) is not None and m.live(s.id).awaiting_selection)
    m.select(s.id, [FAST])
    await finish(m, s.id)
    with pytest.raises(RunRejected) as e:
        m.select(s.id, [FAST])
    assert e.value.code == "bad_state"  # ended
    with pytest.raises(RunRejected) as e:
        m.select("20990101-000000-nope", [FAST])
    assert e.value.code == "not_found"


# -- create validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "req, code",
    [
        (CreateRunRequest(), "bad_request"),
        (CreateRunRequest(demo=True, github_url="https://github.com/a/b"), "bad_request"),
        (CreateRunRequest(demo=True, ref="main"), "bad_request"),
        (CreateRunRequest(github_url="https://gitlab.com/a/b"), "invalid_url"),
        (CreateRunRequest(github_url="https://github.com/a"), "invalid_url"),
        (CreateRunRequest(github_url="https://github.com/a/b", ref="-x"), "invalid_url"),
    ],
)
async def test_create_rejects(manager_factory, req, code):
    m = manager_factory(Scripted())
    with pytest.raises(RunRejected) as e:
        await m.create(req)
    assert e.value.code == code
    assert m.active is None and not FileLock.is_locked(m.store.active_lock_path)


async def test_live_run_needs_an_api_key(manager_factory):
    m = manager_factory(Scripted(), fake_pipeline=False)
    with pytest.raises(RunRejected) as e:
        await m.create(CreateRunRequest(github_url="https://github.com/octo/repo"))
    assert e.value.code == "no_api_key"


async def test_github_source(manager_factory):
    m = manager_factory(Scripted())
    s = await m.create(
        CreateRunRequest(github_url="https://github.com/Octo/Repo.git", auto_select=True)
    )
    await finish(m, s.id)
    assert s.mode == "live" and s.source.kind == "github"
    assert s.source.url == "https://github.com/Octo/Repo"
    clone = next(e for e in events(m, s.id) if e["type"] == "run.clone.started")
    assert clone["data"]["url"] == "https://github.com/Octo/Repo"


# -- one run at a time ----------------------------------------------------------------


async def test_second_run_is_rejected_while_one_is_active(manager_factory):
    pipe = Scripted()
    m = manager_factory(pipe)
    s = await m.create(DEMO)
    with pytest.raises(RunRejected) as e:
        await m.create(DEMO_AUTO)
    assert e.value.code == "run_active"
    assert e.value.active_run is not None and e.value.active_run.id == s.id

    # another manager (standing in for another process) on the same runs dir
    other = manager_factory(Scripted())
    with pytest.raises(RunRejected) as e:
        await other.create(DEMO_AUTO)
    assert e.value.code == "run_active"
    assert e.value.active_run is not None and e.value.active_run.id == s.id
    # it sees the run as owned: no recovery, and it cannot cancel it
    assert other.detail(s.id).state not in ("interrupted", "failed")
    with pytest.raises(RunRejected) as e:
        await other.cancel(s.id)
    assert e.value.code == "bad_state"

    await m.cancel(s.id)
    s2 = await m.create(DEMO_AUTO)  # the slot is free again
    await finish(m, s2.id)
    assert terminal(m, s2.id)["type"] == "run.completed"


# -- failure, cancel, shutdown -------------------------------------------------------


async def test_pipeline_exception_fails_the_run(manager_factory):
    m = manager_factory(Scripted(fail_at="env"))
    s = await m.create(DEMO_AUTO)
    await finish(m, s.id)
    lines = event_lines(m, s.id)
    assert_grammar(lines)
    evs = events(m, s.id)
    env_done = next(e for e in evs if e["type"] == "run.env.completed")
    assert env_done["data"]["ok"] is False
    assert "env exploded" in env_done["data"]["error"]["message"]
    last = evs[-1]
    assert last["type"] == "run.failed"
    assert last["data"]["stage"] == "installing"
    assert "env exploded" in last["data"]["error"]["message"]
    assert any(e["type"] == "log" and e["data"]["level"] == "error" for e in evs)
    d = m.detail(s.id)
    assert d.state == "failed" and d.error is not None


async def test_function_exception_does_not_stop_the_run(manager_factory):
    m = manager_factory(Scripted(fail_at="function"))
    s = await m.create(DEMO_AUTO)
    await finish(m, s.id)
    assert_grammar(event_lines(m, s.id))
    outcomes = {
        e["function_id"]: e["data"]["outcome"]
        for e in events(m, s.id)
        if e["type"] == "function.completed"
    }
    assert outcomes == {FAST: "failed", SLOW: "no_significant_win"}
    assert terminal(m, s.id)["type"] == "run.completed"


async def test_pipeline_that_stops_early_fails(manager_factory):
    m = manager_factory(Scripted(stop_at="optimizing"))
    s = await m.create(DEMO_AUTO)
    await finish(m, s.id)
    assert_grammar(event_lines(m, s.id))
    last = terminal(m, s.id)
    assert last["type"] == "run.failed"
    assert "optimizing" in last["data"]["error"]["message"]


async def test_cancel_closes_open_steps(manager_factory):
    pipe = Scripted(hold="function")
    m = manager_factory(pipe)
    s = await m.create(DEMO_AUTO)
    await asyncio.wait_for(pipe.reached.wait(), 5)

    summary = await m.cancel(s.id)
    assert summary.state == "cancelled"
    lines = event_lines(m, s.id)
    assert_grammar(lines)
    tail = [json.loads(line) for line in lines[-4:]]
    assert [e["type"] for e in tail] == [
        "function.tests.write.completed",
        "function.completed",
        "run.state_changed",
        "run.cancelled",
    ]
    assert tail[0]["data"]["ok"] is False
    assert tail[0]["data"]["error"]["kind"] == "cancelled"
    assert tail[1]["data"]["outcome"] == "cancelled"
    assert tail[3]["data"] == {"at_state": "optimizing", "reason": "user"}

    again = await m.cancel(s.id)  # idempotent
    assert again.state == "cancelled" and again.last_seq == summary.last_seq
    assert len(event_lines(m, s.id)) == len(lines)


async def test_cancel_unknown_run(manager_factory):
    m = manager_factory(Scripted())
    with pytest.raises(RunRejected) as e:
        await m.cancel("20990101-000000-nope")
    assert e.value.code == "not_found"


async def test_shutdown_interrupts_the_run(manager_factory):
    pipe = Scripted(hold="clone")
    m = manager_factory(pipe)
    s = await m.create(DEMO)
    await asyncio.wait_for(pipe.reached.wait(), 5)
    await m.shutdown(timeout=2)

    assert_grammar(event_lines(m, s.id))
    last = terminal(m, s.id)
    assert last["type"] == "run.interrupted"
    assert last["data"]["previous_state"] == "cloning"
    assert last["data"]["open_steps"] == [
        {"type": "run.clone.started", "function_id": None, "candidate_id": None, "attempt": None}
    ]
    clone_done = next(e for e in events(m, s.id) if e["type"] == "run.clone.completed")
    assert clone_done["data"]["error"]["kind"] == "interrupted"
    assert m.active is None


async def test_shutdown_while_awaiting_selection(manager_factory):
    m = manager_factory(Scripted())
    s = await m.create(DEMO)
    await wait_until(lambda: m.live(s.id) is not None and m.live(s.id).awaiting_selection)
    await m.shutdown(timeout=2)
    assert_grammar(event_lines(m, s.id))
    last = terminal(m, s.id)
    assert last["type"] == "run.interrupted"
    assert last["data"] == {"previous_state": "awaiting_selection", "open_steps": []}


# -- recovery --------------------------------------------------------------------------


def orphan(runs_dir, *, state_to: str | None = None, partial_tail: bool = True) -> str:
    """Write a run as a crashed process would leave it: no lock, no terminal event."""
    store = RunStore(runs_dir)
    run_id = store.new_run_id("demo")
    rp = store.paths(run_id)
    rp.mkdirs()
    ctx = RunContext(
        run_id=run_id,
        paths=rp,
        settings=make_settings(runs_dir),
        store=store,
        mode="demo",
        source=RunSource(kind="demo"),
    )
    ctx.emit_created()
    ctx.transition("cloning")
    ctx.emit("run.clone.started", {"url": None})
    if state_to:
        ctx.bus.close_open_steps(ErrorInfo(kind="internal", message="boom"))
        ctx.transition(state_to)  # type: ignore[arg-type]
    ctx.close()
    if partial_tail:
        with rp.events.open("a") as f:
            f.write('{"seq":99,"ts":1,"run_id":"half')  # torn write
    return run_id


async def test_boot_recovers_an_orphaned_run(manager_factory, runs_dir):
    run_id = orphan(runs_dir)
    m = manager_factory(Scripted())  # start() recovers
    lines = event_lines(m, run_id)
    assert_grammar(lines)
    evs = [json.loads(line) for line in lines]
    assert [e["type"] for e in evs[-3:]] == [
        "run.clone.completed",
        "run.state_changed",
        "run.interrupted",
    ]
    assert evs[-1]["data"]["previous_state"] == "cloning"
    assert evs[-1]["data"]["open_steps"][0]["type"] == "run.clone.started"
    clone_done = next(e for e in evs if e["type"] == "run.clone.completed")
    assert clone_done["data"]["ok"] is False
    assert m.detail(run_id).state == "interrupted"
    assert m.store.read_summary(run_id).state == "interrupted"
    # recovering again changes nothing
    assert m.recover_all() == 0
    assert event_lines(m, run_id) == lines


async def test_lazy_recovery_on_read(manager_factory, runs_dir):
    m = manager_factory(Scripted())
    run_id = orphan(runs_dir)  # appears after boot
    d = m.detail(run_id)
    assert d is not None and d.state == "interrupted"
    assert types(m, run_id)[-1] == "run.interrupted"
    assert_grammar(event_lines(m, run_id))


async def test_owned_run_is_not_recovered(manager_factory, runs_dir):
    run_id = orphan(runs_dir, partial_tail=False)
    lock = FileLock(RunStore(runs_dir).paths(run_id).lock)
    assert lock.try_acquire()  # "another process" still drives it
    try:
        m = manager_factory(Scripted())
        assert m.recover(run_id) is None
        assert m.detail(run_id).state == "cloning"
        assert types(m, run_id)[-1] == "run.clone.started"
    finally:
        lock.release()
    assert m.detail(run_id).state == "interrupted"


async def test_recovery_writes_a_missing_terminal_event(manager_factory, runs_dir):
    # the process died between run.state_changed(failed) and run.failed
    run_id = orphan(runs_dir, state_to="failed", partial_tail=False)
    m = manager_factory(Scripted())
    lines = event_lines(m, run_id)
    assert_grammar(lines)
    last = json.loads(lines[-1])
    assert last["type"] == "run.failed"
    assert last["data"]["stage"] == "cloning"
