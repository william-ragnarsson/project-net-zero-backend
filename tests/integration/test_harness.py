"""The sandbox harness end to end: ``launch``, generated tests, input capture and the
differential check, run as real subprocesses in this venv (standing in for the target's)."""

from __future__ import annotations

import asyncio
import json
import pickle
import signal
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest

from netzero.errors import SandboxError, StepTimeout
from netzero.events import DiffCheckResult
from netzero.pipeline.env import install_harness
from netzero.pipeline.harness_runs import (
    FSIZE_MB,
    Captured,
    Sandbox,
    capture_inputs,
    differential,
    run_pytest,
)
from netzero.sandbox import runner
from netzero.sandbox.harness.common import MARK, load_samples
from netzero.sandbox.procs import ProcRegistry

pytestmark = pytest.mark.slow

FUNCS = """
import random
import time


def add(a, b):
    return a + b


def fact(n):
    return 1 if n <= 1 else n * fact(n - 1)


def sort_inplace(xs):
    xs.sort()
    return len(xs)


def smallest(xs):
    return sorted(xs)[0]


def checked_sqrt(x):
    if x < 0:
        raise ValueError("negative")
    return x ** 0.5


def jitter(x):
    return x + random.random()


def apply(fn, x):
    return fn(x)


def slow_square(x):
    time.sleep(0.003)
    return x * x


class Scaler:
    def __init__(self, k):
        self.k = k

    def scale(self, x):
        return self.k * x

    @staticmethod
    def double(x):
        return 2 * x

    @classmethod
    def make(cls, k):
        return cls(k)
"""

MODULE = "pkg.funcs"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def make_tree(root: Path, extra: str = "") -> Path:
    """A repo with ``pkg.funcs``; ``extra`` is appended, so it redefines functions."""
    write(root / "pkg" / "__init__.py", "from pkg.funcs import add\n")
    write(root / "pkg" / "funcs.py", FUNCS + "\n\n" + textwrap.dedent(extra))
    return root


def make_sandbox(base: Path) -> Sandbox:
    (base / "home").mkdir(parents=True)
    (base / "tmp").mkdir()
    return Sandbox(
        venv=Path(sys.prefix),
        harness_root=install_harness(base / "harness"),
        home=base / "home",
        tmp=base / "tmp",
        procs=ProcRegistry(base / "procs.jsonl"),
    )


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def sb(tmp_path: Path) -> Sandbox:
    return make_sandbox(tmp_path / "sb")


@pytest.fixture
def orig(tmp_path: Path) -> Path:
    return make_tree(tmp_path / "orig")


# --- launch ------------------------------------------------------------------------

PROBE = """
import json, os, resource, sys
print(json.dumps({
    "argv": sys.argv,
    "name": __name__,
    "path0": sys.path[0],
    "cwd_on_path": os.getcwd() in sys.path or "" in sys.path,
    "cpu": resource.getrlimit(resource.RLIMIT_CPU),
    "fsize": resource.getrlimit(resource.RLIMIT_FSIZE),
    "nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
}))
"""


async def _probe(sb: Sandbox, tmp_path: Path, *target: str, roots: list[Path]) -> dict:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    r = await runner.run(
        sb.launch_argv(10, *target), cwd=work, env=sb.env(roots), timeout=30, procs=sb.procs
    )
    assert r.returncode == 0, r.tail(20)
    return json.loads(r.stdout.splitlines()[-1])


async def test_launch_module_sets_limits_and_argv(sb: Sandbox, tmp_path: Path):
    mods = tmp_path / "mods"
    write(mods / "nz_probe.py", PROBE)
    out = await _probe(sb, tmp_path, "-m", "nz_probe", "a", "--b", roots=[mods])
    assert out["name"] == "__main__"
    assert out["argv"] == [str(mods / "nz_probe.py"), "a", "--b"]
    assert out["cwd_on_path"] is False  # -P
    assert out["cpu"] == [15, 17]  # ceil(10) + grace; hard is the SIGKILL fallback
    assert out["fsize"] == [FSIZE_MB * 1024 * 1024] * 2
    assert out["nofile"][0] <= 4096 and out["nofile"][1] <= 4096


