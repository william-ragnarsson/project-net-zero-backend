"""The whole pipeline on the demo repo, with the model replies from the cassettes.

Nothing is mocked below the LLM: the repo is copied and committed, a venv is
built from the demo lockfile (offline, after ``make setup``), tests run,
inputs are captured and every candidate is checked and benchmarked for real.
Benchmarks need a quiet machine, so run this with nothing heavy alongside:
``make test-e2e``.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest

from netzero import paths
from netzero.api.schemas import CreateRunRequest
from netzero.config import Settings
from netzero.pipeline.clone import copy_demo
from netzero.pipeline.grammar import check_grammar
from netzero.pipeline.orchestrator import RunManager
from netzero.pipeline.store import read_complete_lines

pytestmark = pytest.mark.e2e

WINS = (
    "textkit.dedupe:dedupe_preserve_order",
    "algos.primes:primes_below",
    "algos.pairs:has_pair_with_sum",
)
MUTATES = "datakit.rank:top_k_inplace"  # every candidate breaks the in-place contract
SKIPS = {"algos.walk:random_walk", "datakit.rates:fetch_exchange_rates"}


async def _wait_for(pred, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.1)


@pytest.fixture
async def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key in ("ANTHROPIC_API_KEY", "NETZERO_FAKE_PIPELINE"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None, ANTHROPIC_API_KEY=None, runs_dir=tmp_path / "runs")  # type: ignore[call-arg]
    m = RunManager(settings)
    m.start()
    yield m
    await m.shutdown(timeout=10)


async def test_demo_run_end_to_end(manager: RunManager, tmp_path: Path):
    s = await manager.create(CreateRunRequest(demo=True))
    assert s.mode == "demo"
    ctx = manager.live(s.id)
    assert ctx is not None
    await _wait_for(lambda: ctx.awaiting_selection or ctx.state in ("failed", "completed"), 600)
    assert ctx.awaiting_selection, f"the prelude ended in {ctx.state}"

    detail = manager.detail(s.id)
    assert detail is not None
    skipped = {t.function_id for t in detail.triage if t.skip_reason}
    assert SKIPS <= skipped
    assert not {t.function_id for t in detail.triage if t.preselected} & SKIPS

    manager.select(s.id, [*WINS, MUTATES])
    await asyncio.wait_for(manager.wait(s.id), 3600)

    detail = manager.detail(s.id)
    assert detail is not None and detail.state == "completed", detail and detail.error
    lines, _ = read_complete_lines(manager.store.paths(s.id).events)
    assert check_grammar(lines) == []

    outcomes = {f.function_id: f for f in detail.functions}
    for fid in WINS:
        rec = outcomes[fid]
        assert rec.outcome == "accepted", f"{fid}: {rec.outcome} ({rec.reason})"
        assert rec.delta_pct is not None and rec.delta_pct <= -5
    assert outcomes[MUTATES].outcome != "accepted"
    assert detail.llm_cost_usd == 0  # the cassettes bill nothing

    # the patch applies to a fresh copy of the demo repo (same base commit)
    assert detail.artifacts is not None and detail.artifacts.patch is not None
    patch = manager.store.paths(s.id).out / f"{s.id}.patch"
    fresh = tmp_path / "fresh"
    await copy_demo(paths.DEMO_REPO, fresh)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=fresh, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=fresh, check=True)
    changed = subprocess.run(
        ["git", "diff", "--name-only"], cwd=fresh, check=True, capture_output=True, text=True
    ).stdout.split()
    assert {"textkit/dedupe.py", "algos/primes.py", "algos/pairs.py"} <= set(changed)
    assert "datakit/rank.py" not in changed
    for name in changed:
        ast.parse((fresh / name).read_text("utf-8"), name)
