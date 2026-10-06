"""Cassette keys, paths and files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from netzero import paths
from netzero.errors import LlmError
from netzero.llm.cassette import CassetteEntry, CassetteKey, CassetteStore, fslug, request_hash


@pytest.mark.parametrize(
    ("fid", "slug"),
    [
        ("pkg.mod:Cls.method", "pkg.mod__Cls.method"),
        ("pkg.mod:f", "pkg.mod__f"),
        ("a b/c\\d:é", "a_b_c_d___"),
        ("my-pkg.v2_mod:g", "my-pkg.v2_mod__g"),
        (None, "_"),
        ("..", "_"),
        (".", "_"),
    ],
)
def test_fslug(fid: str | None, slug: str) -> None:
    assert fslug(fid) == slug


def test_paths(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    assert store.path(CassetteKey("triage")) == tmp_path / "triage" / "_" / "_" / "0.json"
    assert store.path(CassetteKey("tests", "pkg.mod:f")) == tmp_path / "tests/pkg.mod__f/_/0.json"
    key = CassetteKey("tests_repair", "pkg.mod:Cls.m", None, 2)
    assert store.path(key) == tmp_path / "tests_repair/pkg.mod__Cls.m/_/2.json"
    key = CassetteKey("rewrite_repair", "pkg.mod:f", "B", 1)
    assert store.path(key) == tmp_path / "rewrite_repair/pkg.mod__f/B/1.json"


def test_default_root_is_the_demo_cassettes() -> None:
    assert CassetteStore().root == paths.CASSETTES_DIR


def test_keys_are_hashable_and_compare_by_value() -> None:
    assert CassetteKey("rewrite", "m:f", "A") == CassetteKey("rewrite", "m:f", "A", 0)
    assert len({CassetteKey("triage"), CassetteKey("triage")}) == 1


def test_save_then_load(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    key = CassetteKey("rewrite", "pkg.mod:f", "A", 0)
    entry = CassetteEntry(
        output={"code": "def f():\n    return 'ü'\n", "new_imports": []},
        model="claude-haiku-4-5",
        request_hash="abc",
        usage={
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        stop_reason="end_turn",
    )
    path = store.save(key, entry)
    assert path == store.path(key)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["key"] == {
        "stage": "rewrite",
        "function_id": "pkg.mod:f",
        "candidate": "A",
        "attempt": 0,
    }
    assert "ü" in path.read_text(encoding="utf-8")  # readable, not \u-escaped
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert store.load(key) == entry
    assert not list(tmp_path.rglob("*.tmp"))


def test_only_output_is_required(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    key = CassetteKey("triage")
    store.path(key).parent.mkdir(parents=True)
    store.path(key).write_text('{"output": {"ratings": []}}')
    entry = store.load(key)
    assert entry == CassetteEntry(output={"ratings": []})


def test_miss_names_the_expected_path(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    with pytest.raises(LlmError) as exc:
        store.load(CassetteKey("tests", "pkg.mod:f"))
    assert exc.value.kind == "cassette_miss"
    assert "tests/pkg.mod__f/_/0.json" in exc.value.message


def test_miss_path_is_repo_relative_under_the_repo() -> None:
    store = CassetteStore(paths.ROOT / "does-not-exist-cassettes")
    with pytest.raises(LlmError) as exc:
        store.load(CassetteKey("triage"))
    assert "does-not-exist-cassettes/triage/_/_/0.json" in exc.value.message
    assert str(paths.ROOT) not in exc.value.message


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '{"usage": {}}', '{"output": "x"}'])
def test_malformed_cassettes_are_llm_errors(tmp_path: Path, text: str) -> None:
    store = CassetteStore(tmp_path)
    key = CassetteKey("triage")
    store.path(key).parent.mkdir(parents=True)
    store.path(key).write_text(text)
    with pytest.raises(LlmError) as exc:
        store.load(key)
    assert exc.value.kind == "llm_error"


@pytest.mark.parametrize(
    "fields",
    [
        {"usage": {"input_tokens": "12"}},
        {"usage": {"output_tokens": -1}},
        {"usage": {"input_tokens": 1.5}},
        {"usage": {"cache_read_input_tokens": True}},
        {"stop_reason": 1},
        {"request_hash": ["abc"]},
    ],
)
def test_hand_written_fields_of_the_wrong_type_are_llm_errors(tmp_path: Path, fields: dict) -> None:
    """Not a ValueError or ValidationError later, which would fail the whole function."""
    store = CassetteStore(tmp_path)
    key = CassetteKey("triage")
    store.path(key).parent.mkdir(parents=True)
    store.path(key).write_text(json.dumps({"output": {"ratings": []}, **fields}))
    with pytest.raises(LlmError) as exc:
        store.load(key)
    assert exc.value.kind == "llm_error"
    assert "triage/_/_/0.json" in exc.value.message


def test_usage_may_carry_other_fields_and_omit_counts(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    key = CassetteKey("triage")
    store.path(key).parent.mkdir(parents=True)
    usage = {"input_tokens": 5, "output_tokens": None, "service_tier": "standard"}
    store.path(key).write_text(json.dumps({"output": {}, "usage": usage, "stop_reason": None}))
    assert store.load(key).usage == usage


def test_a_failed_save_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    store = CassetteStore(tmp_path)
    with pytest.raises(OSError):
        store.save(CassetteKey("triage"), CassetteEntry(output={}))
    assert not list(tmp_path.rglob("*.tmp"))
    assert not store.path(CassetteKey("triage")).exists()


def test_request_hash_is_canonical_and_sensitive() -> None:
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    reordered = [{"content": [{"text": "hi", "type": "text"}], "role": "user"}]
    h = request_hash("m", "sys", msgs, {"type": "object"})
    assert h == request_hash("m", "sys", reordered, {"type": "object"})
    assert len(h) == 64
    assert h != request_hash("m2", "sys", msgs, {"type": "object"})
    assert h != request_hash("m", "sys!", msgs, {"type": "object"})
    assert h != request_hash("m", "sys", [], {"type": "object"})
    assert h != request_hash("m", "sys", msgs, {"type": "array"})
