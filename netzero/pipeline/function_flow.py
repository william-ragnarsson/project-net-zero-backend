"""One selected function: tests (+ repairs) -> capture -> baseline || A/B/C -> decision -> merge.

The order of events mirrors ``FakePipeline`` exactly; the UI and the CLI
cannot tell the two apart except by the numbers.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from netzero.errors import SandboxError, StepTimeout, ValidationFailed
from netzero.events import (
    DecisionRule,
    DiffCheckResult,
    FunctionDecisionData,
    PytestFailure,
    PytestResult,
    TestFile,
    TestsRunStartedData,
    TestsWriteStartedData,
)
from netzero.llm import tasks as llm_tasks
from netzero.llm.client import LlmClient
from netzero.pipeline import static_check
from netzero.pipeline.candidates import (
    BenchSlot,
    CandidateResult,
    CandidateRun,
    apply_rewrite,
)
from netzero.pipeline.common import CANDIDATES, ci_text, fmt_p, pct, reap
from netzero.pipeline.discovery import DiscoveredFunction
from netzero.pipeline.git import commit_files, git
from netzero.pipeline.harness_runs import (
    Captured,
    Sandbox,
    capture_inputs,
    differential,
    run_pytest,
)
from netzero.pipeline.run import FunctionScope, RunContext
from netzero.pipeline.splice import SpliceError

if TYPE_CHECKING:
    from netzero.bench.lane import BenchLane, QuietGate

TESTS_DIR = "netzero-tests"  # where the generated tests go in the zip


def test_file_name(fn: DiscoveredFunction) -> str:
    def flat(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")

    return f"test_nz_{flat(fn.module)}__{flat(fn.qualname)}.py"


@dataclass
class Merged:
    function_id: str
    commit_sha: str
    diff: str


@dataclass
class RunShared:
    """What every function of a run uses: the sandbox, the LLM, the bench, the trunk."""

    ctx: RunContext
    llm: LlmClient
    sandbox: Sandbox
    lane: BenchLane
    gate: QuietGate
    import_roots: list[str]
    fixed_date: bool = False
    current_fid: str | None = None  # whose bench the lane's own warnings belong to
    passed_tests: list[Path] = field(default_factory=list)  # passed on their original
    merged: list[Merged] = field(default_factory=list)

    @property
    def trunk(self) -> Path:
        return self.ctx.paths.trunk

    def roots(self, tree: Path) -> list[Path]:
        return [tree if r in ("", ".") else tree / r for r in self.import_roots]

    def lane_log(self, message: str) -> None:
        """``BenchLane(log=...)``: the lane's warnings, scoped to the current function."""
        self.ctx.log("bench", [message], level="warn", function_id=self.current_fid)


