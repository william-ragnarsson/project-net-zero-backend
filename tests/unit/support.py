"""Builders shared by the unit tests: a small grammar-valid run driven through ``RunContext``."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from netzero.events import (
    EventBase,
    FunctionInfo,
    RunCompletedData,
    TriageItem,
    dump_event,
    parse_event,
)
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import read_complete_lines

FID = "pkg.mod:Cls.method"
FID2 = "pkg.util:helper"


def function_info(fid: str = FID) -> FunctionInfo:
    module, qualname = fid.split(":")
    return FunctionInfo(
        function_id=fid,
        module=module,
        qualname=qualname,
        kind="function",
        file=module.replace(".", "/") + ".py",
        line=1,
        end_line=5,
        loc=5,
        import_line=f"from {module} import {qualname.split('.')[0]}",
        call_hint=f"{qualname}()",
        source="def f():\n    return 1\n",
    )


def triage_item(fid: str = FID, score: float = 0.9) -> TriageItem:
    info = function_info(fid)
    return TriageItem(
        function_id=fid,
        module=info.module,
        qualname=info.qualname,
        kind=info.kind,
        file=info.file,
        line=info.line,
        end_line=info.end_line,
        loc=info.loc,
        import_line=info.import_line,
        call_hint=info.call_hint,
        heuristic_score=score,
        score=score,
        preselected=True,
    )


async def drive_valid_run(
    ctx: RunContext,
    fids: Sequence[str] = (FID,),
    *,
    failing: Iterable[str] = (),
) -> None:
    """Drive ``ctx`` through a complete, grammar-valid run ending in ``run.completed``.

    Each function in ``fids`` is accepted (candidate A wins) unless it is in
    ``failing``, in which case its block raises and the outcome becomes ``failed``.
    """
    failing = set(failing)
    ctx.emit_created()
    ctx.transition("cloning")
    async with ctx.step("run.clone.started", {"url": "https://example.invalid/r.git"}) as st:
        st.ok(commit_sha="abc123", n_files=3, n_py_files=2, size_bytes=100)
    ctx.transition("installing")
    async with ctx.step("run.env.started", {"python_request": "3.12"}) as st:
        st.ok(python_version="3.12.2")
    ctx.transition("discovering")
    async with ctx.step("run.discovery.started") as st:
        st.ok(n_files=2, n_functions=len(fids))
    ctx.transition("triaging")
    async with ctx.step("run.triage.started", {"n_candidates": len(fids), "llm": False}) as st:
        st.ok(items=[triage_item(f) for f in fids], preselected=list(fids))
    ctx.transition("awaiting_selection")
    ctx.emit("run.selection.confirmed", {"function_ids": list(fids)})
    ctx.transition("optimizing")
    for i, fid in enumerate(fids):
        async with ctx.function(function_info(fid), index=i, total=len(fids)) as fs:
            async with ctx.step(
                "function.tests.write.started", {"kind": "write"}, function_id=fid, attempt=0
            ):
                pass
            if fid in failing:
                raise RuntimeError(f"pipeline bug in {fid}")
            async with ctx.step(
                "candidate.write.started",
                {"kind": "write", "strategy_hint": "vectorize"},
                function_id=fid,
                candidate_id="A",
                attempt=0,
            ):
                pass
            ctx.emit(
                "candidate.rejected", {"reason": "identical"}, function_id=fid, candidate_id="B"
            )
            ctx.emit(
                "function.decision",
                {"outcome": "winner", "winner": "A", "rule": {"alpha": 0.05, "min_effect_pct": 5}},
                function_id=fid,
            )
            async with ctx.step("function.merge.started", function_id=fid) as st:
                st.ok(commit_sha="def456")
            fs.complete(
                "accepted",
                winner="A",
                delta_pct=-40.0,
                g_saved_per_1m_calls=2.0,
                kwh_saved_per_1m_calls=0.01,
            )
    ctx.transition("finalizing")
    async with ctx.step("run.artifacts.started"):
        pass
    ctx.transition("completed")
    ctx.emit(
        "run.completed",
        RunCompletedData(summary=ctx.projection.totals(duration_ms=ctx.elapsed_ms())),
    )


def read_lines(ctx: RunContext) -> list[str]:
    """Every complete line of the run's ``events.jsonl``."""
    lines, _ = read_complete_lines(ctx.paths.events)
    return lines


def parse_all(lines: Iterable[str]) -> list[EventBase]:
    return [parse_event(line) for line in lines]  # type: ignore[misc]


def renumber(events: Iterable[EventBase]) -> list[str]:
    """Dump ``events`` with seq rewritten to 1..n (keeps every other field)."""
    return [dump_event(ev.model_copy(update={"seq": i})) for i, ev in enumerate(events, 1)]
