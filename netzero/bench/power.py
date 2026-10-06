"""Energy per trial: the CodeCarbon-based model, a RAPL meter, and the macOS calibration.

Model (``PowerInfo.method == "codecarbon_model"``)::

    E = cpu_s * P_core + wall_s * P_ram

``P_core`` is CodeCarbon's process-mode CPU-load power for one busy thread
(``tdp * cpu_percent / cpu_count / 100`` at 100% of one core, i.e.
``tdp / cpu_count``) or a calibrated value; ``P_ram`` is CodeCarbon's RAM
model. CPU *time* instead of a sampled ``cpu_percent`` makes it exact for the
code under test and free of the 1 s sampling window.

With RAPL (``method == "codecarbon_task"``) ``RaplMeter`` reads the package
energy through a CodeCarbon tracker's ``start_task``/``stop_task``.

Calibration (macOS, only when ``sudo -n powermetrics`` works): the mean
"CPU Power" while idle and while one thread spins; ``P_core = max(busy - idle,
1 W)`` is stored in the cached profile with the ``calibrated`` badge.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import tempfile
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from netzero.bench import probe as power_probe
from netzero.bench import units
from netzero.errors import BenchError
from netzero.events import PowerInfo
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import tool_env
from netzero.sandbox.procs import ProcRegistry

if TYPE_CHECKING:
    from netzero.bench.lane import QuietGate
    from netzero.config import Settings

RAPL_ROOT = Path("/sys/class/powercap")
MIN_P_CORE_W = 1.0
IDLE_S = 3.0
BUSY_S = 3.0
SETTLE_S = 0.5
SAMPLE_MS = 500
CPU_POWER_RE = re.compile(r"CPU Power: (\d+) mW")  # codecarbon.core.powermetrics

Sampler = Callable[[float], Awaitable[float]]  # seconds -> mean CPU power in W


@dataclass(frozen=True)
class Energy:
    kwh_cpu: float
    kwh_ram: float

    @property
    def kwh(self) -> float:
        return self.kwh_cpu + self.kwh_ram

    def grams(self, kg_per_kwh: float) -> float:
        return units.grams(self.kwh, kg_per_kwh)


def model_energy(cpu_s: float, wall_s: float, power: PowerInfo) -> Energy:
    return Energy(
        kwh_cpu=units.kwh(cpu_s * power.p_core_w), kwh_ram=units.kwh(wall_s * power.p_ram_w)
    )


def rapl_readable(root: Path = RAPL_ROOT) -> bool:
    """A readable RAPL package-domain counter, found the way CodeCarbon's
    ``is_rapl_available`` scans (``intel-rapl:N/energy_uj`` whose name contains
    "package" or whose directory ends in ``:0``), without importing CodeCarbon."""
    for base in (root / "intel-rapl" / "subsystem", root / "intel-rapl", root):
        if not base.is_dir():
            continue
        for domain in sorted(base.glob("intel-rapl:*")):
            energy = domain / "energy_uj"
            if domain.name.count(":") != 1 or not energy.is_file():
                continue
            try:
                name = (domain / "name").read_text().strip().lower()
            except OSError:
                name = ""
            if ("package" in name or domain.name.endswith(":0")) and os.access(energy, os.R_OK):
                return True
    return False


class TaskTracker(Protocol):
    def start_task(self, task_name: str | None = None) -> None: ...
    def stop_task(self, task_name: str | None = None) -> Any: ...


class RaplMeter:
    """Energy of one trial from a CodeCarbon tracker's tasks (RAPL package energy,
    so it includes whatever else the machine does: hence the quiet gate).

    ``start``/``stop`` run in threads. A trial cancelled while its ``start``
    thread ran leaves a task open; the next ``start`` closes it first, since
    CodeCarbon would otherwise keep it and measure from its old start. Tracker
    failures surface as ``BenchError``."""

    def __init__(self, tracker: TaskTracker):
        self.tracker = tracker
        self._lock = threading.Lock()
        self._open = False

    @classmethod
    def create(cls, output_dir: Path) -> RaplMeter:
        """Builds the tracker (slow: run it in a thread)."""
        from codecarbon import OfflineEmissionsTracker

        tracker = OfflineEmissionsTracker(
            tracking_mode="process",
            save_to_file=False,
            log_level="error",
            allow_multiple_runs=True,
            output_dir=str(output_dir),
        )
        return cls(tracker)

    def start(self) -> None:
        with self._lock:
            if self._open:
                with contextlib.suppress(Exception):
                    self.tracker.stop_task()
                self._open = False
            try:
                self.tracker.start_task()
            except Exception as e:
                raise BenchError(
                    f"the CodeCarbon task did not start: {type(e).__name__}: {e}"
                ) from e
            self._open = True

    def stop(self) -> Energy:
        with self._lock:
            self._open = False
            try:
                data = self.tracker.stop_task()
                if data is None:
                    raise BenchError("the CodeCarbon task returned no energy")
                return Energy(kwh_cpu=float(data.cpu_energy), kwh_ram=float(data.ram_energy))
            except BenchError:
                raise
            except Exception as e:
                raise BenchError(f"the CodeCarbon task failed: {type(e).__name__}: {e}") from e


# -- calibration ------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationResult:
    power: PowerInfo
    p_idle_w: float | None = None
    p_busy_w: float | None = None
    cached: bool = False  # a stored calibration was reused

    @property
    def p_core_w(self) -> float | None:
        """The calibrated per-core power (None when uncalibrated)."""
        return self.power.p_core_w if self.power.badge == "calibrated" else None


def parse_cpu_power(text: str) -> float:
    """Mean ``CPU Power`` in W over a powermetrics output."""
    mw = [int(m) for m in CPU_POWER_RE.findall(text)]
    if not mw:
        raise BenchError("powermetrics printed no CPU power", detail=text[-500:])
    return sum(mw) / len(mw) / 1000.0


async def sample_powermetrics(
    seconds: float, *, procs: ProcRegistry | None = None, interval_ms: int = SAMPLE_MS
) -> float:
    n = max(1, round(seconds * 1000 / interval_ms))
    argv = [power_probe.SUDO, "-n", power_probe.POWERMETRICS, "--samplers", "cpu_power"]
    argv += ["-i", str(interval_ms), "-n", str(n)]
    r = await runner.run(
        argv,
        cwd=Path(tempfile.gettempdir()),
        env=tool_env(),
        timeout=seconds + 10,
        procs=procs,
        label="powermetrics",
    )
    if not r.ok:
        raise BenchError("powermetrics failed", detail=r.tail(10))
    return parse_cpu_power(r.stdout)


@contextlib.asynccontextmanager
async def spinning_thread(procs: ProcRegistry | None = None) -> AsyncIterator[None]:
    """One thread at 100% in a child process, killed on exit."""
    proc = await runner.spawn(
        [sys.executable, "-c", "while True: pass"],
        cwd=Path(tempfile.gettempdir()),
        env=tool_env(),
        procs=procs,
        label="calibration spinner",
    )
    try:
        yield
    finally:
        await runner.reap(proc, procs)


async def run_calibration(
    settings: Settings,
    *,
    force: bool = False,
    procs: ProcRegistry | None = None,
    gate: QuietGate | None = None,
    sampler: Sampler | None = None,
    available: bool | None = None,
) -> CalibrationResult:
    """Measure ``P_core`` with powermetrics and cache it. A stored calibration
    is reused unless ``force``; without passwordless powermetrics the probed
    profile is returned unchanged (plus a note). ``sampler``/``available`` are
    test seams."""
    base = await power_probe.probe(settings, procs=procs)
    if base.badge == "calibrated" and not force:
        return CalibrationResult(power=base, cached=True)
    if available is None:
        available = await asyncio.to_thread(power_probe.powermetrics_ok)
    if not available:
        note = "calibration skipped: `sudo -n powermetrics` needs a password (macOS only)"
        return CalibrationResult(power=base.model_copy(update={"notes": [*base.notes, note]}))
    sample = sampler or (lambda s: sample_powermetrics(s, procs=procs))
    async with gate.exclusive() if gate is not None else contextlib.nullcontext():
        idle = await sample(IDLE_S)
        async with spinning_thread(procs):
            await asyncio.sleep(SETTLE_S)
            busy = await sample(BUSY_S)
    p_core = max(busy - idle, MIN_P_CORE_W)
    note = (
        f"{power_probe.CALIBRATION_NOTE} powermetrics CPU power {idle:.2f} W idle, "
        f"{busy:.2f} W with one busy thread: P_core {p_core:.2f} W"
    )
    notes = [n for n in base.notes if not power_probe.is_calibration_note(n)]
    info = base.model_copy(
        update={
            "power_source": "powermetrics",
            "badge": "calibrated",
            "method": "codecarbon_model",
            "p_core_w": p_core,
            "notes": [*notes, note],
        }
    )
    with contextlib.suppress(OSError):
        power_probe.save_cached(info)
    return CalibrationResult(power=info, p_idle_w=idle, p_busy_w=busy)


async def calibrate(
    settings: Settings, *, force: bool = False, procs: ProcRegistry | None = None
) -> PowerInfo:
    return (await run_calibration(settings, force=force, procs=procs)).power
