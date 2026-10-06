"""One rewrite candidate (A, B or C): write -> check (-> repair -> check) -> bench.

Each candidate runs as its own task; the three overlap each other and the
baseline. Checks share the quiet gate, benches take it exclusively and go
through the function's bench slot one at a time, baseline first.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from netzero.errors import BenchError, LlmError, NetzeroError, SandboxError, StepTimeout
from netzero.events import (
    BenchStats,
    CandidateBenchQueuedData,
    CandidateBenchStartedData,
    CandidateCode,
    CandidateId,
    CandidateRejectedData,
    CandidateWriteStartedData,
    DiffCheckResult,
    MeasureStats,
    PytestResult,
    RejectReason,
    StaticCheck,
)
from netzero.events import Calibration as CalibrationData
from netzero.llm import tasks as llm_tasks
from netzero.pipeline import static_check
from netzero.pipeline.common import STRATEGY_HINTS, ci_text, fmt_p, pct, unified_diff
from netzero.pipeline.harness_runs import differential, run_pytest
from netzero.pipeline.splice import SpliceError, splice_source

if TYPE_CHECKING:
    from netzero.bench.lane import BenchWorker
    from netzero.pipeline.function_flow import FunctionRun

TREE_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache")
CHECK_TIMEOUT_FLOOR_S = 10.0
CHECK_TIMEOUT_FACTOR = 5.0
IDENTICAL = "the code is identical to the original"  # static_check's wording


# ---------------------------------------------------------------------------
# Module text
# ---------------------------------------------------------------------------


def add_imports(source: str, imports: Sequence[str]) -> str:
    """Insert import lines the module lacks after its leading import block."""
    have = {ln.strip() for ln in source.splitlines()}
    new = [imp.strip() for imp in imports if imp.strip() and imp.strip() not in have]
    if not new:
        return source
    tree = ast.parse(source)
    at = 0  # insert after this line (1-based end line), 0 = top of file
    for i, node in enumerate(tree.body):
        is_doc = (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if is_doc or isinstance(node, ast.Import | ast.ImportFrom):
            at = node.end_lineno or at
            continue
        break
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    block = [f"{imp}\n" for imp in dict.fromkeys(new)]
    return "".join(lines[:at] + block + lines[at:])


def apply_rewrite(source: str, qualname: str, code: str, new_imports: Sequence[str]) -> str:
    """The module with ``qualname`` replaced by ``code``; raises ``SpliceError``/``SyntaxError``."""
    return add_imports(splice_source(source, {qualname: code}), new_imports)


def normalize(code: str) -> str:
    """Model output as the checks see it: no markdown fences, dedented."""
    return textwrap.dedent(static_check.strip_fences(code)).strip("\n") + "\n"


def static_reason(problems: Sequence[str]) -> RejectReason:
    if any(p.startswith("syntax error") or p.startswith("cannot splice") for p in problems):
        return "syntax"
    if list(problems) == [IDENTICAL]:
        return "identical"
    return "static_rule"


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, symlinks=True, ignore=TREE_IGNORE)


# ---------------------------------------------------------------------------
# Candidate state
# ---------------------------------------------------------------------------


class BenchSlot:
    """The function's bench: the original worker and calibration, one bench at a time."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.waiting = 0
        self.worker: BenchWorker | None = None  # the original's, open until the function ends
        self.calibration: CalibrationData | None = None
        self.original: MeasureStats | None = None


@dataclass
class CandidateResult:
    cid: CandidateId
    attempt: int = 0
    code: CandidateCode | None = None
    stats: BenchStats | None = None
    reject: RejectReason | None = None
    detail: str = ""

    @property
    def diff(self) -> str:
        return self.code.diff if self.code else ""


@dataclass
class Verdict:
    """The outcome of one check: ``reason`` is None when the candidate passed."""

    reason: RejectReason | None = None
    detail: str = ""
    problems: list[str] = field(default_factory=list)  # what the repair prompt is told


def _failure_lines(result: PytestResult, limit: int = 5) -> list[str]:
    lines = [
        f"{f.nodeid}: {f.message}" + (f"\n{f.tb_tail}" if f.tb_tail else "")
        for f in result.failures[:limit]
    ]
    return lines or [f"pytest exited with {result.exit_code}:\n{result.output_tail[-1500:]}"]


def _mismatch_lines(diff: DiffCheckResult) -> list[str]:
    out = []
    for m in diff.mismatches:
        where = f" at {m.path}" if m.path else ""
        out.append(f"sample {m.sample_idx}: {m.kind}{where}: expected {m.expected}, got {m.actual}")
    return out


# ---------------------------------------------------------------------------
# One candidate
# ---------------------------------------------------------------------------


