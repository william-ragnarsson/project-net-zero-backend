"""``netzero doctor`` and ``netzero calibrate`` with the probe and powermetrics stubbed."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from netzero import config
from netzero.bench import power as power_mod
from netzero.bench import probe as probe_mod
from netzero.bench.power import CalibrationResult
from netzero.cli import app
from netzero.config import Settings
from netzero.events import GridInfo, PowerInfo
from netzero.llm import client as llm
from tests.fake_anthropic import KEY, WORKSPACE, TokenCounter, api_error

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


@pytest.fixture(autouse=True)
def isolated(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """No key, database or ``.env`` from the developer's machine."""
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    return settings


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


@pytest.fixture
def with_key(isolated: Settings, monkeypatch: pytest.MonkeyPatch):
    """A key in the settings and a fake SDK in place of ``anthropic.AsyncAnthropic``."""
    keyed = isolated.model_copy(update={"anthropic_api_key": SecretStr(KEY)})
    monkeypatch.setattr(config, "get_settings", lambda: keyed)

    def use(sdk: TokenCounter) -> TokenCounter:
        monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", lambda **kw: sdk)
        return sdk

    return use


def test_doctor_without_a_key_makes_no_call(stub_probe):
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "api key      not set: demo and replay modes only" in result.output


def test_doctor_checks_the_key_with_a_free_call(stub_probe, with_key):
    sdk = with_key(TokenCounter())
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "✓ api key      accepted for claude-haiku-4-5" in result.output
    assert sdk.models == ["claude-haiku-4-5"]


def test_doctor_shows_the_api_error_for_a_rejected_key(stub_probe, with_key):
    with_key(TokenCounter(api_error(400, WORKSPACE)))
    result = CliRunner().invoke(app, ["doctor"], terminal_width=400)
    assert "✗ api key" in result.output
    assert f"Anthropic API error (400): {WORKSPACE}" in result.output
    assert KEY not in result.output
    # demo and replay runs still work, as with an unreachable database
    assert result.exit_code == 0, result.output


def test_doctor_cannot_tell_while_rate_limited(stub_probe, with_key):
    with_key(TokenCounter(api_error(429, "rate limited", "rate_limit_error")))
    result = CliRunner().invoke(app, ["doctor"], terminal_width=400)
    assert (
        "! api key      could not check: Anthropic API error (429): rate limited" in result.output
    )


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