class FunctionRun:
    def __init__(self, shared: RunShared, fn: DiscoveredFunction):
        self.shared = shared
        self.ctx = shared.ctx
        self.s = shared.ctx.settings
        self.llm = shared.llm
        self.sandbox = shared.sandbox
        self.lane = shared.lane
        self.gate = shared.gate
        self.fn = fn
        self.fid = fn.function_id
        self.trunk = shared.trunk
        self.dir = shared.ctx.paths.fn(fn.function_id)
        self.cwd = self.dir / "cwd"  # tests run here, so they cannot dirty a tree
        self.test_path = self.dir / test_file_name(fn)
        self.module_source = ""
        self.test_file: TestFile | None = None
        self.original_tests_s = 0.0
        self.captured: Captured | None = None
        self.samples_preview: list[str] = []

    def roots(self, tree: Path) -> list[Path]:
        return self.shared.roots(tree)

    # -- the function ------------------------------------------------------------

    async def run(self, fs: FunctionScope) -> None:
        self.shared.current_fid = self.fid
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.module_source = (self.trunk / self.fn.file).read_text(encoding="utf-8")

        if not await self.tests():
            n = self.s.test_repairs
            fs.complete(
                "skipped_untestable",
                reason="generated tests still fail on the original after "
                f"{n} repair{'' if n == 1 else 's'}",
            )
            return
        problem = await self.capture()
        if problem:
            fs.complete("skipped_capture", reason=f"capture: {problem}")
            return

        results = await self.candidates()
        decision = self.decide(results)
        winner = next((r for r in results if r.cid == decision.winner), None)
        if winner is None:
            return self.no_winner(fs, results, decision)
        await self.merge(fs, winner)

    # -- tests -------------------------------------------------------------------

    async def tests(self) -> bool:
        """Write the tests, run them on the original twice; repair up to ``test_repairs``."""
        ctx, fid = self.ctx, self.fid
        conv = None
        failures: list[PytestFailure | str] = []
        for attempt in range(self.s.test_repairs + 1):
            failures_in = [f.nodeid if isinstance(f, PytestFailure) else f for f in failures]
            started = TestsWriteStartedData(
                kind="write" if attempt == 0 else "repair", failures_in=failures_in
            )
            async with ctx.step(
                "function.tests.write.started", started, function_id=fid, attempt=attempt
            ) as st:
                if attempt == 0:
                    out, conv = await llm_tasks.write_tests(
                        self.llm, self.fn, module_source=self.module_source
                    )
                    diagnosis = ""
                else:
                    out, conv = await llm_tasks.repair_tests(
                        self.llm, conv, self.fn, failures, attempt
                    )
                    diagnosis = out.diagnosis
                chk = static_check.validate_test_file(out.code, self.fn)
                tf = TestFile(
                    path=f"{TESTS_DIR}/{self.test_path.name}",
                    code=chk.code,
                    test_names=chk.test_names,
                    workload_test=chk.workload_test or "",
                    notes=out.notes,
                    diagnosis=diagnosis,
                )
                if not chk.ok:
                    ctx.log(
                        "orchestrator",
                        [f"test file rejected: {p}" for p in chk.problems],
                        level="warn",
                        function_id=fid,
                        attempt=attempt,
                    )
                    st.fail(None, test_file=tf)
                    failures = list(chk.problems)
                    continue
                st.ok(test_file=tf)
            self.test_path.write_text(tf.code, encoding="utf-8")

            first = await self.run_tests(attempt, repeat=0)
            if not first.ok:
                failures = first.problems
                continue
            again = await self.run_tests(attempt, repeat=1)
            if not again.ok:
                failures = again.problems
                continue
            self.test_file = tf
            self.original_tests_s = max(first.duration_s, again.duration_s)
            self.shared.passed_tests.append(self.test_path)
            return True
        return False

    async def run_tests(self, attempt: int, *, repeat: int) -> _TestRun:
        ctx, fid = self.ctx, self.fid
        scope = {"function_id": fid, "attempt": attempt}
        flaky = repeat == 1
        async with ctx.step(
            "function.tests.run.started", TestsRunStartedData(repeat=repeat), **scope
        ) as st:
            timeout = float(self.s.test_timeout_s)
            try:
                with ctx.log_sink("pytest", stream="stdout", **scope) as sink:
                    result = await run_pytest(
                        self.sandbox,
                        self.test_path,
                        roots=self.roots(self.trunk),
                        cwd=self.cwd,
                        timeout=timeout,
                        junit=self.dir / f"tests-{attempt}-{repeat}.junit.xml",
                        on_line=lambda _s, line: sink.write(line),
                    )
            except StepTimeout as exc:
                ctx.log("sandbox", [f"Timeout: {exc.message}"], level="warn", **scope)
                st.fail(exc, flaky=False)
                msg = (
                    f"the tests did not finish within {timeout:g} s on the original; keep them fast"
                )
                return _TestRun(False, [msg])
            if result.exit_code == 0:
                st.ok(result=result, flaky=False)
                return _TestRun(True, duration_s=result.duration_s)
            if flaky:
                ctx.log(
                    "orchestrator",
                    ["tests passed once and failed on the re-run: flaky"],
                    level="warn",
                    **scope,
                )
            st.fail(None, result=result, flaky=flaky)
        problems: list[PytestFailure | str] = list(result.failures) or [
            f"pytest exited with {result.exit_code}:\n{result.output_tail[-1500:]}"
        ]
        if flaky:
            problems.insert(
                0, "these tests passed once and failed on a re-run; make them deterministic"
            )
        return _TestRun(False, problems)

    # -- capture -----------------------------------------------------------------

    async def capture(self) -> str:
        """Record the inputs and reference outputs; returns why not, or ''."""
        ctx, fid = self.ctx, self.fid
        async with ctx.step("function.capture.started", function_id=fid) as st:
            try:
                cap = await capture_inputs(
                    self.sandbox,
                    self.test_path,
                    roots=self.roots(self.trunk),
                    cwd=self.cwd,
                    module=self.fn.module,
                    qualname=self.fn.qualname,
                    out_dir=self.dir / "capture",
                    timeout=float(self.s.test_timeout_s),
                )
            except (StepTimeout, SandboxError) as exc:
                ctx.log("sandbox", [f"capture: {exc.message}"], level="warn", function_id=fid)
                st.fail(exc)
                return exc.message
            d = cap.data
            fields = d.model_dump(exclude={"ok", "duration_ms", "error"})
            problem = ""
            if d.n_kept == 0:
                problem = (
                    f"none of the {d.n_calls} calls had picklable arguments"
                    if d.n_calls
                    else "the tests never called the function"
                )
            elif not d.deterministic:
                problem = "the function gave different results for the same inputs"
            if problem:
                ctx.log("sandbox", [f"capture: {problem}"], level="warn", function_id=fid)
                st.fail(None, **fields)
                return problem
            ctx.log(
                "sandbox",
                [f"captured {d.n_calls} calls ({d.n_kept} kept) while running the tests"],
                function_id=fid,
            )
            st.ok(**fields)
        self.captured = cap
        self.samples_preview = list(d.previews)
        return ""

    # -- candidates + baseline ---------------------------------------------------

    async def candidates(self) -> list[CandidateResult]:
        """A, B and C write and check while the baseline runs; benches queue behind it."""
        slot = BenchSlot()
        runs = [CandidateRun(self, cid, slot) for cid in CANDIDATES]
        async with AsyncExitStack() as stack:  # the original's worker lives until the reap
            tasks = [asyncio.create_task(r.run(), name=f"candidate-{r.cid}") for r in runs]
            try:
                await asyncio.sleep(0)  # let the writes start before the baseline
                await self.baseline(slot, stack)
                return list(await asyncio.gather(*tasks))
            finally:
                await reap(tasks)

    async def baseline(self, slot: BenchSlot, stack: AsyncExitStack) -> None:
        ctx, fid, s = self.ctx, self.fid, self.s
        assert self.captured is not None
        async with slot.lock:
            async with ctx.step("function.baseline.started", function_id=fid) as st:
                worker = await stack.enter_async_context(
                    self.lane.worker(
                        self.roots(self.trunk),
                        self.fn.module,
                        self.fn.qualname,
                        self.captured.inputs,
                        label="original",
                    )
                )
                cal = await self.lane.calibrate(worker)
                ctx.log(
                    "bench",
                    [
                        f"calibration: {cal.n_samples} samples, ~{cal.est_call_s * 1e3:.3g} ms/call"
                        f" -> {cal.calls_per_trial} calls/trial"
                    ],
                    function_id=fid,
                )
                original, cv = await self.lane.baseline(worker, cal)
                ctx.log(
                    "bench",
                    [f"baseline: {s.n_trials} trials × {cal.calls_per_trial} calls, cv {cv:.1f}%"],
                    function_id=fid,
                )
                st.ok(
                    calibration=cal, original=original, cv_pct=round(cv, 2), power=self.lane.power
                )
        slot.worker, slot.calibration, slot.original = worker, cal, original
        slot.ready.set()

    # -- decision ----------------------------------------------------------------

    def decide(self, results: list[CandidateResult]) -> FunctionDecisionData:
        from netzero.bench import stats

        rows = [
            (r.cid, r.stats, f"{r.reject}: {r.detail}" if r.reject else None, len(r.diff))
            for r in results
        ]
        rule = DecisionRule(alpha=self.s.alpha, min_effect_pct=self.s.min_effect_pct)
        decision = stats.decide(rows, rule)
        for cid, st in stats.with_holm(rows, decision).items():
            next(r for r in results if r.cid == cid).stats = st
        self.ctx.emit("function.decision", decision, function_id=self.fid)
        return decision

    def no_winner(
        self, fs: FunctionScope, results: list[CandidateResult], decision: FunctionDecisionData
    ) -> None:
        eligible = [e for e in decision.ranking if e.status == "eligible"]
        if not eligible:
            parts = ", ".join(f"{r.cid} {r.reject}" for r in results)
            fs.complete("all_rejected", reason=f"all {len(results)} candidates rejected: {parts}")
            return
        best = eligible[0]
        assert best.delta_pct is not None and best.delta_ci_pct is not None
        fs.complete(
            "no_significant_win",
            reason=(
                f"best: {best.candidate_id} {pct(best.delta_pct)}% ({ci_text(best.delta_ci_pct)}), "
                f"{fmt_p(best.p_holm if best.p_holm is not None else 1.0, 'p_holm')}: {best.reason}"
            ),
        )

    # -- merge -------------------------------------------------------------------

    async def merge(self, fs: FunctionScope, winner: CandidateResult) -> None:
        """Splice the winner into the trunk, re-run every passing test file and this
        function's differential; commit, or reset and report ``reverted``."""
        ctx, fid, cid = self.ctx, self.fid, winner.cid
        stats, code = winner.stats, winner.code
        assert stats is not None and code is not None
        procs = ctx.procs
        headline = (
            f"{pct(stats.delta_pct)}% CO₂/call ({ci_text(stats.delta_ci_pct)}), "
            f"{fmt_p(stats.p_holm if stats.p_holm is not None else stats.p_value, 'p_holm')}"
        )
        why = ""
        suite: PytestResult | None = None
        diff: DiffCheckResult | None = None
        async with ctx.step("function.merge.started", function_id=fid) as st:
            ctx.log(
                "orchestrator",
                [f"merging {cid} into the trunk; running the full suite"],
                function_id=fid,
            )
            target = self.trunk / self.fn.file
            try:
                current = target.read_text(encoding="utf-8")
                target.write_text(
                    apply_rewrite(current, self.fn.qualname, code.code, code.new_imports),
                    encoding="utf-8",
                )
            except (SpliceError, SyntaxError, ValueError) as exc:
                why = f"cannot splice into the current trunk: {exc}"
            if not why:
                suite, why = await self.run_suite()
            if not why:
                diff, why = await self.merged_differential()
            if why:
                await git(["reset", "--hard", "-q"], cwd=self.trunk, procs=procs)
                ctx.log("orchestrator", [f"reverting {cid}: {why}"], level="warn", function_id=fid)
                st.fail(ValidationFailed(why), reverted=True, suite=suite, differential=diff)
            else:
                message = f"netzero: optimize {self.fn.qualname} ({pct(stats.delta_pct)}%)"
                sha = await commit_files(
                    self.trunk, message, [self.fn.file], procs, fixed_date=self.shared.fixed_date
                )
                diff_path = self.dir / "merge.diff"
                await git(
                    ["diff", "--no-color", f"--output={diff_path}", f"{sha}~1", sha],
                    cwd=self.trunk,
                    procs=procs,
                )
                merge_diff = diff_path.read_text(encoding="utf-8")
                self.shared.merged.append(Merged(fid, sha, merge_diff))
                ctx.log("orchestrator", [f"merged {cid} as {sha[:7]}"], function_id=fid)
                st.ok(suite=suite, differential=diff, commit_sha=sha, diff=merge_diff)
        if why:
            fs.complete(
                "reverted",
                winner=cid,
                delta_pct=stats.delta_pct,
                delta_ci_pct=stats.delta_ci_pct,
                reason=f"{cid} reverted after merge: {why}",
            )
            return
        fs.complete(
            "accepted",
            winner=cid,
            delta_pct=stats.delta_pct,
            delta_ci_pct=stats.delta_ci_pct,
            g_saved_per_1m_calls=stats.g_saved_per_1m_calls,
            kwh_saved_per_1m_calls=stats.kwh_saved_per_1m_calls,
            diff=merge_diff,
            reason=f"{cid}: {headline}",
        )

    async def run_suite(self) -> tuple[PytestResult | None, str]:
        """Every generated test file that passed on its original, on the merged trunk."""
        ctx, fid = self.ctx, self.fid
        results: list[PytestResult] = []
        timeout = float(self.s.test_timeout_s)
        for i, path in enumerate(self.shared.passed_tests):
            try:
                with ctx.log_sink("pytest", stream="stdout", function_id=fid) as sink:
                    results.append(
                        await run_pytest(
                            self.sandbox,
                            path,
                            roots=self.roots(self.trunk),
                            cwd=self.cwd,
                            timeout=timeout,
                            junit=self.dir / f"merge-{i}.junit.xml",
                            on_line=lambda _s, line: sink.write(line),
                            label=f"pytest merge {path.name}",
                        )
                    )
            except StepTimeout:
                suite = combine(results) if results else None
                return suite, f"{path.name} did not finish within {timeout:g} s on the merged trunk"
        suite = combine(results)
        if suite.exit_code != 0:
            first = suite.failures[0] if suite.failures else None
            what = f"{first.nodeid} failed: {first.message}" if first else "the suite failed"
            return suite, f"{what} on the merged trunk"
        return suite, ""

    async def merged_differential(self) -> tuple[DiffCheckResult | None, str]:
        assert self.captured is not None
        timeout = float(self.s.test_timeout_s)
        try:
            diff = await differential(
                self.sandbox,
                roots=self.roots(self.trunk),
                cwd=self.cwd,
                module=self.fn.module,
                qualname=self.fn.qualname,
                inputs=self.captured.inputs,
                refs=self.captured.refs,
                timeout=timeout,
            )
        except (StepTimeout, SandboxError) as exc:
            return None, f"differential on the merged trunk: {exc.message}"
        wrong = [m for m in diff.mismatches if m.kind != "slowdown"]
        diff = diff.model_copy(update={"ok": not wrong, "mismatches": wrong})
        if wrong:
            return diff, (
                f"{len(wrong)} of {diff.n_samples} captured calls differ on the merged trunk"
            )
        return diff, ""


