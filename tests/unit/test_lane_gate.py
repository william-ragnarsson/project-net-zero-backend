"""The quiet gate: concurrent checks, lone benches, writer preference, cancellation;
and the lane's calibration against a simulated worker."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Literal

import pytest

from netzero.bench import lane as lane_mod
from netzero.bench.lane import BenchLane, QuietGate
from netzero.config import Settings
from netzero.events import GridInfo, PowerInfo

Mode = Literal["shared", "exclusive"]


class Holder:
    """A task that enters the gate, records it and stays inside until released."""

    def __init__(self, gate: QuietGate, mode: Mode, log: list[str], name: str):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        cm: AbstractAsyncContextManager[None] = getattr(gate, mode)()
        self.task = asyncio.create_task(self._run(cm, log, name))

    async def _run(self, cm: AbstractAsyncContextManager[None], log: list[str], name: str):
        async with cm:
            log.append(name)
            self.entered.set()
            await self.release.wait()

    async def leave(self) -> None:
        self.release.set()
        await self.task


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_shared_holders_overlap():
    gate, log = QuietGate(), []
    a, b = Holder(gate, "shared", log, "a"), Holder(gate, "shared", log, "b")
    await settle()
    assert log == ["a", "b"] and gate.readers == 2
    await a.leave()
    await b.leave()
    assert gate.readers == 0 and not gate.exclusive_held


async def test_exclusive_waits_for_readers_and_blocks_them():
    gate, log = QuietGate(), []
    r1 = Holder(gate, "shared", log, "r1")
    await settle()
    w = Holder(gate, "exclusive", log, "w")
    await settle()
    assert log == ["r1"]
    await r1.leave()
    await settle()
    assert log == ["r1", "w"] and gate.exclusive_held and gate.readers == 0
    r2 = Holder(gate, "shared", log, "r2")
    w2 = Holder(gate, "exclusive", log, "w2")
    await settle()
    assert log == ["r1", "w"]
    await w.leave()
    await settle()
    assert log == ["r1", "w", "w2"]  # waiting benches go before checks
    await w2.leave()
    await settle()
    assert log[-1] == "r2"
    await r2.leave()
    assert gate.readers == 0 and not gate.exclusive_held


async def test_writer_preference():
    """A check arriving while a bench waits queues behind the bench."""
    gate, log = QuietGate(), []
    r1 = Holder(gate, "shared", log, "r1")
    await settle()
    w = Holder(gate, "exclusive", log, "w")
    await settle()
    r2 = Holder(gate, "shared", log, "r2")
    await settle()
    assert log == ["r1"] and gate.writers_waiting == 1
    await r1.leave()
    await settle()
    assert log == ["r1", "w"]
    await w.leave()
    await settle()
    assert log == ["r1", "w", "r2"]
    await r2.leave()


async def test_writers_go_in_order():
    gate, log = QuietGate(), []
    holders = [Holder(gate, "exclusive", log, f"w{i}") for i in range(3)]
    await settle()
    assert log == ["w0"]
    for i, h in enumerate(holders):
        await h.leave()
        await settle()
        assert log == [f"w{j}" for j in range(min(i + 2, 3))]
    assert not gate.exclusive_held


async def test_cancelled_waiting_writer_lets_readers_in():
    gate, log = QuietGate(), []
    r1 = Holder(gate, "shared", log, "r1")
    await settle()
    w = Holder(gate, "exclusive", log, "w")
    await settle()
    r2 = Holder(gate, "shared", log, "r2")
    await settle()
    assert log == ["r1"]
    w.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w.task
    await settle()
    assert log == ["r1", "r2"] and gate.readers == 2 and gate.writers_waiting == 0
    await r1.leave()
    await r2.leave()
    assert gate.readers == 0 and not gate.exclusive_held


async def test_cancel_after_grant_gives_it_back():
    """The release hands the gate over; the new holder is cancelled before it resumes."""
    gate, log = QuietGate(), []
    held = gate.exclusive()
    await held.__aenter__()
    w = Holder(gate, "exclusive", log, "w")
    await settle()
    await held.__aexit__(None, None, None)  # synchronous: grants w, which has not run yet
    assert gate.exclusive_held and log == []
    w.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w.task
    assert log == [] and not gate.exclusive_held
    r = Holder(gate, "shared", log, "r")
    await settle()
    assert log == ["r"]
    await r.leave()


async def test_cancel_after_shared_grant_gives_it_back():
    gate, log = QuietGate(), []
    held = gate.exclusive()
    await held.__aenter__()
    r = Holder(gate, "shared", log, "r")
    await settle()
    await held.__aexit__(None, None, None)
    assert gate.readers == 1 and log == []
    r.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await r.task
    assert gate.readers == 0 and log == []
    async with gate.exclusive():
        pass


async def test_cancelled_waiting_reader_leaves_the_queue():
    gate, log = QuietGate(), []
    w = Holder(gate, "exclusive", log, "w")
    await settle()
    r = Holder(gate, "shared", log, "r")
    await settle()
    r.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await r.task
    await w.leave()
    assert gate.readers == 0 and not gate.exclusive_held and log == ["w"]


async def test_an_error_inside_releases():
    gate = QuietGate()
    with pytest.raises(RuntimeError):
        async with gate.exclusive():
            raise RuntimeError("bench failed")
    with pytest.raises(RuntimeError):
        async with gate.shared():
            raise RuntimeError("check failed")
    assert gate.readers == 0 and not gate.exclusive_held
    async with gate.exclusive():
        assert gate.exclusive_held


async def test_a_cancelled_exclusive_holder_releases():
    gate = QuietGate()
    w = Holder(gate, "exclusive", [], "w")
    await w.entered.wait()
    r = Holder(gate, "shared", [], "r")
    await settle()
    w.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w.task
    await r.entered.wait()
    assert gate.readers == 1 and not gate.exclusive_held
    await r.leave()


# -- calibration --------------------------------------------------------------------------

POWER = PowerInfo(
    power_source="tdp_estimate",
    badge="estimated",
    method="codecarbon_model",
    cpu_model="test",
    tdp_w=10.0,
    cpu_count=8,
    p_core_w=1.25,
    p_ram_w=3.0,
    grid=GridInfo(country_iso="WORLD", kg_per_kwh=0.475, source="test"),
)


class SimWorker:
    """Answers ``time`` with the summed cost of the samples the calls would replay,
    and fails a command whose simulated time is over its timeout (as a real one would)."""

    def __init__(self, costs: list[float]):
        self.costs = costs
        self.samples = len(costs)
        self.warmed = False
        self.walls: list[float] = []

    async def request(self, cmd: dict[str, Any], timeout: float) -> dict[str, Any]:
        if cmd["cmd"] == "warmup":
            wall = sum(self.costs)
        else:
            m, start = len(self.costs), cmd.get("start", 0)
            wall = sum(self.costs[(start + i) % m] for i in range(cmd["n"]))
        assert wall <= timeout, f"{cmd} would take {wall:g} s, over its {timeout:g} s limit"
        self.walls.append(wall)
        return {"ok": True, "wall_s": wall, "n": cmd.get("n", 1)}


def sim_lane(settings: Settings, tmp_path: Path, **update: float) -> BenchLane:
    return BenchLane(
        settings.model_copy(update=update),
        POWER,
        venv=tmp_path / "venv",
        harness_root=tmp_path / "harness",
        home=tmp_path,
        tmp=tmp_path,
    )


async def test_calibration_does_not_overshoot_on_a_cheap_first_sample(
    settings: Settings, tmp_path: Path
):
    """The first sample is cheap and the other is not: the probe must not jump
    100x on the cheap one (100 calls = 50 x 3 s, past the 120 s limit)."""
    lane = sim_lane(settings, tmp_path, trial_target_s=0.75, test_timeout_s=120.0)
    w = SimWorker([1e-6, 3.0])
    cal = await lane.calibrate(w)  # type: ignore[arg-type]
    assert max(w.walls) <= sum(w.costs)  # never more than one pass (= the warmup)
    assert cal.calls_per_trial == 1 and cal.n_samples == 2
    assert cal.est_call_s == pytest.approx(1.5)
    assert not lane.gate.exclusive_held


async def test_calibration_probe_stays_near_its_goal(settings: Settings, tmp_path: Path):
    costs = [1e-6] * 9 + [1e-2]  # one expensive sample in ten
    lane = sim_lane(settings, tmp_path, trial_target_s=1.0)
    w = SimWorker(costs)
    cal = await lane.calibrate(w)  # type: ignore[arg-type]
    goal = 1.0 * lane_mod.PROBE_FRACTION
    assert max(w.walls[1:]) < 3 * goal  # past the warmup, no probe runs far past the goal
    per_call = sum(costs) / len(costs)
    assert cal.est_call_s == pytest.approx(per_call, rel=0.2)
    assert cal.calls_per_trial % len(costs) == 0


@pytest.mark.parametrize(
    ("m", "want"),
    [
        (3, 99),  # whole passes: round(100 / 3) * 3
        (120, 120),  # one pass is 1.2x the target: worth it
        (200, 100),  # one pass would be 2x: keep the target, cover the samples over trials
        (1, 100),
    ],
)
async def test_calibration_rounds_to_whole_passes_when_they_fit(
    settings: Settings, tmp_path: Path, m: int, want: int
):
    lane = sim_lane(settings, tmp_path, trial_target_s=0.1)
    cal = await lane.calibrate(SimWorker([1e-3] * m))  # type: ignore[arg-type]
    assert cal.calls_per_trial == want
    assert cal.calls_per_trial * cal.est_call_s <= lane_mod.WHOLE_PASS_SLACK * 0.1


async def test_calibration_of_a_call_longer_than_the_target(settings: Settings, tmp_path: Path):
    lane = sim_lane(settings, tmp_path, trial_target_s=0.75)
    cal = await lane.calibrate(SimWorker([2.0, 2.0]))  # type: ignore[arg-type]
    assert cal.calls_per_trial == 1 and cal.est_call_s == pytest.approx(2.0)


async def test_compare_needs_two_trials(settings: Settings, tmp_path: Path):
    from netzero.errors import BenchError
    from netzero.events import Calibration

    lane = sim_lane(settings, tmp_path, n_trials=1)
    cal = Calibration(calls_per_trial=1, est_call_s=1e-3, n_samples=1)
    with pytest.raises(BenchError):
        await lane.compare(SimWorker([1e-3]), SimWorker([1e-3]), cal)  # type: ignore[arg-type]
    assert not lane.gate.exclusive_held
