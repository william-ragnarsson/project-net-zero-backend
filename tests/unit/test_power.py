"""The energy model against CodeCarbon, the RAPL meter, the probe cache and the calibration."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from netzero import paths
from netzero.bench import power, probe, units
from netzero.bench.lane import QuietGate
from netzero.config import Settings
from netzero.errors import BenchError
from netzero.events import GridInfo, PowerInfo


def info(
    *,
    country: str = "WORLD",
    badge: str = "estimated",
    cpu_model: str = "Test CPU",
    p_core_w: float = 1.25,
    notes: list[str] | None = None,
) -> PowerInfo:
    return PowerInfo(
        power_source="tdp_estimate",
        badge=badge,  # type: ignore[arg-type]
        method="codecarbon_model",
        cpu_model=cpu_model,
        tdp_w=10.0,
        cpu_count=8,
        p_core_w=p_core_w,
        p_ram_w=3.0,
        grid=GridInfo(country_iso=country, kg_per_kwh=0.475, source="test"),
        notes=notes or [],
    )


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The cached power profile, redirected into the test's tmp dir."""
    target = tmp_path / "cache" / "power-profile.json"
    monkeypatch.setattr(paths, "POWER_PROFILE", target)
    return target


class FakeChild:
    """Stands in for the probe subprocess."""

    def __init__(self, result: PowerInfo | Exception):
        self.result = result
        self.calls: list[str | None] = []

    async def __call__(self, country: str | None, procs: object) -> PowerInfo:
        self.calls.append(country)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


# -- the model ----------------------------------------------------------------------------


def test_p_core_is_codecarbon_process_cpu_load_for_one_busy_core():
    """``tdp * cpu_percent / cpu_count / 100`` at 100% of one core, over one hour."""
    from codecarbon.core.units import Energy, Time
    from codecarbon.external.hardware import CPU

    cpu = CPU(tempfile.gettempdir(), "cpu_load", "Test CPU", 10, tracking_mode="process")
    cpu._process = SimpleNamespace(cpu_percent=lambda interval=None: 100.0)
    cpu._cpu_count = 8
    watts = cpu._get_power_from_cpu_load()
    p = info(p_core_w=10 / 8)
    assert watts.W == pytest.approx(p.p_core_w)
    want = Energy.from_power_and_time(power=watts, time=Time.from_seconds(3600)).kWh
    assert power.model_energy(3600.0, 0.0, p).kwh_cpu == pytest.approx(want)


def test_model_energy_and_grams():
    e = power.model_energy(cpu_s=2.0, wall_s=4.0, power=info(p_core_w=1.5))
    assert e.kwh_cpu == pytest.approx(3.0 / units.J_PER_KWH)
    assert e.kwh_ram == pytest.approx(12.0 / units.J_PER_KWH)
    assert e.kwh == pytest.approx(15.0 / units.J_PER_KWH)
    assert e.grams(0.475) == pytest.approx(15.0 / 3.6e6 * 475)


def test_units():
    assert units.kwh(3.6e6) == 1.0
    assert units.grams(2.0, 0.5) == 1000.0
    assert units.saved_per_million(3e-6, 1e-6) == pytest.approx(2.0)
    assert units.saved_per_million(1e-6, 3e-6) == pytest.approx(-2.0)


# -- RAPL ---------------------------------------------------------------------------------


def _domain(base: Path, name: str, label: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "energy_uj").write_text("123\n")
    (d / "name").write_text(f"{label}\n")
    return d