@dataclass
class _TestRun:
    ok: bool
    problems: list[PytestFailure | str] = field(default_factory=list)
    duration_s: float = 0.0


def combine(results: list[PytestResult]) -> PytestResult:
    """Several pytest runs as one: counts add up, the first failing run's exit code wins."""
    failing = [r for r in results if r.exit_code != 0]
    return PytestResult(
        exit_code=failing[0].exit_code if failing else 0,
        passed=sum(r.passed for r in results),
        failed=sum(r.failed for r in results),
        errors=sum(r.errors for r in results),
        skipped=sum(r.skipped for r in results),
        duration_s=round(sum(r.duration_s for r in results), 3),
        failures=[f for r in results for f in r.failures],
        output_tail=(failing[0] if failing else results[-1]).output_tail if results else "",
    )


async def optimize_function(
    shared: RunShared, fn: DiscoveredFunction, index: int, total: int
) -> None:
    ctx, fid = shared.ctx, fn.function_id
    async with ctx.function(fn.info(), index=index, total=total) as fs:
        ctx.log("orchestrator", [f"[{index + 1}/{total}] {fid}"], function_id=fid)
        await FunctionRun(shared, fn).run(fs)


__all__ = ["FunctionRun", "Merged", "RunShared", "combine", "optimize_function", "test_file_name"]
