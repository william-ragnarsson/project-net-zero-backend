"""``netzero doctor`` and ``netzero calibrate`` with the probe and powermetrics stubbed."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from netzero.bench import power as power_mod
from netzero.bench import probe as probe_mod
from netzero.bench.power import CalibrationResult
from netzero.cli import app
from netzero.events import GridInfo, PowerInfo

ESTIMATED = PowerInfo(
    power_source="tdp_estimate",
    badge="estimated",
    method="codecarbon_model",
    cpu_model="test cpu",
    tdp_w=40.0,
    cpu_count=8,
    p_core_w=5.0,
    p_ram_w=3.0,
    grid=GridInfo(country_iso="WORLD", kg_per_kwh=0.475, source="test"),
)
CALIBRATED = ESTIMATED.model_copy(
    update={"power_source": "powermetrics", "badge": "calibrated", "p_core_w": 2.5}
)


@pytest.fixture
def stub_probe(monkeypatch: pytest.MonkeyPatch):
    async def probe(settings, *, refresh=False, procs=None):
        return ESTIMATED

    monkeypatch.setattr(probe_mod, "probe", probe)
    monkeypatch.setattr(probe_mod, "powermetrics_ok", lambda timeout=5.0: False)


def test_doctor_reports_each_check(stub_probe):
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    for name in ("git", "uv", "python", "api key", "power"):
        assert name in result.output
    assert "estimated via tdp_estimate: P_core 5.00 W" in result.output


def test_doctor_fails_without_git(stub_probe, monkeypatch: pytest.MonkeyPatch):
    import shutil

    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: None if name == "git" else real(name)
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "not found: install git" in result.output


def test_calibrate_reports_the_measured_core_power(monkeypatch: pytest.MonkeyPatch):
    async def run(settings, *, force=False, **_):
        assert force
        return CalibrationResult(power=CALIBRATED, p_idle_w=1.0, p_busy_w=3.5)

    monkeypatch.setattr(power_mod, "run_calibration", run)
    result = CliRunner().invoke(app, ["calibrate", "--force"])
    assert result.exit_code == 0, result.output
    assert "P_core 2.50 W" in result.output


def test_calibrate_without_powermetrics_exits_1(monkeypatch: pytest.MonkeyPatch):
    async def run(settings, *, force=False, **_):
        note = "calibration skipped: `sudo -n powermetrics` needs a password (macOS only)"
        return CalibrationResult(power=ESTIMATED.model_copy(update={"notes": [note]}))

    monkeypatch.setattr(power_mod, "run_calibration", run)
    result = CliRunner().invoke(app, ["calibrate"])
    assert result.exit_code == 1
    assert "power stays estimated" in result.output