async def test_launch_script_runs_like_python_script(sb: Sandbox, tmp_path: Path):
    script = write(tmp_path / "scripts" / "probe.py", PROBE)
    out = await _probe(sb, tmp_path, str(script), "x", roots=[])
    assert out["argv"] == [str(script), "x"]
    assert out["name"] == "__main__"
    assert out["path0"] == str(script.parent)


async def test_launch_cpu_limit_stops_a_busy_loop(sb: Sandbox, tmp_path: Path):
    script = write(tmp_path / "spin.py", "while True:\n    pass\n")
    argv = [str(sb.python), "-P", "-m", "_netzero_harness.launch", "--cpu", "1", str(script)]
    r = await runner.run(argv, cwd=tmp_path, env=sb.env([]), timeout=30, procs=sb.procs)
    assert not r.timed_out
    assert r.returncode in (-signal.SIGXCPU, -signal.SIGKILL)
    assert r.duration_s < 10


async def test_launch_bad_usage_exits_with_usage(sb: Sandbox, tmp_path: Path):
    argv = [str(sb.python), "-P", "-m", "_netzero_harness.launch", "--cpu", "x"]
    r = await runner.run(argv, cwd=tmp_path, env=sb.env([]), timeout=30)
    assert r.returncode == 1 and "usage: launch" in r.stderr


# --- run_pytest --------------------------------------------------------------------

MIXED_TESTS = """
import pytest
from pkg.funcs import add


def test_pass():
    assert add(1, 2) == 3


def test_fail():
    assert add(1, 2) == 4


@pytest.mark.skip(reason="nah")
def test_skip():
    pass


class TestK:
    @pytest.mark.parametrize("x", [1, 2])
    def test_param(self, x):
        assert add(x, 0) == 1
"""


async def run(sb: Sandbox, test_file: Path, tree: Path, **kw):
    junit = test_file.parent / "junit.xml"
    return await run_pytest(sb, test_file, roots=[tree], cwd=tree, timeout=60, junit=junit, **kw)


async def test_run_pytest_counts_and_failures(sb: Sandbox, orig: Path, tmp_path: Path):
    lines: list[tuple[str, str]] = []
    test_file = write(tmp_path / "t" / "test_nz_mixed.py", MIXED_TESTS)
    res = await run(sb, test_file, orig, on_line=lambda s, line: lines.append((s, line)))
    assert (res.exit_code, res.passed, res.failed, res.errors, res.skipped) == (1, 2, 2, 0, 1)
    nodeids = [f.nodeid for f in res.failures]
    assert nodeids == ["test_nz_mixed.py::test_fail", "test_nz_mixed.py::TestK::test_param[2]"]
    assert res.failures[0].message.startswith("assert 3 == 4")
    assert "test_nz_mixed.py" in res.failures[0].tb_tail
    assert "2 failed, 2 passed, 1 skipped" in res.output_tail
    assert any("2 failed" in line for _, line in lines)


