"""Build the demo cassettes from the hand-written stories in ``examples/demo-stories``.

The demo replays these files instead of calling a model. They were written by
people, not recorded from Claude, so every cassette says so in ``model`` and
carries no token usage (a demo run costs $0). The mapping is documented in
``examples/demo-stories/README.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from netzero import paths
from netzero.llm.cassette import CassetteEntry, CassetteKey, CassetteStore
from netzero.llm.models import RewriteOut, RewriteRepairOut, TestFileOut, TestRepairOut, TriageOut

STORIES_DIR = paths.ROOT / "examples" / "demo-stories"
AUTHOR = "hand-written"  # the cassette's ``model``: no model produced these replies
CANDIDATES = ("A", "B", "C")
REWRITE_FIELDS = ("strategy", "rationale", "new_imports")


class StoryError(ValueError):
    pass


def _read(path: Path) -> str:
    if not path.is_file():
        raise StoryError(f"missing {path.relative_to(paths.ROOT).as_posix()}")
    return path.read_text(encoding="utf-8")


def _out(model: type[BaseModel], where: str, **fields: Any) -> dict[str, Any]:
    """The output dict, checked against the schema the pipeline will parse it with."""
    try:
        return model.model_validate(fields).model_dump(mode="json")
    except ValueError as e:
        raise StoryError(f"{where}: does not fit {model.__name__}: {e}") from None


def story_cassettes(story_dir: Path) -> list[tuple[CassetteKey, dict[str, Any]]]:
    """Every (key, output) one story produces."""
    story = json.loads(_read(story_dir / "story.json"))
    fid, name = story["function_id"], story_dir.name
    v1 = story_dir / "tests_v1.py"
    out: list[tuple[CassetteKey, dict[str, Any]]] = []

    first = _read(v1) if v1.is_file() else _read(story_dir / "tests.py")
    out.append(
        (
            CassetteKey("tests", fid),
            _out(TestFileOut, f"{name}/tests", code=first, notes=story["tests_notes"]),
        )
    )
    if v1.is_file():
        rep = story.get("tests_repair") or {}
        if not rep:
            raise StoryError(f"{name}: tests_v1.py exists but tests_repair is empty")
        out.append(
            (
                CassetteKey("tests_repair", fid, None, 1),
                _out(
                    TestRepairOut,
                    f"{name}/tests_repair",
                    diagnosis=rep["diagnosis"],
                    code=_read(story_dir / "tests.py"),
                    notes=rep["notes"],
                ),
            )
        )
    elif story.get("tests_repair"):
        raise StoryError(f"{name}: tests_repair is set but tests_v1.py is missing")

    repairs = story.get("repairs") or {}
    for cid in CANDIDATES:
        meta = story[cid]
        fields = {k: meta[k] for k in REWRITE_FIELDS}
        out.append(
            (
                CassetteKey("rewrite", fid, cid, 0),
                _out(RewriteOut, f"{name}/{cid}", code=_read(story_dir / f"{cid}.py"), **fields),
            )
        )
        repair_file = story_dir / f"{cid}_repair.py"
        if (cid in repairs) != repair_file.is_file():
            raise StoryError(f"{name}: repairs.{cid} and {cid}_repair.py must come together")
        if cid in repairs:
            rep = repairs[cid]
            out.append(
                (
                    CassetteKey("rewrite_repair", fid, cid, 1),
                    _out(
                        RewriteRepairOut,
                        f"{name}/{cid}_repair",
                        diagnosis=rep["diagnosis"],
                        code=_read(repair_file),
                        **{k: rep[k] for k in REWRITE_FIELDS},
                    ),
                )
            )
    return out


def all_cassettes(stories: Path = STORIES_DIR) -> list[tuple[CassetteKey, dict[str, Any]]]:
    triage = json.loads(_read(stories / "triage.json"))
    out = [(CassetteKey("triage"), _out(TriageOut, "triage.json", **triage))]
    for story_dir in sorted(p for p in stories.iterdir() if (p / "story.json").is_file()):
        out.extend(story_cassettes(story_dir))
    return out


def build(stories: Path = STORIES_DIR, root: Path | None = None) -> list[Path]:
    """Rewrite the cassette folder from the stories; returns the files written."""
    store = CassetteStore(root)
    cassettes = all_cassettes(stories)  # validate everything before touching the folder
    if store.root.exists():
        shutil.rmtree(store.root)
    return [
        store.save(key, CassetteEntry(output=output, model=AUTHOR, stop_reason="end_turn"))
        for key, output in cassettes
    ]
