"""Hardware power probe (CodeCarbon), cached in ``.netzero-cache/power-profile.json``.

``probe()`` runs ``python -m netzero.bench.probe`` as a trusted child (empty
temp cwd and ``HOME``, so no ``.codecarbon.config`` applies; CodeCarbon's
output dir is in there too). The child asks CodeCarbon 3.2.2 what it would
track with and prints one ``PowerInfo`` JSON line:

* CPU ``_mode == "intel_rapl"`` (``codecarbon.core.cpu.is_rapl_available``):
  ``rapl``/``measured``, energy read per trial with ``start_task``/``stop_task``.
* ``AppleSiliconChip`` (powermetrics): ``powermetrics``/``estimated`` until
  ``netzero.bench.power.calibrate`` stores a measured per-core power.
* otherwise CodeCarbon's TDP model: ``tdp_estimate``/``estimated``.

``p_core_w = tdp_w / cpu_count`` is CodeCarbon's process-mode CPU-load model
(``external/hardware.py CPU._get_power_from_cpu_load``: ``tdp * cpu_percent /
cpu_count / 100``) for one fully busy thread. ``p_ram_w`` is CodeCarbon's RAM
model (``external/ram.py RAM.total_power``). The grid intensity comes from
CodeCarbon's data files (``DataSource``).

CodeCarbon's own powermetrics check runs ``sudo powermetrics`` without ``-n``,
which can prompt for a password, so the child checks with ``sudo -n`` first and
switches CodeCarbon's check off when that fails.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from netzero import paths
from netzero.events import GridInfo, PowerInfo
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import tool_env
from netzero.sandbox.procs import ProcRegistry

if TYPE_CHECKING:
    from netzero.config import Settings

PROBE_TIMEOUT_S = 15.0
SUDO_TIMEOUT_S = 5.0
SUDO = "/usr/bin/sudo"
POWERMETRICS = "/usr/bin/powermetrics"
WORLD = "WORLD"
FALLBACK_TDP_W = 85.0  # codecarbon.external.hardware.POWER_CONSTANT
RAM_MIN_ARM_W = 3.0  # codecarbon.external.ram: ARM floor
RAM_MIN_X86_W = 10.0  # codecarbon.external.ram: x86 floor
WORLD_KG_PER_KWH = 0.475  # codecarbon data: world_average 475 g/kWh
CALIBRATION_NOTE = "calibrated:"  # prefix of the note a calibration leaves
CALIBRATE_HINT = "powermetrics works: run the power calibration to measure the per-core power"


def load_cached() -> PowerInfo | None:
    """The cached probe result, if any. Never runs the probe."""
    try:
        return PowerInfo.model_validate_json(paths.POWER_PROFILE.read_text("utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def save_cached(info: PowerInfo) -> None:
    target = paths.POWER_PROFILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(info.model_dump_json(indent=2), "utf-8")
    os.replace(tmp, target)


def normalize_country(country: str | None) -> str | None:
    c = (country or "").strip().upper()
    return c or None


def grid_matches(info: PowerInfo, country: str | None) -> bool:
    return info.grid.country_iso == (country or WORLD)


def powermetrics_ok(timeout: float = SUDO_TIMEOUT_S) -> bool:
    """``sudo -n powermetrics`` runs without a password (``-n`` never prompts)."""
    if sys.platform != "darwin" or not (os.path.exists(SUDO) and os.path.exists(POWERMETRICS)):
        return False
    argv = [SUDO, "-n", POWERMETRICS, "--samplers", "cpu_power", "-n", "1", "-i", "1"]
    try:
        r = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=tool_env(),  # it runs in the netzero process too: no API keys for sudo
            timeout=timeout,
            start_new_session=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def fallback(country: str | None, why: str) -> PowerInfo:
    """A conservative estimate that needs neither CodeCarbon nor a child process:
    CodeCarbon's generic 85 W TDP and its RAM-model floor, at the world grid average."""
    count = os.cpu_count() or 1
    arm = platform.machine().lower() in ("arm64", "aarch64")
    source = "codecarbon world average"
    if country:
        source += f" (energy mix for {country} not loaded)"
    return PowerInfo(
        power_source="tdp_estimate",
        badge="estimated",
        method="codecarbon_model",
        cpu_model=platform.processor() or platform.machine() or "unknown",
        tdp_w=FALLBACK_TDP_W,
        cpu_count=count,
        p_core_w=FALLBACK_TDP_W / count,
        p_ram_w=RAM_MIN_ARM_W if arm else RAM_MIN_X86_W,
        grid=GridInfo(country_iso=country or WORLD, kg_per_kwh=WORLD_KG_PER_KWH, source=source),
        notes=[why, "conservative estimate: CodeCarbon's generic 85 W TDP over all cores"],
    )


