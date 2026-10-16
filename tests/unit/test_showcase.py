"""``netzero showcase``: finished runs bundled as the static files the showcase build reads."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from netzero import paths
from netzero.cli import app
from netzero.events import RunDetail, RunSummary
from netzero.pipeline.fake import expected_outcome
from netzero.pipeline.grammar import check_grammar
from netzero.pipeline.projection import project
from netzero.pipeline.store import RunPaths, read_complete_lines, to_summary
from netzero.showcase import SAMPLE_OWNER, SAMPLES, ShowcaseError, build, make_samples

SYNTHETIC = paths.WEB_PUBLIC_REPLAYS / "synthetic.events.jsonl"
RUN_ID = "20260101-000000-demo"
FN = "textkit.dedupe:dedupe_preserve_order"
BASE = f"/api/runs/{RUN_ID}/artifacts"


def meta(out: Path) -> dict:
    return json.loads((out / "showcase.json").read_text())


def run_folder(tmp_path: Path) -> Path:
    """The synthetic run laid out like ``runs/<id>/``, with its patch and one merge diff."""
    rp = RunPaths(tmp_path / "runs" / RUN_ID)
    rp.mkdirs()
    shutil.copyfile(SYNTHETIC, rp.events)
    (rp.out / f"{RUN_ID}.patch").write_text("the patch\n")
    (rp.out / f"{RUN_ID}.zip").write_bytes(b"PK")
    rp.fn(FN).mkdir(parents=True)
    (rp.fn(FN) / "merge.diff").write_text("the diff\n")
    return rp.root


def test_a_loose_log_bundles_without_artifacts(tmp_path: Path):
    out = tmp_path / "showcase"
    [b] = build([SYNTHETIC], out)
    assert (b.id, b.artifacts) == (RUN_ID, 0)
    [summary] = json.loads((out / "runs.json").read_text())
    assert RunSummary.model_validate(summary).state == "completed"
    detail = RunDetail.model_validate_json((out / "runs" / RUN_ID / "run.json").read_text())
    assert detail.last_seq == summary["last_seq"]
    assert (out / "runs" / RUN_ID / "events.jsonl").read_text() == SYNTHETIC.read_text()
    assert json.loads((out / "health.json").read_text())["ok"] is True
    assert meta(out) == {"samples": [], "artifacts": {}}


def test_a_run_folder_brings_its_patch_and_diffs_but_never_the_zip(tmp_path: Path):
    out = tmp_path / "showcase"
    [b] = build([run_folder(tmp_path)], out)
    manifest = meta(out)["artifacts"]
    assert manifest == {
        f"{BASE}/patch": f"/showcase/runs/{RUN_ID}/netzero-{RUN_ID}.patch",
        f"{BASE}/functions/textkit.dedupe%3Adedupe_preserve_order/diff": (
            f"/showcase/runs/{RUN_ID}/fn/textkit.dedupe__dedupe_preserve_order.diff"
        ),
    }
    assert b.artifacts == 2
    for static in manifest.values():
        assert (out / static.removeprefix("/showcase/")).is_file()
    assert not list(out.rglob("*.zip"))


def test_unfinished_runs_are_refused_before_anything_is_written(tmp_path: Path):
    partial = tmp_path / "partial.events.jsonl"
    partial.write_text("".join(SYNTHETIC.read_text().splitlines(keepends=True)[:-2]))
    out = tmp_path / "showcase"
    build([SYNTHETIC], out)
    with pytest.raises(ShowcaseError, match="terminal"):
        build([partial], out)
    assert (out / "runs" / RUN_ID / "events.jsonl").is_file()


def test_the_same_run_twice_is_refused(tmp_path: Path):
    with pytest.raises(ShowcaseError, match="twice"):
        build([SYNTHETIC, run_folder(tmp_path)], tmp_path / "showcase")


def test_a_folder_that_is_not_a_showcase_is_left_alone(tmp_path: Path):
    out = tmp_path / "mine"
    out.mkdir()
    (out / "notes.txt").write_text("keep me")
    with pytest.raises(ShowcaseError, match="not a showcase"):
        build([SYNTHETIC], out)
    assert (out / "notes.txt").read_text() == "keep me"


def test_the_cli_replaces_the_previous_bundle(tmp_path: Path):
    out = tmp_path / "showcase"
    (out / "runs" / "20250101-000000-old").mkdir(parents=True)
    (out / "runs.json").write_text("[]")
    result = CliRunner().invoke(app, ["showcase", str(SYNTHETIC), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (out / "runs").iterdir()) == [RUN_ID]


def test_samples_are_finished_runs_on_made_up_repositories(tmp_path: Path):
    logs = make_samples(tmp_path / "runs")
    out = tmp_path / "showcase"
    bundled = build([SYNTHETIC], out, samples=logs)
    assert [b.sample for b in bundled] == [False] + [True] * len(SAMPLES)
    summaries = json.loads((out / "runs.json").read_text())
    by_id = {s["id"]: s for s in summaries}
    assert meta(out)["samples"] == [s["id"] for s in summaries if s["id"] != RUN_ID]
    for sample, log in zip(SAMPLES, logs, strict=True):
        summary = by_id[log.parent.name]
        assert summary["source"]["url"] == f"https://github.com/{SAMPLE_OWNER}/{sample.repo}"
        assert summary["state"] == ("failed" if sample.scenario == "fail_env" else "completed")
        if sample.functions:
            detail = RunDetail.model_validate_json(
                (out / "runs" / log.parent.name / "run.json").read_text()
            )
            assert sorted(detail.selection or []) == sorted(sample.functions)


def test_no_improved_function_repeats_across_samples():
    """The fake scores a function the same in every run, so a repeat shows twice on the dashboard."""
    improved = [f for s in SAMPLES for f in s.functions if expected_outcome(f) == "accepted"]
    assert len(improved) == len(set(improved))


def test_the_committed_showcase_matches_its_logs():
    """A fold change leaves the bundled run.json stale: rebundle with ``netzero showcase``."""
    root = paths.WEB_PUBLIC_SHOWCASE
    summaries = json.loads((root / "runs.json").read_text())
    assert sorted(p.name for p in (root / "runs").iterdir()) == sorted(s["id"] for s in summaries)
    for summary in summaries:
        run = root / "runs" / summary["id"]
        lines, _ = read_complete_lines(run / "events.jsonl")
        assert check_grammar(lines) == []
        detail = project(lines, summary["id"])
        stored = RunDetail.model_validate_json((run / "run.json").read_text())
        assert stored.model_dump(mode="json") == detail.model_dump(mode="json")
        assert to_summary(detail).model_dump(mode="json") == summary
    shown = meta(root)
    assert set(shown["samples"]) <= {s["id"] for s in summaries}
    for static in shown["artifacts"].values():
        assert (root / static.removeprefix("/showcase/")).is_file()
