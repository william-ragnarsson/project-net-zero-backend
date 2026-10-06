"""The real pipeline end to end on a tiny repo, its LLM answers replayed from
hand-written cassettes: tests, capture, A/B/C, bench, decision, merge, artifacts."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

from netzero import paths as nz_paths
from netzero.llm.cassette import CassetteEntry, CassetteKey, CassetteStore
from netzero.pipeline import flow
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.orchestrator import RunManager
from tests.integration.helpers import DEMO_AUTO, event_lines, events

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv"),
]

DEDUPE = "pkg.text:dedupe"
PAIRS = "pkg.text:pairs"

TEXT = """
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
"""

DEDUPE_TESTS = """
import pytest

from pkg.text import dedupe


def test_keeps_first_occurrence_order():
    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_empty():
    assert dedupe([]) == []


def test_strings():
    assert dedupe(["b", "a", "b"]) == ["b", "a"]


@pytest.mark.nz_workload
def test_workload():
    items = [i % 400 for i in range(2000)]
    assert dedupe(items) == list(range(400))
"""

# expects the wrong answer: the tests can never pass on the original
PAIRS_TESTS = """
import pytest

from pkg.text import pairs


def test_none():
    assert pairs([1, 2], 10) == []


def test_one_pair():
    assert pairs([1, 2], 3) == [(0, 1)]


def test_empty():
    assert pairs([], 3) == []


@pytest.mark.nz_workload
def test_workload():
    assert len(pairs(list(range(60)), 50)) == 50
