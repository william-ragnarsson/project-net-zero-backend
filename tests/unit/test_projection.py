"""``Projection``: folding events into the ``RunDetail`` behind ``run.json``."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from netzero.events import (
    CI,
    ErrorInfo,
    FunctionCompletedData,
    RunDetail,
    RunSource,
    make_event,
)
from netzero.pipeline.projection import Projection, project, rebuild
from netzero.pipeline.run import RunContext
from tests.unit.support import FID, FID2, drive_valid_run, read_lines, triage_item


async def test_projection_of_a_valid_run(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    await drive_valid_run(ctx, (FID, FID2), failing={FID2})
    ctx.close()
    lines = read_lines(ctx)
    d = ctx.detail

    assert d.state == "completed"
    assert d.last_seq == ctx.last_seq == len(lines)
    assert d.counts_by_outcome == {"accepted": 1, "failed": 1}
    assert d.selection == [FID, FID2]
    assert [(f.function_id, f.qualname, f.outcome) for f in d.functions] == [
        (FID, "Cls.method", "accepted"),
        (FID2, "helper", "failed"),
    ]
    assert d.functions[0].winner == "A"
    assert d.functions[1].reason.startswith("RuntimeError: pipeline bug")
    assert (d.functions_total, d.functions_done) == (2, 2)
    assert d.mean_reduction_pct == -40.0
    assert d.g_saved_per_1m_calls == 2.0
    assert [t.function_id for t in d.triage] == [FID, FID2]
    assert d.settings is not None and d.settings.n_trials == ctx.settings.n_trials
    assert d.models == ctx.settings.stages.models()
    assert d.mode == "demo" and d.source == RunSource(kind="demo")

    # run.json and a rebuild from events.jsonl agree with the live projection
    assert project(lines, ctx.run_id) == d
    assert ctx.store.read_detail(ctx.run_id) == d
    assert rebuild(lines, ctx.run_id).totals(duration_ms=0).kwh_saved_per_1m_calls == 0.01


def _proj() -> Projection:
    return Projection.empty(
        "20261005-120000-p", created_ts=1, source=RunSource(kind="demo"), mode="demo"
    )


def _ev(seq: int, type_: str, data=None, **scope):
    return make_event(type_, seq=seq, ts=100 + seq, run_id="20261005-120000-p", data=data, **scope)


def test_selection_builds_function_records() -> None:
    p = _proj()
    p.apply(
        _ev(1, "run.triage.completed", {"ok": True, "duration_ms": 1, "items": [triage_item()]})
    )
    p.apply(_ev(2, "run.selection.confirmed", {"function_ids": [FID, "other.mod:fn"]}))
    d = p.detail
    assert [f.qualname for f in d.functions] == ["Cls.method", "fn"]
    assert d.functions_total == 2 and d.functions_done == 0
    assert all(f.outcome is None for f in d.functions)
    assert (d.last_seq, d.updated_ts) == (2, 102)


def test_function_completed_counts_once_and_updates() -> None:
    p = _proj()
    p.apply(_ev(1, "run.selection.confirmed", {"function_ids": [FID]}))
    win = FunctionCompletedData(
        outcome="accepted",
        winner="B",
        delta_pct=-20.0,
        delta_ci_pct=CI(lo=-25.0, hi=-15.0),
        g_saved_per_1m_calls=4.0,
        kwh_saved_per_1m_calls=0.5,
        duration_ms=10,
    )
    p.apply(_ev(2, "function.completed", win, function_id=FID))
    assert p.detail.functions_done == 1
    assert p.detail.counts_by_outcome == {"accepted": 1}
    assert p.totals(duration_ms=5).kwh_saved_per_1m_calls == 0.5
    reverted = FunctionCompletedData(outcome="reverted", duration_ms=1)
    p.apply(_ev(3, "function.completed", reverted, function_id=FID))
    d = p.detail
    assert d.functions_done == 1  # a second completion is not a second function
    assert d.counts_by_outcome == {"reverted": 1}
    assert d.mean_reduction_pct is None and d.g_saved_per_1m_calls == 0.0
    assert p.totals(duration_ms=5).kwh_saved_per_1m_calls == 0.0


def test_function_completed_for_unselected_function_adds_a_record() -> None:
    p = _proj()
    data = FunctionCompletedData(outcome="skipped_untestable", duration_ms=1)
    p.apply(_ev(1, "function.completed", data, function_id="a.b:c.d"))
    d = p.detail
    assert [(f.function_id, f.qualname) for f in d.functions] == [("a.b:c.d", "c.d")]
    assert (d.functions_total, d.functions_done) == (1, 1)


def test_discovery_triage_and_misc_fields() -> None:
    p = _proj()
    p.apply(
        _ev(
            1,
            "run.discovery.completed",
            {"ok": True, "duration_ms": 1, "heuristic_ranked": [triage_item(FID2)]},
        )
    )
    assert [t.function_id for t in p.detail.triage] == [FID2]
    p.apply(_ev(2, "run.triage.completed", {"ok": False, "duration_ms": 1}))
    assert [t.function_id for t in p.detail.triage] == [FID2]  # failed triage keeps heuristics
    p.apply(
        _ev(3, "run.triage.completed", {"ok": True, "duration_ms": 1, "items": [triage_item()]})
    )
    assert [t.function_id for t in p.detail.triage] == [FID]
    p.apply(
        _ev(
            4,
            "llm.usage",
            {"stage": "triage", "model": "m", "cassette": "off", "run_cost_usd": 0.25},
        )
    )
    assert p.detail.llm_cost_usd == 0.25
    p.apply(_ev(5, "run.state_changed", {"from_state": "created", "to_state": "failed"}))
    err = ErrorInfo(kind="clone_error", message="nope")
    p.apply(_ev(6, "run.failed", {"stage": "cloning", "error": err}))
    assert p.detail.state == "failed"
    assert p.detail.error == err


def test_rebuild_requires_run_created_first() -> None:
    with pytest.raises(ValueError):
        project([], "20261005-120000-p")
    line = _ev(1, "run.state_changed", {"from_state": "created", "to_state": "cloning"})
    with pytest.raises(ValueError):
        project([line.model_dump_json()], "20261005-120000-p")


def test_empty_projection() -> None:
    d = _proj().detail
    assert isinstance(d, RunDetail)
    assert (d.state, d.last_seq, d.functions, d.counts_by_outcome) == ("created", 0, [], {})