async def test_roots_decide_which_tree_is_tested(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(
        tmp_path / "t" / "test_nz_add.py", MIXED_TESTS.split("\n\n\ndef test_fail")[0]
    )
    cand = make_tree(tmp_path / "cand", "def add(a, b):\n    return a - b\n")
    assert (await run(sb, test_file, orig)).failed == 0
    res = await run(sb, test_file, cand)
    assert (res.passed, res.failed) == (0, 1)


async def test_the_repos_own_pytest_config_is_ignored(sb: Sandbox, orig: Path, tmp_path: Path):
    write(orig / "conftest.py", "raise RuntimeError('repo conftest loaded')\n")
    write(orig / "pytest.ini", "[pytest]\naddopts = --definitely-not-an-option\n")
    write(orig / "tests" / "conftest.py", "raise RuntimeError('repo conftest loaded')\n")
    test_file = write(tmp_path / "t" / "test_nz_cfg.py", "def test_ok():\n    pass\n")
    res = await run(sb, test_file, orig)
    assert (res.exit_code, res.passed, res.errors) == (0, 1, 0), res.output_tail


async def test_collection_error(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_imp.py", "from pkg.missing import nope\n")
    res = await run(sb, test_file, orig)
    assert (res.exit_code, res.passed, res.errors) == (2, 0, 1)
    [f] = res.failures
    assert f.nodeid == "test_nz_imp.py"
    assert f.message == "collection failure: ModuleNotFoundError: No module named 'pkg.missing'"


async def test_syntax_error_in_the_test_file(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_syn.py", "def test_x(:\n    pass\n")
    res = await run(sb, test_file, orig)
    assert (res.exit_code, res.errors) == (2, 1)
    assert res.failures[0].nodeid == "test_nz_syn.py"
    assert "SyntaxError" in res.failures[0].message


async def test_a_stale_report_is_never_reused(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_ok.py", "def test_ok():\n    pass\n")
    assert (await run(sb, test_file, orig)).passed == 1
    write(test_file, "import os\n\n\ndef test_ok():\n    os._exit(0)\n")  # no report written
    res = await run(sb, test_file, orig)
    assert (res.passed, res.errors) == (0, 1)
    assert res.failures[0].message == "pytest wrote no report (exit code 0)"


HANGING_TEST = """
import subprocess, sys, time


def test_hang():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open({pidfile!r}, "w") as f:
        f.write(str(child.pid))
    time.sleep(60)
"""


async def test_timeout_kills_the_process_group(sb: Sandbox, orig: Path, tmp_path: Path):
    pidfile = tmp_path / "grandchild.pid"
    test_file = write(tmp_path / "t" / "test_nz_hang.py", HANGING_TEST.format(pidfile=str(pidfile)))
    t0 = time.perf_counter()
    with pytest.raises(StepTimeout, match="pytest timed out after 3 s"):
        await run_pytest(sb, test_file, roots=[orig], cwd=orig, timeout=3, junit=tmp_path / "j.xml")
    assert time.perf_counter() - t0 < 15
    grandchild = int(pidfile.read_text())
    for _ in range(100):
        if not alive(grandchild):
            break
        await asyncio.sleep(0.05)
    assert not alive(grandchild)
    assert sb.procs is not None and sb.procs.live == {}


# --- capture -----------------------------------------------------------------------

CAPTURE_TESTS = """
import pytest
import pkg
from pkg.funcs import add, fact


def test_add():
    assert add(1, 2) == 3
    assert add(1, 2) == 3  # a duplicate
    assert add("a", "b") == "ab"
    assert pkg.add(5, 5) == 10  # re-exported by the package


@pytest.mark.nz_workload
def test_workload():
    assert add(1, 2) == 3  # already seen: now part of the workload
    assert [add(i, 1) for i in range(3)] == [1, 2, 3]


def test_fact():
    assert fact(5) == 120
    assert fact(3) == 6
"""


async def capture(
    sb: Sandbox, test_file: Path, tree: Path, qualname: str, out: Path, **kw
) -> Captured:
    return await capture_inputs(
        sb,
        test_file,
        roots=[tree],
        cwd=tree,
        module=MODULE,
        qualname=qualname,
        out_dir=out,
        timeout=60,
        **kw,
    )


async def test_capture_a_plain_function(sb: Sandbox, orig: Path, tmp_path: Path):
    lines: list[tuple[str, str]] = []
    test_file = write(tmp_path / "t" / "test_nz_capture.py", CAPTURE_TESTS)
    cap = await capture(
        sb, test_file, orig, "add", tmp_path / "out", on_line=lambda s, ln: lines.append((s, ln))
    )
    d = cap.data
    assert (d.ok, d.n_calls, d.n_kept, d.n_unpicklable) == (True, 8, 6, 0)
    assert (d.mutates_args, d.raises, d.deterministic) == (False, False, True)
    assert d.previews == ["add(1, 2)", "add('a', 'b')", "add(5, 5)"]
    assert d.total_bytes > 0
    assert cap.pytest.passed == 3 and cap.pytest.failed == 0
    assert cap.inputs.is_file() and cap.refs.is_file()
    data = load_samples(cap.inputs)
    assert (data["module"], data["qualname"]) == (MODULE, "add")
    got = [
        (pickle.loads(s["blob"]), s["workload"], s["nodeid"].split("::")[1])
        for s in data["samples"]
    ]
    assert got == [
        (((1, 2), {}), True, "test_add"),  # first seen in test_add, again in the workload
        ((("a", "b"), {}), False, "test_add"),
        (((5, 5), {}), False, "test_add"),
        (((0, 1), {}), True, "test_workload"),
        (((1, 1), {}), True, "test_workload"),
        (((2, 1), {}), True, "test_workload"),
    ]
    assert lines and not any(line.startswith(MARK) for _, line in lines)


async def test_recursion_records_only_outer_calls(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_capture.py", CAPTURE_TESTS)
    cap = await capture(sb, test_file, orig, "fact", tmp_path / "out")
    assert (cap.data.n_calls, cap.data.n_kept) == (2, 2)
    blobs = [pickle.loads(s["blob"]) for s in load_samples(cap.inputs)["samples"]]
    assert blobs == [((5,), {}), ((3,), {})]


METHOD_TESTS = """
from pkg.funcs import Scaler


def test_methods():
    s = Scaler(3)
    assert s.scale(2) == 6
    assert Scaler.double(4) == 8
    assert s.double(5) == 10
    assert Scaler.make(7).k == 7
"""


@pytest.mark.parametrize(
    ("qualname", "n_calls", "preview"),
    [
        ("Scaler.scale", 1, "scale(<pkg.funcs.Scaler object"),
        ("Scaler.double", 2, "double(4)"),
        ("Scaler.make", 1, "make(<class 'pkg.funcs.Scaler'>, 7)"),
    ],
)
async def test_capture_methods(
    sb: Sandbox, orig: Path, tmp_path: Path, qualname: str, n_calls: int, preview: str
):
    """``self``/``cls`` is the first captured argument; the replay on the same tree
    (the determinism check) shows the instance and class pickle and compare."""
    test_file = write(tmp_path / "t" / "test_nz_methods.py", METHOD_TESTS)
    cap = await capture(sb, test_file, orig, qualname, tmp_path / "out")
    assert cap.pytest.passed == 1
    assert (cap.data.n_calls, cap.data.n_kept, cap.data.n_unpicklable) == (n_calls, n_calls, 0)
    assert cap.data.deterministic
    assert cap.data.previews[0].startswith(preview)


UNPICKLABLE_TESTS = """
from pkg.funcs import apply


class Local:
    def __call__(self, x):
        return x


def test_apply():
    assert apply(lambda x: x + 1, 1) == 2
    assert apply(Local(), 3) == 3  # pickles, but by reference to the test module
    assert apply(abs, -2) == 2
"""


async def test_unpicklable_arguments_are_counted(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_apply.py", UNPICKLABLE_TESTS)
    cap = await capture(sb, test_file, orig, "apply", tmp_path / "out")
    assert (cap.data.n_calls, cap.data.n_unpicklable, cap.data.n_kept) == (3, 2, 1)
    assert cap.data.previews == ["apply(<built-in function abs>, -2)"]


async def test_zero_samples_is_a_result(sb: Sandbox, orig: Path, tmp_path: Path):
    test_file = write(tmp_path / "t" / "test_nz_capture.py", CAPTURE_TESTS)
    cap = await capture(sb, test_file, orig, "smallest", tmp_path / "out")
    assert (cap.data.n_calls, cap.data.n_kept, cap.data.deterministic) == (0, 0, True)
    assert load_samples(cap.inputs)["samples"] == []


async def test_capture_of_a_missing_function_is_a_sandbox_error(
    sb: Sandbox, orig: Path, tmp_path: Path
):
    test_file = write(tmp_path / "t" / "test_nz_capture.py", CAPTURE_TESTS)
    with pytest.raises(SandboxError, match="cannot wrap pkg.funcs:nope"):
        await capture(sb, test_file, orig, "nope", tmp_path / "out")


# --- differential ------------------------------------------------------------------

WORLD_TESTS = """
import pytest
from pkg.funcs import add, checked_sqrt, jitter, slow_square, smallest, sort_inplace


def test_add():
    assert add(1, 2) == 3
    assert add(10, -4) == 6


def test_checked_sqrt():
    assert checked_sqrt(4) == 2.0
    with pytest.raises(ValueError):
        checked_sqrt(-1)


def test_sort_inplace():
    xs = [3, 1, 2]
    assert sort_inplace(xs) == 3
    assert xs == [1, 2, 3]


def test_smallest():
    assert smallest([5, 2, 9]) == 2


def test_jitter():
    assert 1 <= jitter(1) < 2


def test_slow_square():
    for i in range(5):
        assert slow_square(i) == i * i
"""
WORLD_TARGETS = ("add", "checked_sqrt", "sort_inplace", "smallest", "jitter", "slow_square")


@dataclass
class World:
    base: Path
    sb: Sandbox
    orig: Path
    caps: dict[str, Captured]

    async def diff(
        self, tmp_path: Path, qualname: str, rewrite: str, timeout: float = 60, **kw
    ) -> DiffCheckResult:
        cand = make_tree(tmp_path / "cand", rewrite)
        cap = self.caps[qualname]
        return await differential(
            self.sb,
            roots=[cand],
            cwd=cand,
            module=MODULE,
            qualname=qualname,
            inputs=cap.inputs,
            refs=cap.refs,
            timeout=timeout,
            **kw,
        )


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    """The original tree's inputs and references for every target, captured once."""
    base = tmp_path_factory.mktemp("world")
    sb = make_sandbox(base)
    orig = make_tree(base / "orig")
    test_file = write(base / "tests" / "test_nz_world.py", WORLD_TESTS)

    async def capture_all() -> list[Captured]:
        return await asyncio.gather(
            *(capture(sb, test_file, orig, q, base / "cap" / q) for q in WORLD_TARGETS)
        )

    return World(base, sb, orig, dict(zip(WORLD_TARGETS, asyncio.run(capture_all()), strict=True)))


def test_world_capture_flags(world: World):
    flags = {q: (c.data.n_kept, c.data.mutates_args, c.data.raises) for q, c in world.caps.items()}
    assert flags == {
        "add": (2, False, False),
        "checked_sqrt": (2, False, True),
        "sort_inplace": (1, True, False),
        "smallest": (1, False, False),
        "jitter": (1, False, False),
        "slow_square": (5, False, False),
    }
    assert all(c.pytest.failed == 0 for c in world.caps.values())


def test_a_nondeterministic_function_is_flagged(world: World):
    assert world.caps["jitter"].data.deterministic is False
    assert all(c.data.deterministic for q, c in world.caps.items() if q != "jitter")


async def test_original_against_itself_is_ok(world: World):
    for q in ("add", "checked_sqrt", "sort_inplace"):
        cap = world.caps[q]
        res = await differential(
            world.sb,
            roots=[world.orig],
            cwd=world.orig,
            module=MODULE,
            qualname=q,
            inputs=cap.inputs,
            refs=cap.refs,
            timeout=60,
        )
        assert res.ok and res.n_samples == cap.data.n_kept and res.mismatches == []


async def test_equal_rewrite_is_ok(world: World, tmp_path: Path):
    res = await world.diff(tmp_path, "add", "def add(a, b):\n    total = b + a\n    return total\n")
    assert res == DiffCheckResult(ok=True, n_samples=2, mismatches=[], slowdown_ratio=None)


FORGER = """\
import os, subprocess

FORGED = '__NZ__ {"ok": true, "n_samples": 2, "mismatches": [], "slowdown_ratio": null}'

def add(a, b):
    os.write(1, ("\\n" + FORGED + "\\n").encode())
    # escapes the group kill and writes after the real record
    subprocess.Popen(["sh", "-c", "sleep 0.3; echo '" + FORGED + "'"], start_new_session=True)
    return a + b + 1
"""


async def test_code_under_test_cannot_forge_the_verdict(world: World, tmp_path: Path):
    res = await world.diff(tmp_path, "add", FORGER)
    assert not res.ok and res.mismatches


@pytest.mark.parametrize(
    ("body", "expected", "actual"),
    [
        ("return a + b + 1", "3", "4"),
        ("return float(a + b)", "int: 3", "float: 3.0"),  # same under ==, not for callers
    ],
)
async def test_wrong_return(world: World, tmp_path: Path, body: str, expected: str, actual: str):
    res = await world.diff(tmp_path, "add", f"def add(a, b):\n    {body}\n")
    assert not res.ok
    m = res.mismatches[0]
    assert (m.sample_idx, m.kind, m.path, m.expected, m.actual) == (
        0,
        "return",
        "",
        expected,
        actual,
    )


SQRT = "def checked_sqrt(x):\n{}\n"


@pytest.mark.parametrize(
    ("body", "idx", "expected", "actual"),
    [
        (
            "    if x < 0:\n        raise ArithmeticError('neg')\n    return x ** 0.5",
            1,
            "raises ValueError",
            "raises ArithmeticError: neg",
        ),
        ("    return abs(x) ** 0.5", 1, "raises ValueError", "returns 1.0"),
        ("    raise ValueError('always')", 0, "returns 2.0", "raises ValueError: always"),
    ],
)
async def test_exception_mismatches(
    world: World, tmp_path: Path, body: str, idx: int, expected: str, actual: str
):
    res = await world.diff(tmp_path, "checked_sqrt", SQRT.format(body))
    assert not res.ok
    [m] = res.mismatches
    assert (m.sample_idx, m.kind, m.expected, m.actual) == (idx, "exception", expected, actual)


async def test_a_different_exception_message_is_fine(world: World, tmp_path: Path):
    body = "    if x < 0:\n        raise ValueError(f'{x} is negative')\n    return x ** 0.5"
    assert (await world.diff(tmp_path, "checked_sqrt", SQRT.format(body))).ok


async def test_mutation_dropped(world: World, tmp_path: Path):
    res = await world.diff(
        tmp_path, "sort_inplace", "def sort_inplace(xs):\n    return len(sorted(xs))\n"
    )
    [m] = res.mismatches
    assert (m.kind, m.path, m.expected, m.actual) == ("mutation", "args[0][0]", "1", "3")


async def test_mutation_added(world: World, tmp_path: Path):
    res = await world.diff(
        tmp_path, "smallest", "def smallest(xs):\n    xs.sort()\n    return xs[0]\n"
    )
    [m] = res.mismatches
    assert (m.kind, m.path, m.expected, m.actual) == ("mutation", "args[0][0]", "5", "2")


async def test_slowdown_is_detected(world: World, tmp_path: Path):
    slow = "def slow_square(x):\n    time.sleep(0.03)\n    return x * x\n"
    res = await world.diff(tmp_path, "slow_square", slow)
    assert not res.ok and res.slowdown_ratio is not None and res.slowdown_ratio > 4
    [m] = res.mismatches
    assert m.kind == "slowdown" and "limit 4x" in m.actual
    same = await world.diff(tmp_path / "same", "slow_square", "")
    assert same.ok and same.slowdown_ratio is not None and same.slowdown_ratio < 4


async def test_a_hanging_sample_is_stopped(world: World, tmp_path: Path):
    hang = "def slow_square(x):\n    while True:\n        pass\n"
    t0 = time.perf_counter()
    res = await world.diff(tmp_path, "slow_square", hang, sample_timeout=0.5)
    assert time.perf_counter() - t0 < 10
    assert not res.ok
    [m] = res.mismatches  # the rest of the samples are skipped
    assert (m.sample_idx, m.kind, m.actual) == (0, "slowdown", "over 0.5 s (stopped)")


async def test_a_broken_candidate_module_is_a_sandbox_error(world: World, tmp_path: Path):
    with pytest.raises(SandboxError, match="diffcheck check: SyntaxError"):
        await world.diff(tmp_path, "add", "def add(:\n")


async def test_differential_timeout(world: World, tmp_path: Path):
    hang = "def slow_square(x):\n    while True:\n        pass\n"
    with pytest.raises(StepTimeout, match="diffcheck check timed out"):
        await world.diff(tmp_path, "slow_square", hang, timeout=2)


# --- results and arguments that only pickle can compare ----------------------------

STATEFUL = """
class Box:
    def __init__(self, v):
        self.v = v


def boxed(x):
    return Box(x)


def draw(rng, n):
    return [rng.random() for _ in range(n)]
"""

STATEFUL_TESTS = """
import random
from pkg.funcs import boxed, draw, sort_inplace


def test_boxed():
    assert boxed(2).v == 2


def test_draw():
    assert len(draw(random.Random(0), 3)) == 3


def test_sort_by_keyword():
    xs = [3, 1, 2]
    assert sort_inplace(xs=xs) == 3 and xs == [1, 2, 3]
"""


async def _capture_stateful(sb: Sandbox, tmp_path: Path, qualname: str) -> Captured:
    orig = make_tree(tmp_path / "orig", STATEFUL)
    test_file = write(tmp_path / "t" / "test_nz_stateful.py", STATEFUL_TESTS)
    return await capture(sb, test_file, orig, qualname, tmp_path / "out")


async def _diff(sb: Sandbox, cap: Captured, tree: Path, qualname: str) -> DiffCheckResult:
    return await differential(
        sb,
        roots=[tree],
        cwd=tree,
        module=MODULE,
        qualname=qualname,
        inputs=cap.inputs,
        refs=cap.refs,
        timeout=60,
    )


async def test_a_result_class_the_candidate_removed_is_a_mismatch(sb: Sandbox, tmp_path: Path):
    """The reference return value no longer unpickles on the candidate tree: that is
    the candidate's fault, so a mismatch, not a crash of the check."""
    cap = await _capture_stateful(sb, tmp_path, "boxed")
    assert (cap.data.n_kept, cap.data.deterministic) == (1, True)
    rewrite = "class Crate:\n    def __init__(self, v):\n        self.v = v\n\n\n"
    rewrite += "def boxed(x):\n    return Crate(x)\n"
    res = await _diff(sb, cap, make_tree(tmp_path / "cand", rewrite), "boxed")
    assert not res.ok and res.n_samples == 1
    [m] = res.mismatches
    assert (m.sample_idx, m.kind) == (0, "return")
    assert m.actual.startswith("cannot compare: AttributeError")


async def test_a_random_generators_state_is_compared(sb: Sandbox, tmp_path: Path):
    """``random.Random`` keeps its state in C: drawing from it is a mutation, an equal
    rewrite passes, and one extra draw is caught although the returns agree."""
    cap = await _capture_stateful(sb, tmp_path, "draw")
    assert (cap.data.n_kept, cap.data.mutates_args, cap.data.deterministic) == (1, True, True)
    same = "def draw(rng, n):\n    return list(map(lambda _: rng.random(), range(n)))\n"
    assert (await _diff(sb, cap, make_tree(tmp_path / "same", same), "draw")).ok
    extra = "def draw(rng, n):\n    out = [rng.random() for _ in range(n)]\n    rng.random()\n"
    extra += "    return out\n"
    res = await _diff(sb, cap, make_tree(tmp_path / "extra", extra), "draw")
    [m] = res.mismatches
    assert (m.kind, m.path) == ("mutation", "args[0]")


async def test_a_keyword_argument_mutation_is_reported_under_kwargs(sb: Sandbox, tmp_path: Path):
    cap = await _capture_stateful(sb, tmp_path, "sort_inplace")
    assert (cap.data.n_kept, cap.data.mutates_args) == (1, True)
    assert cap.data.previews == ["sort_inplace(xs=[3, 1, 2])"]
    rewrite = "def sort_inplace(xs):\n    return len(sorted(xs))\n"
    res = await _diff(sb, cap, make_tree(tmp_path / "cand", rewrite), "sort_inplace")
    [m] = res.mismatches
    assert (m.kind, m.path, m.expected, m.actual) == ("mutation", "kwargs['xs'][0]", "1", "3")