"""

SET_BASED = """
def dedupe(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
"""

SORTED = """
def dedupe(items):
    return sorted(set(items))
"""

# different AST, same algorithm: passes every check, saves nothing
SAME_SPEED = """
def dedupe(items):
    out = []
    for x in items:
        if not x in out:
            out.append(x)
    return out
"""

ORIGINAL = textwrap.dedent(TEXT).split("\n\n\n")[0].strip() + "\n"


def rewrite(code: str, strategy: str, **extra) -> dict:
    return {
        "strategy": strategy,
        "rationale": "hand-written for the test",
        "code": textwrap.dedent(code).strip() + "\n",
        "new_imports": [],
        **extra,
    }


CASSETTES: dict[CassetteKey, dict] = {
    CassetteKey("triage"): {
        "ratings": [
            {"function_id": DEDUPE, "potential": "high", "testability": "high", "reason": "x"},
            {"function_id": PAIRS, "potential": "medium", "testability": "high", "reason": "y"},
        ]
    },
    CassetteKey("tests", DEDUPE): {"code": DEDUPE_TESTS, "notes": "order, empty, strings"},
    CassetteKey("tests", PAIRS): {"code": PAIRS_TESTS, "notes": "pairs"},
    CassetteKey("tests_repair", PAIRS, None, 1): {
        "diagnosis": "still wrong",
        "code": PAIRS_TESTS,
        "notes": "pairs",
    },
    CassetteKey("rewrite", DEDUPE, "A", 0): rewrite(SET_BASED, "set for membership"),
    CassetteKey("rewrite", DEDUPE, "B", 0): rewrite(SORTED, "set then sort"),
    CassetteKey("rewrite_repair", DEDUPE, "B", 1): rewrite(
        SAME_SPEED, "keep the list scan", diagnosis="sorting broke the order"
    ),
    CassetteKey("rewrite", DEDUPE, "C", 0): rewrite(ORIGINAL, "unchanged"),
    CassetteKey("rewrite_repair", DEDUPE, "C", 1): rewrite(
        ORIGINAL, "unchanged", diagnosis="nothing to change"
    ),
}


@pytest.fixture
def demo_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "demo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/__init__.py").write_text("")
    (root / "pkg/text.py").write_text(textwrap.dedent(TEXT).lstrip("\n"))
    store = CassetteStore(root / ".netzero-cassettes")
    usage = {"input_tokens": 1200, "output_tokens": 300}
    for key, output in CASSETTES.items():
        store.save(key, CassetteEntry(output=output, model="claude-haiku-4-5", usage=usage))
    monkeypatch.setattr(nz_paths, "DEMO_REPO", root)
    return root


def of(evs: list[dict], type_: str, fid: str | None = None, cid: str | None = None) -> list[dict]:
    return [
        e
        for e in evs
        if e["type"] == type_
        and (fid is None or e["function_id"] == fid)
        and (cid is None or e["candidate_id"] == cid)
    ]


def git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_optimizes_merges_and_packages(manager_factory, demo_repo: Path):
    m: RunManager = manager_factory(
        flow.run_pipeline, n_trials=4, trial_target_s=0.05, test_repairs=1
    )
    s = await m.create(DEMO_AUTO)
    await asyncio.wait_for(m.wait(s.id), 300)
    assert_grammar(event_lines(m, s.id))
    evs = events(m, s.id)
    assert evs[-1]["type"] == "run.completed", evs[-1]

    tri = of(evs, "run.triage.completed")[0]["data"]
    assert tri["llm_used"] is True
    assert tri["preselected"] == [DEDUPE, PAIRS]

    # pairs: its tests fail on the original, and so does the one repair
    runs = of(evs, "function.tests.run.completed", PAIRS)
    assert [e["attempt"] for e in runs] == [0, 1] and not any(e["data"]["ok"] for e in runs)
    repair = of(evs, "function.tests.write.started", PAIRS)[1]
    assert repair["data"]["kind"] == "repair" and repair["data"]["failures_in"]
    done = {e["function_id"]: e["data"] for e in of(evs, "function.completed")}
    assert done[PAIRS]["outcome"] == "skipped_untestable"
    assert done[PAIRS]["reason"].endswith("after 1 repair")

    # dedupe: the tests pass twice on the original, then the capture
    assert [e["data"]["ok"] for e in of(evs, "function.tests.run.completed", DEDUPE)] == [
        True,
        True,
    ]
    cap = of(evs, "function.capture.completed", DEDUPE)[0]["data"]
    assert cap["ok"] and cap["n_kept"] >= 4 and cap["deterministic"]
    base = of(evs, "function.baseline.completed", DEDUPE)[0]["data"]
    assert base["ok"] and base["original"]["n_trials"] == 4

    # B fails the tests, is repaired into a same-speed rewrite; C never changes
    b_checks = of(evs, "candidate.check.completed", DEDUPE, "B")
    assert [(e["attempt"], e["data"]["ok"]) for e in b_checks] == [(0, False), (1, True)]
    rejected = {e["candidate_id"]: e["data"] for e in of(evs, "candidate.rejected", DEDUPE)}
    assert set(rejected) == {"C"} and rejected["C"]["reason"] == "identical"
    benched = [e["candidate_id"] for e in of(evs, "candidate.bench.completed", DEDUPE)]
    assert sorted(benched) == ["A", "B"]

    decision = of(evs, "function.decision", DEDUPE)[0]["data"]
    assert decision["outcome"] == "winner" and decision["winner"] == "A"
    ranking = {r["candidate_id"]: r for r in decision["ranking"]}
    assert ranking["A"]["significant"] and ranking["A"]["delta_pct"] < -50
    assert not ranking["B"]["significant"]
    assert ranking["C"]["status"] == "rejected"

    merge = of(evs, "function.merge.completed", DEDUPE)[0]["data"]
    assert merge["ok"] and not merge["reverted"]
    assert merge["suite"]["exit_code"] == 0 and merge["differential"]["ok"]
    assert done[DEDUPE]["outcome"] == "accepted" and done[DEDUPE]["winner"] == "A"
    assert "seen = set()" in done[DEDUPE]["diff"]

    usage = of(evs, "llm.usage")
    assert len(usage) == len(CASSETTES)
    assert usage[-1]["data"]["run_cost_usd"] == pytest.approx(
        sum(e["data"]["cost_usd"] for e in usage)
    )

    p = m.store.paths(s.id)
    assert git_out(p.trunk, "log", "-1", "--format=%s").startswith("netzero: optimize dedupe")
    assert "seen = set()" in (p.trunk / "pkg/text.py").read_text()

    arts = of(evs, "run.artifacts.completed")[0]["data"]
    assert arts["patch"]["files_changed"] == 1
    assert [d["function_id"] for d in arts["function_diffs"]] == [DEDUPE]
    patch = p.out / f"{s.id}.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=p.repo, check=True)

    with zipfile.ZipFile(p.out / f"{s.id}.zip") as zf:
        names = set(zf.namelist())
        assert "demo-repo/pkg/text.py" in names
        assert {"netzero.patch", "netzero-report.json"} <= names
        assert "netzero-tests/test_nz_pkg_text__dedupe.py" in names
        assert "netzero-tests/test_nz_pkg_text__pairs.py" not in names
        assert "seen = set()" in zf.read("demo-repo/pkg/text.py").decode()
        report = io.TextIOWrapper(zf.open("netzero-report.json")).read()
        assert '"outcome": "accepted"' in report