class CandidateRun:
    def __init__(self, fr: FunctionRun, cid: CandidateId, bench: BenchSlot):
        self.fr = fr
        self.ctx = fr.ctx
        self.s = fr.ctx.settings
        self.cid = cid
        self.bench = bench
        self.tree = fr.ctx.paths.candidate(fr.fid, cid)
        self.conv = None

    def scope(self, attempt: int) -> dict:
        return {"function_id": self.fr.fid, "candidate_id": self.cid, "attempt": attempt}

    async def run(self) -> CandidateResult:
        res = CandidateResult(self.cid)
        problems: list[str] = []
        attempt = 0
        while True:
            res.attempt = attempt
            code, error = await self.write(attempt, problems)
            if code is None:
                return self.reject(res, "llm_error", error)
            res.code = code
            verdict = await self.check(attempt, code)
            if verdict.reason is None:
                break
            if attempt < self.s.candidate_repairs:
                problems, attempt = verdict.problems, attempt + 1
                continue
            return self.reject(res, verdict.reason, verdict.detail)

        await self.bench.ready.wait()
        position = self.bench.waiting
        self.bench.waiting += 1
        self.ctx.emit(
            "candidate.bench.queued",
            CandidateBenchQueuedData(position=position),
            **self.scope(attempt),
        )
        try:
            await self.bench.lock.acquire()
        finally:
            self.bench.waiting -= 1
        try:
            stats, error = await self.measure(attempt)
        finally:
            self.bench.lock.release()
        if stats is None:
            return self.reject(res, "bench_failed", error)
        res.stats = stats
        if not stats.sanity_ok:
            detail = (
                f"CO₂ Δ {pct(stats.delta_pct)}% disagrees with CPU-time Δ "
                f"{pct(stats.cpu_time_delta_pct)}%"
            )
            return self.reject(res, "measurement_inconsistent", detail)
        return res

    def reject(self, res: CandidateResult, reason: RejectReason, detail: str) -> CandidateResult:
        res.reject, res.detail = reason, detail
        scope = self.scope(res.attempt)
        self.ctx.emit(
            "candidate.rejected", CandidateRejectedData(reason=reason, detail=detail), **scope
        )
        self.ctx.log(
            "orchestrator", [f"{res.cid} rejected ({reason}): {detail}"], level="warn", **scope
        )
        return res

    # -- write -------------------------------------------------------------------

    async def write(self, attempt: int, problems: list[str]) -> tuple[CandidateCode | None, str]:
        fr, scope = self.fr, self.scope(attempt)
        started = CandidateWriteStartedData(
            kind="write" if attempt == 0 else "repair", strategy_hint=STRATEGY_HINTS[self.cid]
        )
        async with self.ctx.step("candidate.write.started", started, **scope) as st:
            try:
                if attempt == 0:
                    out, self.conv = await llm_tasks.write_candidate(
                        fr.llm,
                        fr.fn,
                        self.cid,
                        module_source=fr.module_source,
                        test_code=fr.test_file.code,
                        samples_preview=fr.samples_preview,
                    )
                    diagnosis = ""
                else:
                    out, self.conv = await llm_tasks.repair_candidate(
                        fr.llm, self.conv, fr.fn, self.cid, "\n\n".join(problems), attempt=attempt
                    )
                    diagnosis = out.diagnosis
            except LlmError as exc:
                st.fail(exc)
                return None, exc.message
            text = normalize(out.code)
            try:
                after = apply_rewrite(fr.module_source, fr.fn.qualname, text, out.new_imports)
                diff = unified_diff(fr.fn.file, fr.module_source, after)
            except (SpliceError, SyntaxError, ValueError):
                diff = unified_diff(fr.fn.file, fr.fn.source, text)  # the check says why
            code = CandidateCode(
                code=text,
                new_imports=list(out.new_imports),
                strategy=out.strategy,
                rationale=out.rationale,
                diff=diff,
                diagnosis=diagnosis,
            )
            st.ok(candidate=code)
        return code, ""

    # -- check -------------------------------------------------------------------

    async def check(self, attempt: int, code: CandidateCode) -> Verdict:
        fr, ctx, scope = self.fr, self.ctx, self.scope(attempt)
        async with ctx.step("candidate.check.started", **scope) as st:
            chk = static_check.check_candidate(
                fr.fn.source,
                code.code,
                fr.fn,
                new_imports=code.new_imports,
                module_source=fr.module_source,
            )
            static = chk.static
            if static.ok and (tree_error := await self.prepare_tree(code)):
                static = StaticCheck(ok=False, problems=[tree_error])
            if not static.ok:
                ctx.log("sandbox", [f"static: {p}" for p in static.problems], level="warn", **scope)
                st.fail(None, static=static)
                problems = list(static.problems)
                return Verdict(static_reason(problems), "; ".join(problems), problems)

            timeout = self.check_timeout()
            roots = fr.roots(self.tree)
            async with fr.gate.shared():
                try:
                    with ctx.log_sink("pytest", stream="stdout", **scope) as sink:
                        tests = await run_pytest(
                            fr.sandbox,
                            fr.test_path,
                            roots=roots,
                            cwd=fr.cwd,
                            timeout=timeout,
                            junit=fr.dir / f"check-{self.cid}-{attempt}.junit.xml",
                            on_line=lambda _s, line: sink.write(line),
                            label=f"pytest {self.cid}",
                        )
                except StepTimeout as exc:
                    return self._timed_out(st, exc, timeout, scope, static=static)
                if tests.exit_code != 0:
                    lines = _failure_lines(tests)
                    ctx.log("pytest", [ln.splitlines()[0] for ln in lines], level="warn", **scope)
                    st.fail(None, static=static, tests=tests)
                    first = tests.failures[0] if tests.failures else None
                    detail = f"{first.nodeid} failed: {first.message}" if first else "tests failed"
                    return Verdict("tests_failed", detail, lines)
                try:
                    diff = await differential(
                        fr.sandbox,
                        roots=roots,
                        cwd=fr.cwd,
                        module=fr.fn.module,
                        qualname=fr.fn.qualname,
                        inputs=fr.captured.inputs,
                        refs=fr.captured.refs,
                        timeout=timeout,
                    )
                except StepTimeout as exc:
                    return self._timed_out(st, exc, timeout, scope, static=static, tests=tests)
                except SandboxError as exc:
                    st.fail(exc, static=static, tests=tests)
                    return Verdict("differential_mismatch", exc.message, [exc.message])
            if not diff.ok:
                lines = _mismatch_lines(diff)
                ctx.log("sandbox", [f"differential: {ln}" for ln in lines], level="warn", **scope)
                st.fail(None, static=static, tests=tests, differential=diff)
                slow_only = all(m.kind == "slowdown" for m in diff.mismatches)
                reason: RejectReason = "timeout" if slow_only else "differential_mismatch"
                return Verdict(reason, lines[0] if lines else "outputs differ", lines)
            n = diff.n_samples
            ctx.log("sandbox", [f"differential: {n}/{n} samples match"], **scope)
            st.ok(static=static, tests=tests, differential=diff)
        return Verdict()

    def _timed_out(self, st, exc: StepTimeout, timeout: float, scope: dict, **fields) -> Verdict:
        msg = f"did not finish within {timeout:g} s; process group killed"
        self.ctx.log("sandbox", [f"Timeout: {msg}"], level="warn", **scope)
        st.fail(exc, **fields)
        return Verdict("timeout", msg, [f"the tests or the captured calls {msg}: it is too slow"])

    def check_timeout(self) -> float:
        base = self.fr.original_tests_s * CHECK_TIMEOUT_FACTOR
        return min(float(self.s.test_timeout_s), max(CHECK_TIMEOUT_FLOOR_S, base))

    async def prepare_tree(self, code: CandidateCode) -> str:
        """Copy the trunk once, then splice this attempt's code; returns a problem or ''."""
        fr = self.fr
        if not self.tree.exists():
            await asyncio.to_thread(copy_tree, fr.trunk, self.tree)
        try:
            after = apply_rewrite(fr.module_source, fr.fn.qualname, code.code, code.new_imports)
        except (SpliceError, SyntaxError, ValueError) as exc:
            return f"cannot splice the rewrite into {fr.fn.file}: {exc}"
        (self.tree / fr.fn.file).write_text(after, encoding="utf-8")
        return ""

    # -- bench -------------------------------------------------------------------

    async def measure(self, attempt: int) -> tuple[BenchStats | None, str]:
        fr, ctx, scope, slot = self.fr, self.ctx, self.scope(attempt), self.bench
        cal = slot.calibration
        assert cal is not None and slot.worker is not None
        started = CandidateBenchStartedData(
            calls_per_trial=cal.calls_per_trial, n_trials=self.s.n_trials
        )
        async with ctx.step("candidate.bench.started", started, **scope) as st:
            try:
                if not slot.worker.running:  # an earlier bench timed out or crashed it
                    ctx.log(
                        "bench",
                        [f"{self.cid}: the original's bench worker had stopped; restarting it"],
                        level="warn",
                        **scope,
                    )
                    await slot.worker.restart()
                async with fr.lane.worker(
                    fr.roots(self.tree),
                    fr.fn.module,
                    fr.fn.qualname,
                    fr.captured.inputs,
                    label=f"candidate {self.cid}",
                ) as worker:
                    stats = await fr.lane.compare(slot.worker, worker, cal)
            except (BenchError, NetzeroError) as exc:
                ctx.log(
                    "bench", [f"{self.cid}: bench failed: {exc.message}"], level="warn", **scope
                )
                st.fail(exc)
                return None, exc.message
            ctx.log(
                "bench",
                [
                    f"{self.cid}: Δ {pct(stats.delta_pct)}% CO₂/call "
                    f"({ci_text(stats.delta_ci_pct)}), {fmt_p(stats.p_value)}"
                ],
                **scope,
            )
            st.ok(stats=stats)
        return stats, ""
