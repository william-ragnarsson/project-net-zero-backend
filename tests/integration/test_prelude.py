"""The real run prelude (clone, env, discovery, triage, selection) on a tiny demo repo."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from netzero import paths as nz_paths
from netzero.pipeline import flow
from netzero.pipeline.clone import copy_demo
from netzero.pipeline.discovery import DiscoveredFunction
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.orchestrator import RunManager
from netzero.pipeline.run import RunContext
from netzero.pipeline.triage import LlmRating
from netzero.pipeline.trunk import TRUNK_BRANCH
from tests.integration.helpers import DEMO_AUTO, event_lines, events

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv"),
]

TINY = {
    "README.md": "a tiny repo\n",
    "pkg/__init__.py": "",
    "pkg/text.py": """
        def dedupe(items):
            out = []
            for x in items:
                if x not in out:
                    out.append(x)
            return out


        def pairs(xs, target):
            hits = []
            for i in range(len(xs)):
                for j in range(len(xs)):
                    if i != j and xs[i] + xs[j] == target:
                        hits.append((i, j))
            return hits
        """,
    "pkg/files.py": """
        def read_lines(path):
            with open(path) as f:
                data = f.read()
            lines = data.splitlines()
            return [line.strip() for line in lines]
        """,
    "pkg/needs.py": """
        import not_installed_xyz


        def doubled(xs):
            out = []
            for x in xs:
                out.append(x * 2)
            return out
        """,
    "tests/test_text.py": """
        from pkg.text import dedupe


        def test_dedupe():
            assert dedupe([1, 1, 2]) == [1, 2]
        """,
}


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


@pytest.fixture
def demo_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = write(tmp_path / "demo", TINY)
    monkeypatch.setattr(nz_paths, "DEMO_REPO", root)
    return root


async def run_to_end(m: RunManager) -> tuple[str, list[dict]]:
    s = await m.create(DEMO_AUTO)
    await asyncio.wait_for(m.wait(s.id), 180)
    lines = event_lines(m, s.id)
    assert_grammar(lines)
    return s.id, events(m, s.id)


def last(evs: list[dict], type_: str) -> dict:
    return [e for e in evs if e["type"] == type_][-1]["data"]


def git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_real_pipeline_without_cassettes(manager_factory, demo_repo: Path):
    """No cassettes for this repo: triage keeps its heuristics and every function
    fails at its first LLM call, but the run still completes with its artifacts."""
    m = manager_factory(flow.run_pipeline)
    run_id, evs = await run_to_end(m)

    states = [e["data"]["to_state"] for e in evs if e["type"] == "run.state_changed"]
    assert states == [
        "cloning",
        "installing",
        "discovering",
        "triaging",
        "awaiting_selection",
        "optimizing",
        "finalizing",
        "completed",
    ]
    assert last(evs, "run.power.detected")["power"]["badge"] in {
        "measured",
        "calibrated",
        "estimated",
    }

    clone = last(evs, "run.clone.completed")
    assert clone["ok"] and len(clone["commit_sha"]) == 40
    assert clone["n_py_files"] == 5 and clone["n_files"] == 6

    started = last(evs, "run.env.started")
    assert started == {"python_request": "3.12", "dependency_sources": []}
    env = last(evs, "run.env.completed")
    assert env["ok"] and env["python_version"].startswith("3.12.")
    assert "pytest" in {p["name"] for p in env["packages"]}
    probe = env["import_probe"]
    assert set(probe["ok_modules"]) == {"pkg.text", "pkg.files"}
    assert probe["failed"]["pkg.needs"].startswith("ModuleNotFoundError")

    disc = last(evs, "run.discovery.completed")
    assert disc["ok"] and disc["n_files"] == 4 and disc["n_test_files"] == 1
    assert disc["n_functions"] == 4 and disc["import_roots"] == ["."]
    skips = {it["function_id"]: it["skip_reason"] for it in disc["heuristic_ranked"]}
    assert skips["pkg.text:dedupe"] is None and skips["pkg.text:pairs"] is None
    assert skips["pkg.files:read_lines"].startswith("does I/O")
    assert skips["pkg.needs:doubled"].startswith("module does not import")

    tri = last(evs, "run.triage.started")
    assert tri == {"n_candidates": 2, "llm": True}
    tri = last(evs, "run.triage.completed")
    assert tri["ok"] and tri["llm_used"] is False
    assert set(tri["preselected"]) == {"pkg.text:dedupe", "pkg.text:pairs"}
    assert last(evs, "run.selection.confirmed") == {
        "function_ids": tri["preselected"],
        "auto": True,
    }

    writes = [e for e in evs if e["type"] == "function.tests.write.completed"]
    assert [e["function_id"] for e in writes] == tri["preselected"]
    assert all(not e["data"]["ok"] and "cassette" in e["data"]["error"]["message"] for e in writes)
    done = [e for e in evs if e["type"] == "function.completed"]
    assert [e["data"]["outcome"] for e in done] == ["failed", "failed"]

    arts = last(evs, "run.artifacts.completed")
    assert arts["ok"] and arts["patch"] is None and arts["function_diffs"] == []
    assert arts["zip"]["bytes"] > 0
    assert evs[-1]["type"] == "run.completed"

    p = m.store.paths(run_id)
    assert (p.trunk / "pkg/text.py").is_file()
    assert git_out(p.trunk, "rev-parse", "HEAD") == clone["commit_sha"]
    assert git_out(p.trunk, "branch", "--show-current") == TRUNK_BRANCH
    assert git_out(p.repo, "rev-parse", "HEAD") == clone["commit_sha"]
    assert (p.logs / "env.log").is_file()
    assert (p.harness / "_netzero_harness" / "probe_imports.py").is_file()


async def _rate(fns: Sequence[DiscoveredFunction]) -> dict[str, LlmRating]:
    assert {fn.function_id for fn in fns} == {"pkg.text:dedupe", "pkg.text:pairs"}
    return {
        "pkg.text:dedupe": LlmRating("high", "high", "list membership is O(n)"),
        "pkg.text:pairs": LlmRating("none", "low"),
    }


async def _boom(fns: Sequence[DiscoveredFunction]) -> dict[str, LlmRating]:
    raise RuntimeError("model unavailable")


def with_ranker(ranker):
    async def pipeline(ctx: RunContext) -> None:
        pre = await flow.prelude(ctx, ranker=ranker)
        await ctx.confirm_selection([it.function_id for it in pre.items if it.preselected])
        raise RuntimeError("stop after selection")

    return pipeline


async def test_llm_ratings_reorder_triage(manager_factory, demo_repo: Path):
    m = manager_factory(with_ranker(_rate))
    _, evs = await run_to_end(m)
    assert last(evs, "run.triage.started") == {"n_candidates": 2, "llm": True}
    tri = last(evs, "run.triage.completed")
    assert tri["llm_used"] is True
    first, second = tri["items"][:2]
    assert first["function_id"] == "pkg.text:dedupe" and first["llm_potential"] == "high"
    assert "list membership is O(n)" in first["reasons"]
    assert second["function_id"] == "pkg.text:pairs"
    assert second["score"] == round(0.5 * second["heuristic_score"], 4)
    assert (
        tri["preselected"]
        == ["pkg.text:dedupe", "pkg.text:pairs"][: 2 if second["score"] >= 0.15 else 1]
    )


async def test_ranker_failure_keeps_heuristics(manager_factory, demo_repo: Path):
    m = manager_factory(with_ranker(_boom))
    _, evs = await run_to_end(m)
    tri = last(evs, "run.triage.completed")
    assert tri["ok"] and tri["llm_used"] is False
    assert tri["items"] == last(evs, "run.discovery.completed")["heuristic_ranked"]
    warn = [e for e in evs if e["type"] == "log" and e["data"]["source"] == "llm"]
    assert any("model unavailable" in line for e in warn for line in e["data"]["lines"])


async def test_no_eligible_functions_fails_at_discovery(
    manager_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = write(
        tmp_path / "bare", {"pkg/__init__.py": "", "pkg/a.py": "def f(x):\n    return x\n"}
    )
    monkeypatch.setattr(nz_paths, "DEMO_REPO", root)
    m = manager_factory(flow.run_pipeline)
    _, evs = await run_to_end(m)
    disc = last(evs, "run.discovery.completed")
    assert disc["ok"] and disc["heuristic_ranked"][0]["skip_reason"].startswith("too short")
    failed = evs[-1]
    assert failed["type"] == "run.failed" and failed["data"]["stage"] == "discovering"
    assert "all skipped" in failed["data"]["error"]["message"]


async def test_copy_demo_commit_is_deterministic(demo_repo: Path, tmp_path: Path):
    (demo_repo / "pkg/__pycache__").mkdir()
    (demo_repo / "pkg/__pycache__/x.pyc").write_bytes(b"\0")
    a = await copy_demo(demo_repo, tmp_path / "a")
    b = await copy_demo(demo_repo, tmp_path / "b")
    assert a == b
    assert not (tmp_path / "a/pkg/__pycache__").exists()
