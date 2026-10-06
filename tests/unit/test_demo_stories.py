"""The demo cassettes are built from ``examples/demo-stories`` and stay in sync with it."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from netzero import paths
from netzero.cli import REPLAY_SELECTION, _add_replay
from netzero.llm import stories
from netzero.llm.cassette import CassetteKey, CassetteStore
from netzero.pipeline import discovery


def _files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text("utf-8") for p in root.rglob("*.json")}


def test_every_story_becomes_cassettes(tmp_path: Path):
    written = stories.build(root=tmp_path)
    stages = Counter(p.relative_to(tmp_path).parts[0] for p in written)
    n = len([p for p in stories.STORIES_DIR.iterdir() if (p / "story.json").is_file()])
    assert stages["triage"] == 1
    assert stages["tests"] == n
    assert stages["rewrite"] == 3 * n
    entry = CassetteStore(tmp_path).load(CassetteKey("tests", "algos.primes:primes_below"))
    assert entry.model == stories.AUTHOR
    assert entry.usage is None  # nothing was billed: no model wrote these


def test_committed_cassettes_match_the_stories(tmp_path: Path):
    stories.build(root=tmp_path)
    assert _files(paths.CASSETTES_DIR) == _files(tmp_path), (
        "examples/demo-repo/.netzero-cassettes is stale: run `uv run netzero demo-cassettes`"
    )


def test_stories_cover_every_eligible_demo_function():
    fids = {fn.function_id for fn in discovery.discover(paths.DEMO_REPO).functions}
    told = {
        json.loads((p / "story.json").read_text("utf-8"))["function_id"]
        for p in stories.STORIES_DIR.iterdir()
        if (p / "story.json").is_file()
    }
    assert told <= fids
    assert fids - told == {"algos.walk:random_walk", "datakit.rates:fetch_exchange_rates"}
    assert set(REPLAY_SELECTION) <= told


def test_a_repair_without_its_file_is_an_error(tmp_path: Path):
    src = stories.STORIES_DIR / "algos.primes__primes_below"
    story = tmp_path / src.name
    shutil.copytree(src, story)
    (story / "C_repair.py").unlink()
    with pytest.raises(stories.StoryError, match="repairs.C and C_repair.py"):
        stories.story_cassettes(story)


def test_a_reply_that_breaks_the_schema_is_an_error(tmp_path: Path):
    src = stories.STORIES_DIR / "algos.primes__primes_below"
    story = tmp_path / src.name
    shutil.copytree(src, story)
    meta = json.loads((story / "story.json").read_text("utf-8"))
    meta["A"]["new_imports"] = "import heapq"  # a string, not a list
    (story / "story.json").write_text(json.dumps(meta), "utf-8")
    with pytest.raises(stories.StoryError, match="does not fit RewriteOut"):
        stories.story_cassettes(story)


def test_add_replay_puts_the_entry_first_and_replaces_its_old_self(tmp_path: Path):
    out = tmp_path / "demo.events.jsonl"
    other = {"id": "synthetic", "title": "s", "src": "/replays/synthetic.events.jsonl"}
    (tmp_path / "index.json").write_text(json.dumps([{"id": "demo", "title": "old"}, other]))
    _add_replay(out, {"id": "demo", "title": "new", "src": "/replays/demo.events.jsonl"})
    index = json.loads((tmp_path / "index.json").read_text())
    assert [r["id"] for r in index] == ["demo", "synthetic"]
    assert index[0]["title"] == "new"
    assert index[1] == other
