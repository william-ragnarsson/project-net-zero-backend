"""Small hand-written pipelines for the orchestrator and API tests.

``Scripted`` walks the run through every state with real steps and one
step per selected function. Tests steer it with ``hold`` (block inside a
step until ``release``), ``fail_at`` (raise at a named point) and ``stop_at``
(return early, without reaching ``finalizing``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from netzero.events import (
    FunctionInfo,
    RunCloneStartedData,
    RunEnvStartedData,
    RunTriageStartedData,
    TestsWriteStartedData,
    TriageItem,
)
from netzero.pipeline.run import RunContext

FAST = "pkg.mod:fast"
SLOW = "pkg.mod:Thing.slow"
IO = "pkg.mod:fetch"


def triage_item(fid: str, *, pre: bool = False, skip: str | None = None) -> TriageItem:
    module, qualname = fid.split(":")
    return TriageItem(
        function_id=fid,
        module=module,
        qualname=qualname,
        kind="method" if "." in qualname else "function",
        file="pkg/mod.py",
        line=1,
        end_line=8,
        loc=8,
        import_line=f"from {module} import {qualname.split('.')[0]}",
        call_hint=f"{qualname}(...)",
        heuristic_score=0.5,
        score=0.5 if skip is None else 0.0,
        skip_reason=skip,
        preselected=pre,
    )


ITEMS = [triage_item(FAST, pre=True), triage_item(SLOW, pre=True), triage_item(IO, skip="io")]
PRESELECTED = [i.function_id for i in ITEMS if i.preselected]


def function_info(fid: str) -> FunctionInfo:
    item = next(i for i in ITEMS if i.function_id == fid)
    return FunctionInfo(
        **item.model_dump(
            include={
                "function_id",
                "module",
                "qualname",
                "kind",
                "file",
                "line",
                "end_line",
                "loc",
                "import_line",
                "call_hint",
            }
        ),
        source="def f():\n    pass\n",
    )


class Boom(RuntimeError):
    pass


@dataclass
class Scripted:
    hold: str | None = None  # "clone" | "function"
    fail_at: str | None = None  # "env" | "function" | "prelude"
    stop_at: str | None = None  # "optimizing": return without finalizing
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    contexts: list[RunContext] = field(default_factory=list)

    async def _maybe_hold(self, where: str) -> None:
        if self.hold == where:
            self.reached.set()
            await self.release.wait()

    async def __call__(self, ctx: RunContext) -> None:
        self.contexts.append(ctx)
        ctx.transition("cloning")
        async with ctx.step("run.clone.started", RunCloneStartedData(url=ctx.source.url)) as st:
            await self._maybe_hold("clone")
            st.ok(commit_sha="0" * 40, n_files=3, n_py_files=1)
        ctx.transition("installing")
        async with ctx.step("run.env.started", RunEnvStartedData(python_request="3.12")) as st:
            if self.fail_at == "env":
                raise Boom("env exploded")
            st.ok(python_version="3.12.0")
        if self.fail_at == "prelude":
            raise Boom("prelude exploded")
        ctx.transition("discovering")
        async with ctx.step("run.discovery.started") as st:
            st.ok(n_files=1, n_functions=len(ITEMS), heuristic_ranked=ITEMS)
        ctx.transition("triaging")
        async with ctx.step(
            "run.triage.started", RunTriageStartedData(n_candidates=len(ITEMS), llm=False)
        ) as st:
            st.ok(items=ITEMS, preselected=PRESELECTED)
        ids = await ctx.confirm_selection(PRESELECTED)
        if self.stop_at == "optimizing":
            return
        for i, fid in enumerate(ids):
            async with ctx.function(function_info(fid), index=i, total=len(ids)) as fs:
                async with ctx.step(
                    "function.tests.write.started",
                    TestsWriteStartedData(kind="write"),
                    function_id=fid,
                ) as st:
                    await self._maybe_hold("function")
                    if self.fail_at == "function" and i == 0:
                        raise Boom("tests exploded")
                    st.ok()
                fs.complete("no_significant_win", reason="scripted")
        ctx.transition("finalizing")