def is_calibration_note(note: str) -> bool:
    return note.startswith(CALIBRATION_NOTE) or note == CALIBRATE_HINT


def keep_calibration(info: PowerInfo, cached: PowerInfo | None) -> PowerInfo:
    """Carry a stored calibration over a re-probe of the same CPU."""
    if (
        cached is None
        or cached.badge != "calibrated"
        or info.badge != "estimated"
        or cached.cpu_model != info.cpu_model
    ):
        return info
    kept = [n for n in cached.notes if n.startswith(CALIBRATION_NOTE)]
    return info.model_copy(
        update={
            "power_source": cached.power_source,
            "badge": "calibrated",
            "p_core_w": cached.p_core_w,
            "notes": [n for n in info.notes if not is_calibration_note(n)] + kept,
        }
    )


async def probe(
    settings: Settings, *, refresh: bool = False, procs: ProcRegistry | None = None
) -> PowerInfo:
    """The machine's ``PowerInfo``: the cache when it has the configured grid,
    else a fresh probe. Never raises (cancellation aside): a failed probe falls
    back to the cache or to ``fallback()``, which is not cached."""
    country = normalize_country(settings.country)
    cached = load_cached()
    if cached is not None and not refresh and grid_matches(cached, country):
        return cached
    try:
        info = await _run_child(country, procs)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        why = f"power probe failed: {type(e).__name__}: {e}"[:300]
        if cached is not None and grid_matches(cached, country):
            return cached.model_copy(update={"notes": [*cached.notes, f"{why}; using the cache"]})
        return fallback(country, why)
    info = keep_calibration(info, cached)
    with contextlib.suppress(OSError):
        save_cached(info)
    return info


async def _run_child(country: str | None, procs: ProcRegistry | None) -> PowerInfo:
    with tempfile.TemporaryDirectory(prefix="netzero-probe-") as tmp:
        argv = [sys.executable, "-m", "netzero.bench.probe", "--output-dir", tmp]
        if country:
            argv += ["--country", country]
        r = await runner.run(
            argv,
            cwd=Path(tmp),
            env=tool_env(extra={"HOME": tmp, "PYTHONPATH": str(paths.ROOT)}),
            timeout=PROBE_TIMEOUT_S,
            procs=procs,
            label="power probe",
        )
    if not r.ok:
        why = "timed out" if r.timed_out else f"exit {r.returncode}"
        raise RuntimeError(f"{why}: {r.tail(3)}")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError("no result on stdout")
    return PowerInfo.model_validate_json(lines[-1])


# -- the child --------------------------------------------------------------------------


