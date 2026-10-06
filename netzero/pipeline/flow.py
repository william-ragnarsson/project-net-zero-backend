"""The real pipeline: clone -> env -> discovery -> triage -> selection -> functions -> artifacts.

Each stage is a ``ctx.step``; an exception inside one closes the step with
``ok=false`` and the orchestrator ends the run ``failed`` at that state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from netzero import paths as nz_paths
from netzero.bench.lane import BenchLane, QuietGate
from netzero.bench.probe import probe
from netzero.errors import EnvError, NetzeroError
from netzero.events import (
    ImportProbe,
    LlmUsageData,
    PowerInfo,
    RunCloneStartedData,
    RunEnvStartedData,
    RunPowerDetectedData,
    RunTriageStartedData,
    TriageItem,
)
from netzero.llm.cassette import CassetteKey, CassetteStore
from netzero.llm.client import LlmClient
from netzero.llm.tasks import make_ranker
from netzero.pipeline import triage
from netzero.pipeline.artifacts import write_artifacts
from netzero.pipeline.bus import LogSink
from netzero.pipeline.clone import (
    CloneResult,
    clone_github,
    copy_demo,
    parse_github_url,
    validate_ref,
)
from netzero.pipeline.common import reap
from netzero.pipeline.discovery import DiscoveredFunction, DiscoveryResult, discover
from netzero.pipeline.env import PYTHON_REQUEST, EnvBuilder, dependency_sources, install_harness
from netzero.pipeline.function_flow import RunShared, optimize_function
from netzero.pipeline.harness_runs import Sandbox
from netzero.pipeline.run import RunContext
from netzero.pipeline.trunk import create_trunk
from netzero.sandbox import runner


@dataclass
class Prelude:
    """What the stages before selection hand to the function stage."""

    base_sha: str
    discovery: DiscoveryResult
    probe: ImportProbe
    functions: dict[str, DiscoveredFunction]
    items: list[TriageItem]
    env: EnvBuilder


def _lines(sink: LogSink) -> runner.LineCallback:
    return lambda _stream, line: sink.write(line)


async def clone_stage(ctx: RunContext) -> CloneResult:
    ctx.transition("cloning")
    src = ctx.source
    async with ctx.step("run.clone.started", RunCloneStartedData(url=src.url, ref=src.ref)) as st:
        with ctx.log_sink("clone", log_name="clone.log") as sink:
            if src.kind == "demo":
                result = await copy_demo(nz_paths.DEMO_REPO, ctx.paths.repo, procs=ctx.procs)
            else:
                repo = parse_github_url(src.url or "")
                s = ctx.settings
                result = await clone_github(
                    repo,
                    validate_ref(src.ref),
                    ctx.paths.repo,
                    timeout=s.clone_timeout_s,
                    max_mb=s.clone_max_mb,
                    max_files=s.clone_max_files,
                    procs=ctx.procs,
                    on_line=_lines(sink),
                )
            await create_trunk(ctx.paths.repo, ctx.paths.trunk, result.commit_sha, ctx.procs)
        st.ok(
            commit_sha=result.commit_sha,
            n_files=result.n_files,
            n_py_files=result.n_py_files,
            size_bytes=result.size_bytes,
        )
    return result


async def env_stage(ctx: RunContext, found: DiscoveryResult) -> tuple[EnvBuilder, ImportProbe]:
    """Venv, dependencies (best effort), pytest, then the import probe."""
    ctx.transition("installing")
    p = ctx.paths
    demo = ctx.source.kind == "demo"
    sources = dependency_sources(p.trunk, demo=demo)
    started = RunEnvStartedData(
        python_request=PYTHON_REQUEST,
        dependency_sources=[s.relative_to(p.trunk).as_posix() for s in sources],
    )
    async with ctx.step("run.env.started", started) as st:
        workdir = p.work / "env"
        workdir.mkdir(parents=True, exist_ok=True)
        with ctx.log_sink("env", log_name="env.log") as sink:
            b = EnvBuilder(
                venv=p.venv,
                workdir=workdir,
                procs=ctx.procs,
                on_line=_lines(sink),
                timeout=ctx.settings.install_timeout_s,
            )
            await b.create()
            try:
                await b.install(sources, offline=demo)
            except EnvError as exc:
                # keep going: modules that need the missing packages fail the probe
                # and triage skips them
                msg = f"{exc.message}; continuing without the repo's dependencies"
                ctx.log("env", msg, level="warn")
                if exc.detail:
                    ctx.log("env", exc.detail, level="warn")
                await b.ensure_pytest()
            modules = list(dict.fromkeys(fn.module for fn in found.functions if fn.module))
            probe = await b.probe_imports(
                modules,
                cwd=p.trunk,
                import_roots=[p.trunk / r for r in found.import_roots],
                harness_root=install_harness(p.harness),
                home=p.home,
                tmp=p.tmp,
            )
            st.ok(
                python_version=await b.python_version(),
                packages=await b.packages(),
                import_probe=probe,
            )
    return b, probe


async def discovery_stage(
    ctx: RunContext, found: DiscoveryResult, probe: ImportProbe
) -> list[TriageItem]:
    ctx.transition("discovering")
    s = ctx.settings
    async with ctx.step("run.discovery.started") as st:
        for rel, err in list(found.parse_errors.items())[:20]:
            ctx.log("orchestrator", f"skipped {rel}: {err}", level="warn")
        items = triage.heuristic_items(found.functions, probe, max_items=s.max_functions)
        items = triage.preselect(items, n=s.preselect, min_score=s.preselect_min_score)
        st.ok(
            n_files=len(found.source_files),
            n_functions=len(found.functions),
            n_test_files=len(found.test_files),
            import_roots=found.import_roots,
            heuristic_ranked=items,
        )
    if not any(not it.skip_reason for it in items):
        n = len(found.functions)
        raise NetzeroError(
            f"no function can be optimized ({n} found, all skipped)"
            if n
            else "no functions found outside tests",
            kind="validation",
        )
    return items


async def triage_stage(
    ctx: RunContext,
    items: list[TriageItem],
    functions: list[DiscoveredFunction],
    ranker: triage.LlmRanker | None,
) -> list[TriageItem]:
    ctx.transition("triaging")
    s = ctx.settings
    shortlist = triage.llm_shortlist(items, functions, s.triage_llm_top) if ranker else []
    started = RunTriageStartedData(n_candidates=len(shortlist), llm=bool(shortlist))
    async with ctx.step("run.triage.started", started) as st:
        ratings: dict[str, triage.LlmRating] = {}
        if shortlist:
            try:
                ratings = await ranker(shortlist)  # type: ignore[misc]
            except Exception as exc:  # the heuristic ranking stands
                ctx.log("llm", f"triage ranking failed, using heuristics: {exc}", level="warn")
        ranked = triage.apply_ratings(items, ratings, max_items=s.max_functions)
        ranked = triage.preselect(ranked, n=s.preselect, min_score=s.preselect_min_score)
        st.ok(
            items=ranked,
            preselected=[it.function_id for it in ranked if it.preselected],
            llm_used=bool(ratings),
        )
    return ranked


async def prelude(ctx: RunContext, *, ranker: triage.LlmRanker | None = None) -> Prelude:
    cloned = await clone_stage(ctx)
    # parse first: the module list feeds the import probe in the env stage
    found = await asyncio.to_thread(discover, ctx.paths.trunk)
    found.functions = triage.unique_functions(found.functions)
    env, probe = await env_stage(ctx, found)
    items = await discovery_stage(ctx, found, probe)
    items = await triage_stage(ctx, items, found.functions, ranker)
    return Prelude(
        base_sha=cloned.commit_sha,
        discovery=found,
        probe=probe,
        functions={fn.function_id: fn for fn in found.functions},
        items=items,
        env=env,
    )


def llm_client(ctx: RunContext) -> LlmClient:
    """Demo runs replay the cassettes, record runs write them; live runs use the setting."""
    mode = {"demo": "replay", "record": "record"}.get(ctx.mode, ctx.settings.cassette_mode)
    settings = ctx.settings.model_copy(update={"cassette_mode": mode})

    def on_usage(usage: LlmUsageData, key: CassetteKey) -> None:
        total = ctx.add_llm_cost(usage.cost_usd)
        ctx.emit(
            "llm.usage",
            usage.model_copy(update={"run_cost_usd": round(total, 8)}),
            function_id=key.function_id,
            candidate_id=key.candidate,
            attempt=key.attempt if key.function_id else None,
        )

    return LlmClient(
        settings,
        cassettes=CassetteStore(nz_paths.DEMO_REPO / ".netzero-cassettes"),
        on_usage=on_usage,
        on_warn=lambda msg: ctx.log("llm", msg, level="warn"),
    )


async def detect_power(ctx: RunContext) -> PowerInfo:
    """The power profile ``run.created`` did not carry: probe it (``run.power.detected``)."""
    if ctx.detail.power is not None:
        return ctx.detail.power
    power = await probe(ctx.settings, procs=ctx.procs)
    ctx.emit("run.power.detected", RunPowerDetectedData(power=power))
    return power


async def run_pipeline(ctx: RunContext) -> None:
    llm = llm_client(ctx)
    power_task = asyncio.create_task(detect_power(ctx), name="power-probe")
    try:
        pre = await prelude(ctx, ranker=make_ranker(llm))
        power = await power_task
        ids = await ctx.confirm_selection([it.function_id for it in pre.items if it.preselected])
        p = ctx.paths
        gate = QuietGate()
        shared = RunShared(
            ctx=ctx,
            llm=llm,
            sandbox=Sandbox(
                venv=p.venv, harness_root=p.harness, home=p.home, tmp=p.tmp, procs=ctx.procs
            ),
            lane=None,
            gate=gate,
            import_roots=pre.discovery.import_roots,
            fixed_date=ctx.source.kind == "demo",
        )
        shared.lane = BenchLane(
            ctx.settings,
            power,
            venv=p.venv,
            harness_root=p.harness,
            home=p.home,
            tmp=p.tmp,
            procs=ctx.procs,
            log=shared.lane_log,
            gate=gate,
        )
        for index, fid in enumerate(ids):  # the orchestrator validated ids against the items
            await optimize_function(shared, pre.functions[fid], index, len(ids))
        ctx.transition("finalizing")
        await write_artifacts(ctx, pre.base_sha, shared.merged, shared.passed_tests)
    finally:
        await reap([power_task])
        await llm.aclose()


__all__ = ["Prelude", "detect_power", "llm_client", "prelude", "run_pipeline"]
