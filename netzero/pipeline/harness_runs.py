"""Run the generated tests, the input capture and the differential check in the sandbox.

Everything goes through ``_netzero_harness.launch`` (rlimits) in the target
venv with ``sandbox_env``. Which tree is under test (the trunk or a candidate
worktree) is decided only by ``roots``, the import roots put on ``PYTHONPATH``
after the harness; the test file itself lives outside every tree.

Failing tests are data (``PytestResult``). Infrastructure failures raise:
``StepTimeout`` when a process runs out of time, ``SandboxError`` when a
harness script dies without its record or reports an error.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netzero.errors import SandboxError, StepTimeout
from netzero.events import CaptureCompletedData, DiffCheckResult, Mismatch, PytestResult
from netzero.pipeline.env import HARNESS_PACKAGE
from netzero.pipeline.pytest_result import parse_junit
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import sandbox_env, venv_python
from netzero.sandbox.harness.common import MARK, parse_record
from netzero.sandbox.procs import ProcRegistry

FSIZE_MB = 512  # larger than any inputs/refs file the caps allow
NOFILE = 4096
CPU_GRACE_S = 5


@dataclass(frozen=True)
class Sandbox:
    venv: Path
    harness_root: Path  # holds ``_netzero_harness/`` (see ``env.install_harness``)
    home: Path
    tmp: Path
    procs: ProcRegistry | None = None

    @property
    def python(self) -> Path:
        return venv_python(self.venv)

    @property
    def pytest_ini(self) -> Path:
        return self.harness_root / HARNESS_PACKAGE / "pytest.ini"

    def env(self, roots: Sequence[Path], extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Harness first, then the tree's import roots; no auto-loaded pytest plugins."""
        return sandbox_env(
            venv=self.venv,
            home=self.home,
            tmp=self.tmp,
            pythonpath=[self.harness_root, *roots],
            extra={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **(extra or {})},
        )

    def launch_argv(self, timeout: float, *target: str) -> list[str]:
        """``-P`` keeps the cwd off ``sys.path``; the CPU limit backs up the wall timeout."""
        cpu = math.ceil(timeout) + CPU_GRACE_S
        limits = ["--cpu", str(cpu), "--fsize-mb", str(FSIZE_MB), "--nofile", str(NOFILE)]
        return [str(self.python), "-P", "-m", f"{HARNESS_PACKAGE}.launch", *limits, *target]


@dataclass(frozen=True)
class Captured:
    data: CaptureCompletedData
    inputs: Path
    refs: Path
    pytest: PytestResult


def _without_records(on_line: runner.LineCallback | None) -> runner.LineCallback | None:
    if on_line is None:
        return None
    return lambda stream, line: None if line.startswith(MARK) else on_line(stream, line)


def _output(r: runner.ProcResult) -> str:
    lines = (r.stdout + "\n" + r.stderr).splitlines()
    return "\n".join(line for line in lines if not line.startswith(MARK))


def _last_record(r: runner.ProcResult, key: str) -> dict[str, Any] | None:
    """The last record on stdout that has ``key`` or ``error``."""
    found = None
    for line in r.stdout.splitlines():
        rec = parse_record(line)
        if rec is not None and (key in rec or "error" in rec):
            found = rec
    return found


def _require_record(r: runner.ProcResult, key: str, what: str) -> dict[str, Any]:
    rec = _last_record(r, key)
    if rec is not None and "error" in rec:
        raise SandboxError(f"{what}: {rec['error']}", detail=r.tail(30))
    if rec is None:
        raise SandboxError(
            f"{what} ended without a result (exit code {r.returncode})", detail=r.tail(30)
        )
    return rec


async def _run(
    sb: Sandbox,
    argv: list[str],
    *,
    roots: Sequence[Path],
    cwd: Path,
    timeout: float,
    label: str,
    on_line: runner.LineCallback | None,
    extra_env: Mapping[str, str] | None = None,
) -> runner.ProcResult:
    r = await runner.run(
        argv,
        cwd=cwd,
        env=sb.env(roots, extra_env),
        timeout=timeout,
        procs=sb.procs,
        label=label,
        on_line=_without_records(on_line),
    )
    if r.timed_out:
        raise StepTimeout(f"{label} timed out after {timeout:g} s", detail=r.tail(30))
    return r


async def _pytest(
    sb: Sandbox,
    test_file: Path,
    *,
    roots: Sequence[Path],
    cwd: Path,
    timeout: float,
    junit: Path,
    on_line: runner.LineCallback | None,
    capture: tuple[str, str, Path] | None,
    label: str,
) -> tuple[PytestResult, runner.ProcResult]:
    test_file, junit = test_file.absolute(), junit.absolute()  # the child's cwd is not ours
    test_dir = str(test_file.parent)
    args = [str(test_file), "-c", str(sb.pytest_ini), "--rootdir", test_dir]
    args += ["--confcutdir", test_dir, f"--junitxml={junit}"]
    extra: dict[str, str] = {}
    if capture is not None:
        module, qualname, out = capture
        args += ["-p", f"{HARNESS_PACKAGE}.capture_plugin"]
        extra = {
            "NZ_CAPTURE_MODULE": module,
            "NZ_CAPTURE_QUALNAME": qualname,
            "NZ_CAPTURE_OUT": str(out.absolute()),
        }
    junit.unlink(missing_ok=True)  # a stale report must never pass for this run's
    r = await _run(
        sb,
        sb.launch_argv(timeout, "-m", "pytest", *args),
        roots=roots,
        cwd=cwd,
        timeout=timeout,
        label=label,
        on_line=on_line,
        extra_env=extra,
    )
    result = parse_junit(
        junit,
        exit_code=r.returncode,
        duration_s=r.duration_s,
        output=_output(r),
        nodeid=test_file.name,
    )
    return result, r


