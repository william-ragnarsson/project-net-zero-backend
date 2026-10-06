"""The bench lane: the quiet gate, persistent bench workers and the A/B measurement.

Gate discipline: checks (pytest, capture, differential) run under
``gate.shared()``; every measurement here (``calibrate``, ``baseline``,
``compare``) runs under ``gate.exclusive()``, so no sandboxed work competes
for the CPU while a bench measures. Starting a worker imports the repo's code,
so it holds ``gate.shared()`` like a check. The gate is not re-entrant: never
call a lane method (or enter ``lane.worker(...)``) while holding the gate.

Measurement: ``n_trials`` trials per arm in ABBA order (AB, BA, AB, ...), so
drift (thermal, frequency, background load) hits both arms alike. Trial j of
both arms replays the same samples. Per-call grams come from the energy model
(``power.model_energy``) or, on RAPL, from the measured package energy plus
the RAM model (the worker copies a trial's arguments first, then the meter
wraps only the timed calls). The statistics are in ``netzero.bench.stats``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from netzero.bench import stats, units
from netzero.bench.power import Energy, RaplMeter, model_energy, rapl_readable
from netzero.errors import BenchError
from netzero.events import BenchStats, Calibration, DecisionRule, MeasureStats, PowerInfo
from netzero.pipeline.env import HARNESS_PACKAGE
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import sandbox_env, venv_python
from netzero.sandbox.harness import common
from netzero.sandbox.procs import ProcRegistry

if TYPE_CHECKING:
    from netzero.config import Settings

PROBE_FRACTION = 0.1  # calibration grows n until a timing run takes this share of the target
MAX_GROWTH = 100.0
MAX_CALLS_PER_TRIAL = 100_000  # the worker holds one argument copy per call in memory
MIN_COMMAND_TIMEOUT_S = 10.0
TRIAL_TIMEOUT_FACTOR = 25.0
WHOLE_PASS_SLACK = 1.5  # a trial may grow to this share of its target to cover whole passes
REAP_TIMEOUT_S = 5.0
DRAIN_TIMEOUT_S = 1.0
EXIT_WAIT_S = 1.0  # after EOF on the records, how long to wait for the exit code
STDERR_LINES = 40
MIN_G = 1e-300  # a zero reading must not break the logs


# ---------------------------------------------------------------------------
# Quiet gate
# ---------------------------------------------------------------------------


class QuietGate:
    """Readers-writer gate: ``shared()`` for checks, ``exclusive()`` for benches.

    Writer preference: once a bench waits, new checks queue behind it, so a
    stream of checks cannot starve it; waiting benches go first in FIFO order.
    Grants are hand-offs (the releaser resolves the waiter's future), so release
    is synchronous, and a waiter cancelled after its grant gives it back.
    """

    def __init__(self) -> None:
        self._readers = 0
        self._writer = False
        self._writers: deque[asyncio.Future[None]] = deque()
        self._waiting_readers: list[asyncio.Future[None]] = []

    @property
    def readers(self) -> int:
        return self._readers

    @property
    def exclusive_held(self) -> bool:
        return self._writer

    @property
    def writers_waiting(self) -> int:
        return sum(not f.done() for f in self._writers)

    @contextlib.asynccontextmanager
    async def shared(self) -> AsyncIterator[None]:
        await self._acquire_shared()
        try:
            yield
        finally:
            self._release_shared()

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        await self._acquire_exclusive()
        try:
            yield
        finally:
            self._release_exclusive()

    async def _acquire_shared(self) -> None:
        if not self._writer and not self.writers_waiting:
            self._readers += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiting_readers.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self._release_shared()  # granted before the cancel landed
            elif fut in self._waiting_readers:
                self._waiting_readers.remove(fut)
            raise

    async def _acquire_exclusive(self) -> None:
        if not self._writer and self._readers == 0 and not self.writers_waiting:
            self._writer = True
            return
        fut = asyncio.get_running_loop().create_future()
        self._writers.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self._release_exclusive()
            else:
                with contextlib.suppress(ValueError):
                    self._writers.remove(fut)
                self._wake()  # checks queued behind this writer may go now
            raise

    def _release_shared(self) -> None:
        self._readers -= 1
        if self._readers == 0:
            self._wake()

    def _release_exclusive(self) -> None:
        self._writer = False
        self._wake()

    def _wake(self) -> None:
        if self._writer:
            return
        while self._writers and self._writers[0].done():
            self._writers.popleft()  # cancelled waiters
        if self._writers:
            if self._readers == 0:
                self._writer = True
                self._writers.popleft().set_result(None)
            return
        waiting, self._waiting_readers = self._waiting_readers, []
        for fut in waiting:
            if not fut.done():
                self._readers += 1
                fut.set_result(None)


# ---------------------------------------------------------------------------
# Bench worker
# ---------------------------------------------------------------------------


class BenchWorker:
    """One persistent ``bench_worker`` process (one tree: the original or a candidate).

    An async context manager: entering starts it and waits for its ready
    record, leaving kills its process group. A command that times out, a dead
    worker or a cancellation also kill it; a target exception leaves it running.
    """

    def __init__(
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path,
        env: dict[str, str],
        label: str,
        start_timeout: float,
        procs: ProcRegistry | None = None,
        gate: QuietGate | None = None,
    ):
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.label = label
        self.start_timeout = start_timeout
        self.procs = procs
        self.gate = gate
        self.proc: asyncio.subprocess.Process | None = None
        self.samples = 0
        self.warmed = False
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._drain: asyncio.Task[None] | None = None

    async def __aenter__(self) -> BenchWorker:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    async def restart(self) -> None:
        """A fresh process after the old one died or was killed (a timed-out command)."""
        await self.close()
        self.warmed = False
        await self.start()

    async def start(self) -> None:
        """Spawn and wait for the ready record (the target imported, the samples loaded)."""
        async with self.gate.shared() if self.gate is not None else contextlib.nullcontext():
            try:
                self.proc = await runner.spawn(
                    self.argv,
                    cwd=self.cwd,
                    env=self.env,
                    procs=self.procs,
                    label=self.label,
                    stdin_pipe=True,
                )
            except OSError as e:  # no interpreter, no cwd, out of fds: a failed bench
                raise BenchError(
                    f"{self.label}: the bench worker did not start ({type(e).__name__}: {e})"
                ) from e
            self._drain = asyncio.create_task(self._drain_stderr(self.proc.stderr))
            try:
                rec = await self._read(self.start_timeout, "start-up")
            except BaseException:
                await self.close()
                raise
        self.samples = int(rec.get("samples", 0))

    async def request(self, cmd: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Send one command and wait for its record; raises ``BenchError``."""
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise BenchError(f"{self.label}: the bench worker is not running")
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write((json.dumps(cmd) + "\n").encode())
            await proc.stdin.drain()
        return await self._read(timeout, str(cmd.get("cmd")))

    async def close(self) -> None:
        """Kill the worker's process group and wait for it; safe to call twice."""
        proc, self.proc = self.proc, None
        drain, self._drain = self._drain, None
        try:
            if proc is not None:
                runner.kill_group(proc.pid)  # before any await: a second cancel cannot skip it
                if proc.stdin is not None:
                    proc.stdin.close()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(runner.reap(proc, self.procs), REAP_TIMEOUT_S)
            if drain is not None:
                await asyncio.wait({drain}, timeout=DRAIN_TIMEOUT_S)  # the last of stderr
        finally:
            if proc is not None and self.procs is not None:
                self.procs.remove(proc.pid)
            if drain is not None:
                drain.cancel()

    async def _read(self, timeout: float, what: str) -> dict[str, Any]:
        try:
            rec = await asyncio.wait_for(self._next_record(), timeout)
        except TimeoutError:
            await self.close()
            raise BenchError(
                f"{self.label}: {what} took longer than {timeout:g} s",
                detail=self.stderr_tail() or None,
                kind="timeout",
            ) from None
        except BaseException:  # cancelled: the worker must not outlive the bench
            await self.close()
            raise
        if rec is None:
            proc = self.proc
            code = proc.returncode if proc is not None else None
            await self.close()
            raise BenchError(
                f"{self.label}: the bench worker exited during {what} (exit {code})",
                detail=self.stderr_tail() or None,
            )
        if "error" in rec:
            raise BenchError(
                f"{self.label}: {rec['error']}",
                detail=rec.get("traceback") or self.stderr_tail() or None,
            )
        return rec

    async def _next_record(self) -> dict[str, Any] | None:
        assert self.proc is not None and self.proc.stdout is not None
        stdout = self.proc.stdout
        while True:
            try:
                line = await stdout.readline()
            except ValueError:  # an over-long line someone wrote to fd 1
                continue
            if not line:
                await self._exited(EXIT_WAIT_S)
                return None
            rec = common.parse_record(line.decode("utf-8", "replace").rstrip("\r\n"))
            if rec is not None:
                return rec

    async def _exited(self, limit: float) -> None:
        """Wait up to ``limit`` for the exit code. Not ``proc.wait()``: that also
        waits for stderr to close, which a grandchild can hold open."""
        proc = self.proc
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        while proc is not None and proc.returncode is None and loop.time() < deadline:
            await asyncio.sleep(0.01)

    async def _drain_stderr(self, stream: asyncio.StreamReader | None) -> None:
        """Keep the tail of stderr (the repo's prints) and never let the pipe fill."""
        if stream is None:
            return
        partial = ""
        while chunk := await stream.read(65536):
            partial += chunk.decode("utf-8", "replace")
            *lines, partial = partial.split("\n")
            self._stderr.extend(ln[:500] for ln in lines)
            partial = partial[-4000:]


# ---------------------------------------------------------------------------
# Lane
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Trial:
    cpu_s: float
    wall_s: float
    n: int
    energy: Energy


class BenchLane:
    """Benchmarks for one run, against one venv and one ``PowerInfo``."""

    def __init__(
        self,
        settings: Settings,
        power: PowerInfo,
        *,
        venv: Path,
        harness_root: Path,
        home: Path,
        tmp: Path,
        procs: ProcRegistry | None = None,
        log: Callable[[str], None] | None = None,
        gate: QuietGate | None = None,
    ):
        self.settings = settings
        self.power = power
        self.venv = venv
        self.harness_root = harness_root
        self.home = home
        self.tmp = tmp
        self.procs = procs
        self.log = log or (lambda _msg: None)
        self.gate = gate or QuietGate()
        self.rule = DecisionRule(alpha=settings.alpha, min_effect_pct=settings.min_effect_pct)
        self._rapl: RaplMeter | None = None
        self._rapl_tried = False

    # -- workers -----------------------------------------------------------------

    def worker(
        self,
        roots: Sequence[Path],
        module: str,
        qualname: str,
        inputs: Path,
        label: str,
        *,
        cwd: Path | None = None,
    ) -> BenchWorker:
        """A worker for the tree whose import roots are ``roots``; start it with
        ``async with``. ``cwd`` defaults to the lane's tmp dir."""
        argv = [venv_python(self.venv), "-P", "-m", f"{HARNESS_PACKAGE}.bench_worker"]
        argv += ["--module", module, "--qualname", qualname, "--inputs", str(inputs)]
        env = sandbox_env(
            venv=self.venv,
            home=self.home,
            tmp=self.tmp,
            pythonpath=[self.harness_root, *roots],
        )
        return BenchWorker(
            argv,
            cwd=cwd or self.tmp,
            env=env,
            label=label,
            start_timeout=self.settings.test_timeout_s,
            procs=self.procs,
            gate=self.gate,
        )

    # -- measurements ------------------------------------------------------------

    async def calibrate(self, worker: BenchWorker) -> Calibration:
        """``calls_per_trial`` so one trial takes about ``trial_target_s``: at
        least 1, at most ``MAX_CALLS_PER_TRIAL``, and a whole number of passes
        over the workload when that keeps a trial under ``WHOLE_PASS_SLACK`` x
        the target. A single call longer than the target gives 1 call per trial.

        The probe grows ``n`` from 1, but never past one pass over the samples
        before it has timed one: the first samples may be the cheap ones, and a
        pass costs what the warmup did."""
        target = self.settings.trial_target_s
        goal = target * PROBE_FRACTION
        m = max(1, worker.samples)
        # the first probe is one call of unknown length: allow what a test may take
        timeout = max(self.settings.test_timeout_s, self._timeout(target))
        async with self.gate.exclusive():
            await self._warmup(worker)
            n = 1
            while True:
                rec = await worker.request({"cmd": "time", "n": n}, timeout)
                wall = float(rec["wall_s"])
                if wall >= goal or n >= MAX_CALLS_PER_TRIAL:
                    break
                growth = min(MAX_GROWTH, max(2.0, goal / max(wall, 1e-9)))
                grown = math.ceil(n * growth)
                if n < m:
                    grown = min(grown, m)
                n = min(MAX_CALLS_PER_TRIAL, grown)
        est = wall / n
        k = MAX_CALLS_PER_TRIAL if est <= 0 else round(target / est)
        k = min(MAX_CALLS_PER_TRIAL, max(1, k))
        if m > 1 and k * WHOLE_PASS_SLACK >= m:
            k = min(MAX_CALLS_PER_TRIAL, max(m, round(k / m) * m))
        return Calibration(calls_per_trial=k, est_call_s=est, n_samples=m)

    async def baseline(self, worker: BenchWorker, cal: Calibration) -> tuple[MeasureStats, float]:
        """The original alone: its per-call stats and the CV% of per-trial grams."""
        await self._meter()
        async with self.gate.exclusive():
            await self._warmup(worker)
            trials = [await self._trial(worker, cal, j) for j in range(self.settings.n_trials)]
        original = self._measure(trials, cal)
        cv = stats.cv_pct(original.trials_g)
        if cv > self.settings.baseline_cv_warn_pct:
            self.log(
                f"baseline is noisy: CV {cv:.0f}% across trials "
                f"(warning above {self.settings.baseline_cv_warn_pct:g}%)"
            )
        return original, cv

    async def compare(
        self, original: BenchWorker, candidate: BenchWorker, cal: Calibration
    ) -> BenchStats:
        """``n_trials`` per arm in ABBA blocks; ``delta_pct < 0`` means the candidate
        emits less per call. ``significant`` is the gate on the raw p (Holm comes
        later, in ``stats.decide``); savings are positive when the candidate saves."""
        n_trials = self.settings.n_trials
        if n_trials < 2:
            raise BenchError(f"n_trials must be at least 2 for a comparison, got {n_trials}")
        await self._meter()
        arms: dict[str, list[Trial]] = {"original": [], "candidate": []}
        async with self.gate.exclusive():
            await self._warmup(original)
            await self._warmup(candidate)
            for j in range(n_trials):
                order = ("original", "candidate") if j % 2 == 0 else ("candidate", "original")
                for arm in order:
                    worker = original if arm == "original" else candidate
                    arms[arm].append(await self._trial(worker, cal, j))
        o = self._measure(arms["original"], cal)
        c = self._measure(arms["candidate"], cal)
        cmp = stats.compare_logs(
            [math.log(max(g, MIN_G)) for g in c.trials_g],
            [math.log(max(g, MIN_G)) for g in o.trials_g],
        )
        o_cpu, c_cpu = o.cpu_s_per_call.mean, c.cpu_s_per_call.mean
        cpu_delta = (c_cpu / o_cpu - 1.0) * 100.0 if o_cpu > 0 else 0.0
        return BenchStats(
            original=o,
            candidate=c,
            delta_pct=cmp.delta_pct,
            delta_ci_pct=cmp.ci,
            p_value=cmp.p_value,
            significant=stats.passes_gate(cmp.p_value, cmp.ci.hi, cmp.delta_pct, self.rule),
            n_trials=n_trials,
            calls_per_trial=cal.calls_per_trial,
            cpu_time_delta_pct=cpu_delta,
            sanity_ok=stats.sanity_ok(cmp.delta_pct, cpu_delta, self.settings.min_effect_pct),
            power=self.power,
            g_saved_per_1m_calls=units.saved_per_million(o.g_per_call.mean, c.g_per_call.mean),
            kwh_saved_per_1m_calls=units.saved_per_million(
                o.kwh_per_call.mean, c.kwh_per_call.mean
            ),
        )

    # -- pieces ------------------------------------------------------------------

    def _timeout(self, expected_s: float) -> float:
        return max(MIN_COMMAND_TIMEOUT_S, TRIAL_TIMEOUT_FACTOR * expected_s)

    async def _warmup(self, worker: BenchWorker) -> None:
        if not worker.warmed:
            await worker.request({"cmd": "warmup"}, self.settings.test_timeout_s)
            worker.warmed = True

    async def _trial(self, worker: BenchWorker, cal: Calibration, j: int) -> Trial:
        k = cal.calls_per_trial
        cmd = {"cmd": "trial", "n": k, "start": j * k}
        timeout = self._timeout(max(self.settings.trial_target_s, cal.est_call_s * k))
        meter = self._rapl
        if meter is None:
            rec = await worker.request(cmd, timeout)
            cpu_s, wall_s = float(rec["cpu_s"]), float(rec["wall_s"])
            return Trial(cpu_s, wall_s, int(rec["n"]), model_energy(cpu_s, wall_s, self.power))
        # the package meter sees everything in its window: copy the arguments first
        await worker.request({**cmd, "cmd": "prepare"}, timeout)
        await asyncio.to_thread(meter.start)
        try:
            rec = await worker.request({"cmd": "run"}, timeout)
        except BaseException:
            with contextlib.suppress(Exception):  # close the task; the trial's error wins
                await asyncio.to_thread(meter.stop)
            raise
        measured = await asyncio.to_thread(meter.stop)
        cpu_s, wall_s = float(rec["cpu_s"]), float(rec["wall_s"])
        # RAPL measures the CPU package; RAM stays the model over the trial's wall time
        ram = model_energy(0.0, wall_s, self.power).kwh_ram
        return Trial(cpu_s, wall_s, int(rec["n"]), Energy(measured.kwh_cpu, ram))

    async def _meter(self) -> None:
        """Set up the RAPL meter once when the profile says RAPL; on failure fall
        back to the model and say so in ``power``."""
        if self._rapl_tried or self.power.method != "codecarbon_task":
            return
        self._rapl_tried = True
        try:
            if not rapl_readable():
                raise BenchError("no readable RAPL package counter")
            out = self.tmp / "codecarbon"
            out.mkdir(parents=True, exist_ok=True)
            self._rapl = await asyncio.to_thread(RaplMeter.create, out)
        except Exception as e:
            why = f"RAPL meter unavailable ({type(e).__name__}: {e}); energy model used"
            self.log(why)
            self.power = self.power.model_copy(
                update={
                    "badge": "estimated",
                    "method": "codecarbon_model",
                    "notes": [*self.power.notes, why],
                }
            )

    def _measure(self, trials: list[Trial], cal: Calibration) -> MeasureStats:
        kg = self.power.grid.kg_per_kwh
        g = [t.energy.grams(kg) / t.n for t in trials]
        return MeasureStats(
            g_per_call=stats.interval(g),
            kwh_per_call=stats.interval([t.energy.kwh / t.n for t in trials]),
            kwh_cpu_per_call=sum(t.energy.kwh_cpu / t.n for t in trials) / len(trials),
            kwh_ram_per_call=sum(t.energy.kwh_ram / t.n for t in trials) / len(trials),
            cpu_s_per_call=stats.interval([t.cpu_s / t.n for t in trials]),
            wall_s_per_call=stats.interval([t.wall_s / t.n for t in trials]),
            n_trials=len(trials),
            calls_per_trial=cal.calls_per_trial,
            trials_g=g,
        )
