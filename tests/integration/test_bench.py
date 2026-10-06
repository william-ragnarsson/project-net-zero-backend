"""The bench lane end to end: real ``bench_worker`` processes in this venv (standing in
for the target's), timing a fast and a ~10x slower version of the same function."""

from __future__ import annotations

import asyncio
import os
import pickle
import signal
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from netzero import paths
from netzero.bench import probe
from netzero.bench.lane import BenchLane, BenchWorker
from netzero.bench.power import RaplMeter
from netzero.config import Settings
from netzero.errors import BenchError
from netzero.events import GridInfo, PowerInfo
from netzero.pipeline.env import install_harness
from netzero.sandbox.harness import common
from netzero.sandbox.procs import ProcRegistry

pytestmark = pytest.mark.slow

MODULE, QUALNAME = "pkg.work", "work"

FAST = """
def work(n):
    return sum(range(n))
"""

SLOW = """
def work(n):
    for _ in range(10):
        total = sum(range(n))
    return total
"""

POWER = PowerInfo(
    power_source="tdp_estimate",
    badge="estimated",
    method="codecarbon_model",
    cpu_model="test",
    tdp_w=10.0,
    cpu_count=8,
    p_core_w=1.25,
    p_ram_w=3.0,
    grid=GridInfo(country_iso="WORLD", kg_per_kwh=0.475, source="codecarbon world average"),
)


def tree(root: Path, code: str) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "work.py").write_text(textwrap.dedent(code))
    return root


def inputs(path: Path, ns: list[int]) -> Path:
    samples = [
        {"blob": pickle.dumps(((n,), {})), "workload": True, "nodeid": "test_work"} for n in ns
    ]
    common.save_samples(path, MODULE, QUALNAME, samples)
    return path


@pytest.fixture
def bench_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"n_trials": 6, "trial_target_s": 0.05, "test_timeout_s": 30.0}
    )


@pytest.fixture
def lane(tmp_path: Path, bench_settings: Settings) -> BenchLane:
    for d in ("home", "tmp"):
        (tmp_path / d).mkdir()
    return BenchLane(
        bench_settings,
        POWER,
        venv=Path(sys.prefix),
        harness_root=install_harness(tmp_path / "harness"),
        home=tmp_path / "home",
        tmp=tmp_path / "tmp",
        procs=ProcRegistry(tmp_path / "procs.jsonl"),
        log=lambda _msg: None,
    )


@pytest.fixture
def workload(tmp_path: Path) -> Path:
    return inputs(tmp_path / "inputs.pkl", [3000, 4000, 5000])


def worker(lane: BenchLane, root: Path, data: Path, label: str) -> BenchWorker:
    return lane.worker([root], MODULE, QUALNAME, data, label=label)