async def run_pytest(
    sb: Sandbox,
    test_file: Path,
    *,
    roots: Sequence[Path],
    cwd: Path,
    timeout: float,
    junit: Path,
    on_line: runner.LineCallback | None = None,
    capture: tuple[str, str, Path] | None = None,
    label: str = "pytest",
) -> PytestResult:
    """Run one test file against the tree given by ``roots``; ``capture`` is
    ``(module, qualname, inputs.pkl)`` to record the target's inputs."""
    result, _ = await _pytest(
        sb,
        test_file,
        roots=roots,
        cwd=cwd,
        timeout=timeout,
        junit=junit,
        on_line=on_line,
        capture=capture,
        label=label,
    )
    return result


async def _diffcheck(
    sb: Sandbox,
    mode: str,
    *,
    roots: Sequence[Path],
    cwd: Path,
    module: str,
    qualname: str,
    inputs: Path,
    refs: Path,
    timeout: float,
    on_line: runner.LineCallback | None,
    sample_timeout: float | None = None,
) -> dict[str, Any]:
    argv = sb.launch_argv(
        timeout,
        *("-m", f"{HARNESS_PACKAGE}.diffcheck", mode, "--module", module, "--qualname", qualname),
        *("--inputs", str(inputs.absolute()), "--refs", str(refs.absolute())),
    )
    if sample_timeout is not None:
        argv += ["--sample-timeout", str(sample_timeout)]
    label = f"diffcheck {mode}"
    r = await _run(sb, argv, roots=roots, cwd=cwd, timeout=timeout, label=label, on_line=on_line)
    return _require_record(r, "n_samples", label)


def _diff_result(rec: dict[str, Any]) -> DiffCheckResult:
    try:
        return DiffCheckResult(
            ok=rec["ok"],
            n_samples=rec["n_samples"],
            mismatches=[Mismatch(**m) for m in rec["mismatches"]],
            slowdown_ratio=rec["slowdown_ratio"],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SandboxError(f"diffcheck check: malformed result ({e})") from e


async def capture_inputs(
    sb: Sandbox,
    test_file: Path,
    *,
    roots: Sequence[Path],
    cwd: Path,
    module: str,
    qualname: str,
    out_dir: Path,
    timeout: float,
    on_line: runner.LineCallback | None = None,
    sample_timeout: float | None = None,
) -> Captured:
    """Tests with the capture plugin -> ``inputs.pkl``; one reference run -> ``refs.pkl``;
    a second run on the same tree checks the function is deterministic on them.

    Each of the three processes gets ``timeout``; ``sample_timeout`` overrides
    diffcheck's per-sample limit. Zero usable samples is a result
    (``data.n_kept == 0``), not an error.
    """
    t0 = time.perf_counter()
    out_dir = out_dir.absolute()
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs, refs = out_dir / "inputs.pkl", out_dir / "refs.pkl"
    inputs.unlink(missing_ok=True)
    refs.unlink(missing_ok=True)
    result, r = await _pytest(
        sb,
        test_file,
        roots=roots,
        cwd=cwd,
        timeout=timeout,
        junit=out_dir / "capture.junit.xml",
        on_line=on_line,
        capture=(module, qualname, inputs),
        label="capture",
    )
    cap = _require_record(r, "n_calls", "capture")
    if not inputs.is_file():
        raise SandboxError("capture wrote no inputs file", detail=r.tail(30))
    shared: dict[str, Any] = dict(
        roots=roots,
        cwd=cwd,
        module=module,
        qualname=qualname,
        inputs=inputs,
        refs=refs,
        timeout=timeout,
        on_line=on_line,
        sample_timeout=sample_timeout,
    )
    ref = await _diffcheck(sb, "record", **shared)
    deterministic = True
    if ref["n_usable"]:
        again = _diff_result(await _diffcheck(sb, "check", **shared))
        deterministic = all(m.kind == "slowdown" for m in again.mismatches)
    data = CaptureCompletedData(
        ok=True,
        duration_ms=round((time.perf_counter() - t0) * 1000),
        n_calls=cap["n_calls"],
        n_kept=ref["n_usable"],
        n_unpicklable=cap["n_unpicklable"],
        mutates_args=ref["mutates_args"],
        raises=ref["raises"],
        deterministic=deterministic,
        total_bytes=cap["total_bytes"],
        previews=ref["previews"],
    )
    return Captured(data=data, inputs=inputs, refs=refs, pytest=result)


async def differential(
    sb: Sandbox,
    *,
    roots: Sequence[Path],
    cwd: Path,
    module: str,
    qualname: str,
    inputs: Path,
    refs: Path,
    timeout: float,
    on_line: runner.LineCallback | None = None,
    sample_timeout: float | None = None,
) -> DiffCheckResult:
    """Replay the captured inputs on the tree given by ``roots`` against ``refs.pkl``;
    ``sample_timeout`` overrides the per-sample limit (diffcheck's default is 10 s)."""
    rec = await _diffcheck(
        sb,
        "check",
        roots=roots,
        cwd=cwd,
        module=module,
        qualname=qualname,
        inputs=inputs,
        refs=refs,
        timeout=timeout,
        on_line=on_line,
        sample_timeout=sample_timeout,
    )
    return _diff_result(rec)
