"""``netzero fake`` and the virtual-time loop it runs on."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from typer.testing import CliRunner

from netzero.cli import app
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.vclock import VirtualTimeLoop


def test_virtual_time_jumps_to_the_next_timer():
    async def main() -> tuple[float, list[str]]:
        loop = asyncio.get_running_loop()
        order: list[str] = []

        async def nap(name: str, s: float) -> None:
            await asyncio.sleep(s)
            order.append(name)

        await asyncio.gather(nap("slow", 3600), nap("fast", 60))
        return loop.time(), order

    t0 = time.monotonic()
    with asyncio.Runner(loop_factory=VirtualTimeLoop) as runner:
        now, order = runner.run(main())
    assert time.monotonic() - t0 < 5
    assert order == ["fast", "slow"]
    assert 3600 <= now < 3601  # concurrent sleeps overlap, they do not add up


def test_fake_writes_a_grammar_valid_replay(tmp_path: Path):
    out = tmp_path / "short.events.jsonl"
    result = CliRunner().invoke(app, ["fake", "--scenario", "short", "--out", str(out)])
    assert result.exit_code == 0, result.output
    lines = out.read_text().splitlines()
    assert_grammar(lines)
    first, last = json.loads(lines[0]), json.loads(lines[-1])
    assert last["type"] == "run.completed"
    assert last["ts"] - first["ts"] > 20_000  # scripted timing survives virtual time


def test_fake_rejects_unknown_scenarios():
    result = CliRunner().invoke(app, ["fake", "--scenario", "nope"])
    assert result.exit_code == 2