def _apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _grid(country: str | None, notes: list[str]) -> tuple[GridInfo, bool]:
    """The grid intensity, and whether CodeCarbon has an energy mix for ``country``."""
    from codecarbon.core.emissions import Emissions
    from codecarbon.core.units import EmissionsPerKWh
    from codecarbon.input import DataSource

    ds = DataSource()
    mix = ds.get_global_energy_mix_data()
    if country and country in mix:
        kg = Emissions._global_energy_mix_to_emissions_rate(mix[country]).kgs_per_kWh
        source = f"codecarbon energy mix ({country})"
        return GridInfo(country_iso=country, kg_per_kwh=kg, source=source), True
    world = ds.get_carbon_intensity_per_source_data()["world_average"]
    kg = EmissionsPerKWh.from_g_per_kWh(world).kgs_per_kWh
    if country:
        notes.append(f"CodeCarbon has no energy mix for {country}: world average used")
        source = f"codecarbon world average (no energy mix for {country})"
        return GridInfo(country_iso=country, kg_per_kwh=kg, source=source), False
    return GridInfo(country_iso=WORLD, kg_per_kwh=kg, source="codecarbon world average"), False


def detect(country: str | None, output_dir: str) -> PowerInfo:
    """What CodeCarbon would measure with on this machine (runs in the child)."""
    from codecarbon.core import powermetrics as cc_powermetrics

    if not (_apple_silicon() and powermetrics_ok()):
        cc_powermetrics.is_powermetrics_available = lambda: False

    from codecarbon import OfflineEmissionsTracker
    from codecarbon.core.cpu import TDP
    from codecarbon.external.hardware import CPU, POWER_CONSTANT, AppleSiliconChip
    from codecarbon.external.ram import RAM

    notes: list[str] = []
    grid, has_mix = _grid(country, notes)
    tracker = OfflineEmissionsTracker(
        tracking_mode="process",
        save_to_file=False,
        log_level="error",
        allow_multiple_runs=True,  # no lock file, no signal handlers
        output_dir=output_dir,
        country_iso_code=country if has_mix else None,
    )
    hardware = tracker._hardware
    cpu = next((h for h in hardware if isinstance(h, CPU)), None)
    chip = next((h for h in hardware if isinstance(h, AppleSiliconChip)), None)
    ram = next((h for h in hardware if isinstance(h, RAM)), None)
    cpu_count = int(tracker._conf.get("cpu_count") or os.cpu_count() or 1)

    if cpu is not None and cpu._mode == "intel_rapl":
        source, badge, method = "rapl", "measured", "codecarbon_task"
        notes.append("CPU energy read from RAPL (package domain) per trial via CodeCarbon tasks")
    elif chip is not None:
        source, badge, method = "powermetrics", "estimated", "codecarbon_model"
        notes.append(CALIBRATE_HINT)
    else:
        source, badge, method = "tdp_estimate", "estimated", "codecarbon_model"
        notes.append("estimated from CodeCarbon's TDP model × measured CPU time")

    if cpu is not None and cpu._mode in ("constant", "cpu_load"):
        tdp_w, generic = float(cpu._tdp), bool(cpu._is_generic_tdp)
    else:
        tdp = TDP().tdp
        physical = int(tracker._conf.get("cpu_physical_count") or 1)
        tdp_w, generic = (float(tdp) * physical, False) if tdp else (float(POWER_CONSTANT), True)
    if generic:
        notes.append(f"CPU not in CodeCarbon's TDP table: generic {tdp_w:g} W")

    arm = platform.machine().lower() in ("arm64", "aarch64")
    p_ram = float(ram.total_power().W) if ram is not None else 0.0
    return PowerInfo(
        power_source=source,
        badge=badge,
        method=method,
        cpu_model=str(tracker._conf.get("cpu_model") or "unknown"),
        tdp_w=tdp_w,
        cpu_count=cpu_count,
        p_core_w=tdp_w / cpu_count,
        p_ram_w=p_ram or (RAM_MIN_ARM_W if arm else RAM_MIN_X86_W),
        grid=grid,
        notes=notes,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m netzero.bench.probe")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--country")
    opts = parser.parse_args(argv)
    with contextlib.redirect_stdout(sys.stderr):  # stdout carries the result only
        info = detect(normalize_country(opts.country), opts.output_dir)
    print(info.model_dump_json())


if __name__ == "__main__":
    main()