def test_rapl_readable(tmp_path: Path):
    assert not power.rapl_readable(tmp_path)
    sub = tmp_path / "intel-rapl" / "subsystem"
    _domain(sub, "intel-rapl:0:0", "core")  # a sub-domain is not the package
    assert not power.rapl_readable(tmp_path)
    _domain(sub, "intel-rapl:1", "psys")
    assert not power.rapl_readable(tmp_path)
    _domain(sub, "intel-rapl:0", "package-0")
    assert power.rapl_readable(tmp_path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
def test_rapl_unreadable(tmp_path: Path):
    d = _domain(tmp_path / "intel-rapl", "intel-rapl:0", "package-0")
    (d / "energy_uj").chmod(0)
    try:
        assert not power.rapl_readable(tmp_path)
    finally:
        (d / "energy_uj").chmod(0o644)


class FakeTracker:
    def __init__(self, data: object):
        self.data = data
        self.started = 0

    def start_task(self, task_name: str | None = None) -> None:
        self.started += 1

    def stop_task(self, task_name: str | None = None) -> object:
        return self.data


def test_rapl_meter():
    tracker = FakeTracker(SimpleNamespace(cpu_energy=2e-6, ram_energy=1e-6))
    meter = power.RaplMeter(tracker)
    meter.start()
    e = meter.stop()
    assert tracker.started == 1
    assert (e.kwh_cpu, e.kwh_ram) == (2e-6, 1e-6)


def test_rapl_meter_without_data():
    meter = power.RaplMeter(FakeTracker(None))
    meter.start()
    with pytest.raises(BenchError):
        meter.stop()


class LoggingTracker:
    """CodeCarbon-like: a second ``start_task`` while a task is open is ignored."""

    def __init__(self, fail: str = ""):
        self.fail = fail
        self.log: list[str] = []
        self.active = False

    def start_task(self, task_name: str | None = None) -> None:
        if self.fail == "start":
            raise RuntimeError("no RAPL")
        self.log.append("start" if not self.active else "start ignored")
        self.active = True

    def stop_task(self, task_name: str | None = None) -> object:
        if self.fail == "stop":
            raise PermissionError("energy_uj")
        was, self.active = self.active, False
        self.log.append("stop")
        return SimpleNamespace(cpu_energy=1e-6, ram_energy=0.0) if was else None


def test_rapl_meter_closes_a_task_a_cancelled_trial_left_open():
    tracker = LoggingTracker()
    meter = power.RaplMeter(tracker)
    meter.start()  # its trial was cancelled before the stop
    meter.start()
    assert meter.stop().kwh_cpu == 1e-6
    assert tracker.log == ["start", "stop", "start", "stop"]


@pytest.mark.parametrize("fail", ["start", "stop"])
def test_rapl_meter_failures_are_bench_errors(fail: str):
    meter = power.RaplMeter(LoggingTracker(fail=fail))
    with pytest.raises(BenchError, match="CodeCarbon task"):
        meter.start()
        meter.stop()


# -- the probe and its cache --------------------------------------------------------------


async def test_probe_uses_the_cache(settings: Settings, profile: Path, monkeypatch):
    probe.save_cached(info(notes=["cached"]))
    child = FakeChild(RuntimeError("must not run"))
    monkeypatch.setattr(probe, "_run_child", child)
    got = await probe.probe(settings)
    assert got.notes == ["cached"] and child.calls == []
    assert probe.load_cached() == got


async def test_probe_reprobes_for_another_grid(settings: Settings, profile: Path, monkeypatch):
    probe.save_cached(info())
    child = FakeChild(info(country="SWE"))
    monkeypatch.setattr(probe, "_run_child", child)
    got = await probe.probe(settings.model_copy(update={"country": " swe "}))
    assert child.calls == ["SWE"]
    assert got.grid.country_iso == "SWE"
    assert probe.load_cached() == got


async def test_probe_refresh(settings: Settings, profile: Path, monkeypatch):
    probe.save_cached(info())
    child = FakeChild(info(p_core_w=2.0))
    monkeypatch.setattr(probe, "_run_child", child)
    got = await probe.probe(settings, refresh=True)
    assert child.calls == [None] and got.p_core_w == 2.0


async def test_probe_failure_falls_back_uncached(settings: Settings, profile: Path, monkeypatch):
    monkeypatch.setattr(probe, "_run_child", FakeChild(RuntimeError("exit 1: boom")))
    got = await probe.probe(settings.model_copy(update={"country": "SWE"}))
    assert got.badge == "estimated" and got.power_source == "tdp_estimate"
    assert got.tdp_w == probe.FALLBACK_TDP_W
    assert got.p_core_w == pytest.approx(probe.FALLBACK_TDP_W / got.cpu_count)
    # the configured grid's code, at the world average, and the source says so
    assert got.grid.country_iso == "SWE"
    assert got.grid.kg_per_kwh == probe.WORLD_KG_PER_KWH
    assert got.grid.source.startswith("codecarbon world average") and "SWE" in got.grid.source
    assert any("power probe failed: RuntimeError: exit 1: boom" in n for n in got.notes)
    assert probe.load_cached() is None  # a fallback is never cached


async def test_probe_failure_keeps_a_matching_cache(settings: Settings, profile: Path, monkeypatch):
    probe.save_cached(info())
    monkeypatch.setattr(probe, "_run_child", FakeChild(RuntimeError("boom")))
    got = await probe.probe(settings, refresh=True)
    assert got.p_core_w == 1.25
    assert got.notes[-1].endswith("using the cache")


async def test_probe_cancellation_propagates(settings: Settings, profile: Path, monkeypatch):
    monkeypatch.setattr(probe, "_run_child", FakeChild(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await probe.probe(settings)


def test_keep_calibration():
    note = f"{probe.CALIBRATION_NOTE} P_core 4.50 W"
    cached = info(badge="calibrated", p_core_w=4.5, notes=[note, "old"])
    fresh = info(p_core_w=1.25, notes=[probe.CALIBRATE_HINT, "new"])
    kept = probe.keep_calibration(fresh, cached)
    assert kept.badge == "calibrated" and kept.p_core_w == 4.5
    assert kept.notes == ["new", note]
    other_cpu = info(cpu_model="Other CPU")
    assert probe.keep_calibration(other_cpu, cached) == other_cpu
    assert probe.keep_calibration(fresh, None) == fresh


def test_load_cached_ignores_garbage(profile: Path):
    assert probe.load_cached() is None
    profile.parent.mkdir(parents=True)
    profile.write_text("{not json")
    assert probe.load_cached() is None


# -- calibration --------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def no_spinner(procs: object = None) -> AsyncIterator[None]:
    yield


class FakeSampler:
    def __init__(self, *watts: float, gate: QuietGate | None = None):
        self.watts = list(watts)
        self.gate = gate
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> float:
        if self.gate is not None:
            assert self.gate.exclusive_held
        self.calls.append(seconds)
        return self.watts.pop(0)


@pytest.fixture
def calib(settings: Settings, profile: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    probe.save_cached(info(notes=[probe.CALIBRATE_HINT]))
    monkeypatch.setattr(probe, "_run_child", FakeChild(RuntimeError("cache only")))
    monkeypatch.setattr(power, "spinning_thread", no_spinner)
    monkeypatch.setattr(power, "SETTLE_S", 0.0)
    return settings


async def test_calibration(calib: Settings):
    gate = QuietGate()
    sampler = FakeSampler(2.0, 6.5, gate=gate)
    r = await power.run_calibration(calib, sampler=sampler, available=True, gate=gate)
    assert sampler.calls == [power.IDLE_S, power.BUSY_S]
    assert (r.p_idle_w, r.p_busy_w, r.p_core_w) == (2.0, 6.5, 4.5)
    assert r.power.badge == "calibrated" and r.power.power_source == "powermetrics"
    assert r.power.method == "codecarbon_model"
    assert probe.CALIBRATE_HINT not in r.power.notes
    assert probe.is_calibration_note(r.power.notes[-1]) and "P_core 4.50 W" in r.power.notes[-1]
    assert probe.load_cached() == r.power
    assert not gate.exclusive_held

    again = await power.run_calibration(calib, sampler=FakeSampler(), available=True)
    assert again.cached and again.power == r.power
    forced = await power.run_calibration(
        calib, force=True, sampler=FakeSampler(2.0, 5.0), available=True
    )
    assert forced.p_core_w == 3.0
    assert sum(probe.is_calibration_note(n) for n in forced.power.notes) == 1


async def test_calibration_floor(calib: Settings):
    r = await power.run_calibration(calib, sampler=FakeSampler(3.0, 3.2), available=True)
    assert r.p_core_w == power.MIN_P_CORE_W


async def test_calibration_unavailable(calib: Settings, profile: Path):
    before = profile.read_text()
    r = await power.run_calibration(calib, sampler=FakeSampler(), available=False)
    assert r.p_core_w is None and r.power.badge == "estimated"
    assert "needs a password" in r.power.notes[-1]
    assert profile.read_text() == before


def test_powermetrics_check_gets_no_secrets(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> SimpleNamespace:
        seen.update(kw)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    monkeypatch.setattr(probe.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert probe.powermetrics_ok()
    env = seen["env"]
    assert isinstance(env, dict) and "ANTHROPIC_API_KEY" not in env
    assert seen["stdin"] == probe.subprocess.DEVNULL and seen["start_new_session"]


def test_parse_cpu_power():
    text = "*** Sampled\nCPU Power: 1200 mW\nGPU Power: 5 mW\n***\nCPU Power: 800 mW\n"
    assert power.parse_cpu_power(text) == pytest.approx(1.0)
    with pytest.raises(BenchError):
        power.parse_cpu_power("Password:")