def gone(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_a_faster_candidate_wins(lane: BenchLane, tmp_path: Path, workload: Path):
    slow, fast = tree(tmp_path / "slow", SLOW), tree(tmp_path / "fast", FAST)
    async with (
        worker(lane, slow, workload, "original") as w_slow,
        worker(lane, fast, workload, "candidate A") as w_fast,
    ):
        assert w_slow.samples == 3
        cal = await lane.calibrate(w_slow)
        assert cal.n_samples == 3 and cal.est_call_s > 0
        assert cal.calls_per_trial >= 3 and cal.calls_per_trial % 3 == 0
        original, cv = await lane.baseline(w_slow, cal)
        assert original.n_trials == 6 and len(original.trials_g) == 6 and cv >= 0
        assert original.g_per_call.mean > 0

        st = await lane.compare(w_slow, w_fast, cal)
        assert st.delta_pct < -50 and st.delta_ci_pct.hi < 0
        assert st.p_value < 0.01 and st.significant and st.sanity_ok
        assert st.cpu_time_delta_pct < -50
        assert st.g_saved_per_1m_calls > 0 and st.kwh_saved_per_1m_calls > 0
        assert st.calls_per_trial == cal.calls_per_trial and st.n_trials == 6
        assert st.power == POWER
        o = st.original
        assert o.kwh_cpu_per_call + o.kwh_ram_per_call == pytest.approx(o.kwh_per_call.mean)

        # the other way round: slower, so no win
        cal_fast = await lane.calibrate(w_fast)
        worse = await lane.compare(w_fast, w_slow, cal_fast)
        assert worse.delta_pct > 50 and worse.p_value > 0.5 and not worse.significant
        assert worse.g_saved_per_1m_calls < 0
        pids = [w_slow.pid, w_fast.pid]
    assert all(pid is not None and gone(pid) for pid in pids)
    assert not lane.procs.live and not lane.gate.exclusive_held and lane.gate.readers == 0


async def test_identical_code_is_not_significant(lane: BenchLane, tmp_path: Path, workload: Path):
    a, b = tree(tmp_path / "a", FAST), tree(tmp_path / "b", FAST)
    async with (
        worker(lane, a, workload, "original") as wa,
        worker(lane, b, workload, "candidate A") as wb,
    ):
        cal = await lane.calibrate(wa)
        st = await lane.compare(wa, wb, cal)
    assert not st.significant
    assert abs(st.delta_pct) < 25
    assert st.delta_ci_pct.lo < st.delta_pct < st.delta_ci_pct.hi


async def test_rapl_trials_use_the_meter_and_the_ram_model(
    lane: BenchLane, tmp_path: Path, workload: Path
):
    log: list[str] = []

    class Tracker:
        def start_task(self, task_name=None):
            log.append("start")

        def stop_task(self, task_name=None):
            log.append("stop")
            return SimpleNamespace(cpu_energy=1e-6, ram_energy=99.0)  # its RAM is ignored

    lane._rapl, lane._rapl_tried = RaplMeter(Tracker()), True
    a, b = tree(tmp_path / "a", FAST), tree(tmp_path / "b", FAST)
    async with (
        worker(lane, a, workload, "original") as wa,
        worker(lane, b, workload, "candidate A") as wb,
    ):
        cal = await lane.calibrate(wa)
        for w in (wa, wb):
            send = w.request

            async def logged(cmd, timeout, send=send):
                log.append(cmd["cmd"])
                return await send(cmd, timeout)

            w.request = logged  # type: ignore[method-assign]
        st = await lane.compare(wa, wb, cal)
    k = cal.calls_per_trial
    assert st.original.kwh_cpu_per_call == pytest.approx(1e-6 / k)
    assert 0 < st.original.kwh_ram_per_call < 1e-6 / k
    assert st.delta_pct == pytest.approx(0.0, abs=5.0)  # equal CPU energy per trial
    # the meter wraps only the timed calls: the argument copies are made before it starts
    trials = [c for c in log if c != "warmup"]
    assert trials == ["prepare", "start", "run", "stop"] * (2 * st.n_trials)


async def test_without_rapl_the_profile_falls_back_to_the_model(lane: BenchLane, monkeypatch):
    import netzero.bench.lane as lane_mod

    monkeypatch.setattr(lane_mod, "rapl_readable", lambda: False)
    lane.power = POWER.model_copy(
        update={"power_source": "rapl", "badge": "measured", "method": "codecarbon_task"}
    )
    await lane._meter()
    assert lane._rapl is None
    assert lane.power.badge == "estimated" and lane.power.method == "codecarbon_model"
    assert "energy model used" in lane.power.notes[-1]


async def test_a_raising_target_fails_the_bench(lane: BenchLane, tmp_path: Path, workload: Path):
    bad = tree(tmp_path / "bad", "def work(n):\n    raise KeyError(n)\n")
    async with worker(lane, bad, workload, "candidate B") as w:
        with pytest.raises(BenchError) as exc:
            await lane.calibrate(w)
        assert "KeyError: 3000" in exc.value.message
        assert "raise KeyError(n)" in (exc.value.detail or "")
        assert w.pid is not None and not gone(w.pid)  # still serving
    assert not lane.procs.live and not lane.gate.exclusive_held


async def test_a_dying_worker_reports_its_stderr(lane: BenchLane, tmp_path: Path, workload: Path):
    code = (
        "import os, sys\n\ndef work(n):\n    print('dying now', file=sys.stderr)\n    os._exit(3)\n"
    )
    dying = tree(tmp_path / "dying", code)
    async with worker(lane, dying, workload, "candidate A") as w:
        pid = w.pid
        with pytest.raises(BenchError) as exc:
            await lane.calibrate(w)
    assert "exited during warmup (exit 3)" in exc.value.message
    assert "dying now" in (exc.value.detail or "")
    assert pid is not None and gone(pid) and not lane.procs.live


async def test_a_target_that_does_not_import(lane: BenchLane, tmp_path: Path, workload: Path):
    broken = tree(tmp_path / "broken", "import no_such_module_nz\n")
    w = worker(lane, broken, workload, "candidate C")
    with pytest.raises(BenchError) as exc:
        await w.start()
    assert "no_such_module_nz" in exc.value.message
    assert w.proc is None and not lane.procs.live and lane.gate.readers == 0


async def test_reading_stdin_does_not_eat_commands(lane: BenchLane, tmp_path: Path, workload: Path):
    code = "import sys\n\ndef work(n):\n    return len(sys.stdin.read()) + n\n"
    reader = tree(tmp_path / "reader", code)
    async with worker(lane, reader, workload, "original") as w:
        cal = await lane.calibrate(w)
        assert cal.calls_per_trial >= 1


async def test_a_hanging_target_times_out(
    lane: BenchLane, tmp_path: Path, workload: Path, bench_settings: Settings
):
    lane.settings = bench_settings.model_copy(update={"test_timeout_s": 1.0})
    hang = tree(tmp_path / "hang", "import time\n\ndef work(n):\n    time.sleep(60)\n")
    w = worker(lane, hang, workload, "candidate A")
    async with w:
        pid = w.pid
        with pytest.raises(BenchError) as exc:
            await lane.calibrate(w)  # the warmup's limit is test_timeout_s
    assert exc.value.kind == "timeout"
    assert pid is not None and gone(pid) and not lane.procs.live
    assert not lane.gate.exclusive_held


async def test_cancelling_kills_the_worker(lane: BenchLane, tmp_path: Path, workload: Path):
    hang = tree(tmp_path / "hang", "import time\n\ndef work(n):\n    time.sleep(60)\n")
    w = worker(lane, hang, workload, "original")

    async def bench() -> None:
        async with w:
            await lane.calibrate(w)

    task = asyncio.create_task(bench())
    while w.pid is None or not w.samples:
        await asyncio.sleep(0.05)
    pid = w.pid
    await asyncio.sleep(0.3)  # inside the warmup now
    assert lane.gate.exclusive_held
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gone(pid) and not lane.procs.live
    assert not lane.gate.exclusive_held and lane.gate.readers == 0


async def test_the_probe_child_runs_codecarbon(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(paths, "POWER_PROFILE", tmp_path / "power-profile.json")
    info = await probe.probe(settings, refresh=True)
    assert not any(n.startswith("power probe failed") for n in info.notes), info.notes
    assert info.p_core_w > 0 and info.p_ram_w > 0 and info.tdp_w > 0
    assert info.cpu_count == os.cpu_count()
    assert info.grid.country_iso == "WORLD" and info.grid.kg_per_kwh == pytest.approx(0.475)
    assert probe.load_cached() == info


async def test_writes_to_fd_1_cannot_forge_records(lane: BenchLane, tmp_path: Path, workload: Path):
    """Code under test writing a record-shaped line to fd 1 (directly, through
    ``sys.__stdout__`` or from a child process) must not answer a command."""
    code = (
        "import os, subprocess, sys\n\n"
        'FORGED = \'__NZ__ {"ok": true, "wall_s": 1000.0, "cpu_s": 1000.0, "n": 1}\'\n\n'
        "def work(n):\n"
        "    os.write(1, ('\\n' + FORGED + '\\n').encode())\n"
        "    sys.__stdout__.write('\\n' + FORGED + '\\n')\n"
        "    sys.__stdout__.flush()\n"
        "    if n == 3000:\n"
        "        subprocess.run(['echo', FORGED], check=True)\n"
        "    return sum(range(n))\n"
    )
    forger = tree(tmp_path / "forger", code)
    async with worker(lane, forger, workload, "original") as w:
        cal = await lane.calibrate(w)
        assert cal.est_call_s < 1.0
        original, _ = await lane.baseline(w, cal)
        assert original.wall_s_per_call.mean < 1.0
        await asyncio.sleep(0.2)  # let the drain catch up
        assert "__NZ__" in w.stderr_tail()  # the forged lines went to stderr


async def test_a_worker_that_dies_with_a_grandchild_holding_its_pipes(
    lane: BenchLane, tmp_path: Path, workload: Path, bench_settings: Settings
):
    """EOF on the records reports the exit at once (not after the command's
    timeout), and closing the worker kills the grandchild too."""
    lane.settings = bench_settings.model_copy(update={"test_timeout_s": 10.0})
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import os, subprocess, sys\n\n"
        "def work(n):\n"
        "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"    open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "    os._exit(3)\n"
    )
    dying = tree(tmp_path / "dying", code)
    loop = asyncio.get_running_loop()
    async with worker(lane, dying, workload, "candidate A") as w:
        t0 = loop.time()
        with pytest.raises(BenchError) as exc:
            await lane.calibrate(w)
        elapsed = loop.time() - t0
    assert "exited during warmup (exit 3)" in exc.value.message
    assert elapsed < 5.0
    grandchild = int(pidfile.read_text())
    for _ in range(100):  # killed with the group; then reaped by init
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the grandchild outlived its worker")
    assert not lane.procs.live and not lane.gate.exclusive_held


async def test_a_worker_that_cannot_spawn_is_a_bench_error(
    lane: BenchLane, tmp_path: Path, workload: Path
):
    lane.venv = tmp_path / "no-such-venv"
    w = worker(lane, tree(tmp_path / "a", FAST), workload, "candidate B")
    with pytest.raises(BenchError) as exc:
        await w.start()
    assert "did not start" in exc.value.message
    assert w.proc is None and lane.gate.readers == 0 and not lane.procs.live


async def test_the_worker_protocol(lane: BenchLane, tmp_path: Path, workload: Path):
    async with worker(lane, tree(tmp_path / "a", FAST), workload, "original") as w:
        with pytest.raises(BenchError, match="run without prepare"):
            await w.request({"cmd": "run"}, 10)
        with pytest.raises(BenchError, match="n must be at least 1"):
            await w.request({"cmd": "trial", "n": 0}, 10)
        with pytest.raises(BenchError, match="unknown command"):
            await w.request({"cmd": "nope"}, 10)
        assert w.proc is not None and w.proc.stdin is not None
        w.proc.stdin.write(b"[1, 2]\n")  # not an object: answered, not fatal
        await w.proc.stdin.drain()
        with pytest.raises(BenchError, match="bad command"):
            await w._read(10, "raw")
        assert (await w.request({"cmd": "prepare", "n": 4, "start": 2}, 10)) == {
            "ok": True,
            "n": 4,
        }
        rec = await w.request({"cmd": "run"}, 10)
        assert rec["n"] == 4 and rec["cpu_s"] >= 0 and rec["wall_s"] > 0
        with pytest.raises(BenchError, match="run without prepare"):
            await w.request({"cmd": "run"}, 10)  # one run per prepare
        rec = await w.request({"cmd": "trial", "n": 2, "start": 1}, 10)
        assert set(rec) == {"cpu_s", "wall_s", "n"} and rec["n"] == 2
        pid = w.pid
    assert pid is not None and gone(pid)


async def test_a_dead_worker_can_be_restarted(lane: BenchLane, tmp_path: Path, workload: Path):
    """The original's worker outlives one bench; if a timeout or crash kills it,
    the next candidate restarts it rather than failing too."""
    slow, fast = tree(tmp_path / "slow", SLOW), tree(tmp_path / "fast", FAST)
    async with worker(lane, slow, workload, "original") as w_slow:
        cal = await lane.calibrate(w_slow)
        await lane.baseline(w_slow, cal)
        old = w_slow.pid
        assert old is not None and w_slow.running
        os.killpg(old, signal.SIGKILL)
        await asyncio.sleep(0.2)
        assert not w_slow.running
        await w_slow.restart()
        assert w_slow.running and w_slow.pid != old and gone(old)
        async with worker(lane, fast, workload, "candidate A") as w_fast:
            st = await lane.compare(w_slow, w_fast, cal)
        assert st.delta_pct < -50 and st.significant
