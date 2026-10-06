"""The ``runs/`` folder: ids, slugs, locks, atomic writes, tail repair and line reads."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from netzero.events import LogData, RunDetail, RunSource
from netzero.pipeline.bus import EventBus
from netzero.pipeline.store import (
    FileLock,
    RunPaths,
    RunStore,
    fn_slug,
    last_complete_line,
    read_complete_lines,
    repair_tail,
    slugify,
    write_atomic,
)

# -- slugs ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("function_id", "expected"),
    [
        ("pkg.mod:Cls.method", "pkg.mod__Cls.method"),
        ("pkg.mod:f", "pkg.mod__f"),
        ("m:outer.<locals>.inner", "m__outer._locals_.inner-"),
        ("..", "_-"),
        (".", "_-"),
        ("...", "_-"),
        (".hidden", "hidden-"),
        ("../etc/passwd", "_etc_passwd-"),
        ("", "_-"),
        ("pkg.mod:fñ", "pkg.mod__f_-"),
        ("pkg.mod:__call", "pkg.mod____call-"),
    ],
)
def test_fn_slug_cases(function_id: str, expected: str) -> None:
    """Plain ids map readably; lossy ones (expected ends in ``-``) get a hash suffix."""
    if expected.endswith("-"):
        assert re.fullmatch(re.escape(expected) + r"[0-9a-f]{12}", fn_slug(function_id))
    else:
        assert fn_slug(function_id) == expected


NASTY_IDS = [
    "..",
    ".",
    "/",
    "//",
    "../../x",
    "..\\..\\x",
    "a/../../b",
    ".git",
    "./.",
    "\x00",
    "con:nul",
    " ",
    "." * 200,
    "." * 119 + "x",
    "a" * 500,
    "pkg." * 60 + "mod:f",
]


@pytest.mark.parametrize("function_id", NASTY_IDS)
def test_fn_slug_is_always_a_safe_name(function_id: str) -> None:
    slug = fn_slug(function_id)
    assert slug not in {"", ".", ".."}
    assert not slug.startswith(".")
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", slug)
    assert len(slug) <= 120


def test_fn_slug_paths_stay_inside_the_run(tmp_path: Path) -> None:
    rp = RunPaths(tmp_path / "run")
    for fid in NASTY_IDS:
        assert rp.fn(fid).resolve().parent == (rp.root / "fn").resolve()
        assert rp.candidate(fid, "A").resolve().parent.parent == (rp.work / "cand").resolve()


def test_fn_slug_distinct_long_ids_get_distinct_slugs() -> None:
    base = "company.services.billing.reconciliation.adapters.legacy_mainframe:"
    base += "LegacyMainframeReconciliationAdapter.reconcile_monthly_statements"
    assert len(base) > 120
    assert fn_slug(base) != fn_slug(base + "_v2")
    assert len(fn_slug(base)) <= 120


def test_fn_slug_colon_mapping_cannot_collide() -> None:
    assert fn_slug("a.b:f__g") != fn_slug("a.b__f:g")
    assert fn_slug("a:b") != fn_slug("a__b")
    assert fn_slug("m:f") != fn_slug("m:f.")  # unsafe-char and dot variants differ too


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My Repo!", "my-repo"),
        ("https://github.com/Org/Some_Repo.git", "https-github-com-org-some-repo-git"),
        ("---", "run"),
        ("", "run"),
        ("åäö", "run"),
        ("a" * 50, "a" * 40),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_slugify_never_ends_with_dash_after_truncation() -> None:
    assert slugify("a" * 39 + " b") == "a" * 39


# -- run ids ------------------------------------------------------------------------------


def test_new_run_id_format(store: RunStore) -> None:
    now = datetime(2026, 10, 5, 12, 34, 56).timestamp()
    assert store.new_run_id("My Repo", now=now) == "20261005-123456-my-repo"
    run_id = store.new_run_id("https://github.com/x/y")
    assert re.fullmatch(r"\d{8}-\d{6}-[a-z0-9][a-z0-9-]*", run_id)
    assert store.valid_id(run_id)


def test_new_run_id_avoids_existing_folders(store: RunStore) -> None:
    now = datetime(2026, 10, 5, 12, 0, 0).timestamp()
    first = store.new_run_id("demo", now=now)
    (store.runs_dir / first).mkdir()
    second = store.new_run_id("demo", now=now)
    (store.runs_dir / second).mkdir()
    third = store.new_run_id("demo", now=now)
    assert (first, second, third) == (
        "20261005-120000-demo",
        "20261005-120000-demo-2",
        "20261005-120000-demo-3",
    )
    assert all(store.valid_id(r) for r in (first, second, third))


def test_new_run_id_long_label_is_still_valid(store: RunStore) -> None:
    run_id = store.new_run_id("x" * 200)
    assert store.valid_id(run_id)
    (store.runs_dir / run_id).mkdir()
    assert store.valid_id(store.new_run_id("x" * 200))


@pytest.mark.parametrize(
    "run_id",
    ["20261005-123456-a", "20261005-123456-my-repo-2", "20261005-123456-" + "a" * 48],
)
def test_valid_id_accepts(store: RunStore, run_id: str) -> None:
    assert store.valid_id(run_id)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        "..",
        "../20261005-123456-a",
        "20261005-123456-a/../../etc",
        "20261005-123456-../x",
        "20261005-123456-A",
        "20261005-123456-",
        "20261005-123456--a",
        "20261005-123456-a_b",
        "20261005-123456-a b",
        "20261005-123456-a.b",
        "2026105-123456-a",
        "20261005123456-a",
        "20261005-123456-" + "a" * 49,
        " 20261005-123456-a",
    ],
)
def test_valid_id_rejects(store: RunStore, run_id: str) -> None:
    assert not store.valid_id(run_id)
    with pytest.raises(KeyError):
        store.paths(run_id)


def test_valid_id_rejects_trailing_newline(store: RunStore) -> None:
    assert not store.valid_id("20261005-123456-a\n")


# -- tail repair + line reads -----------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", None),
        (b"partial", None),
        (b"a\n", "a"),
        (b"a\nb\n", "b"),
        (b"a\nb", "a"),  # a torn trailing write is not a line
        (b"a\n\n", ""),
        (b"x" * 20_000 + b"\n", "x" * 20_000),  # longer than one read chunk
        (b"head\n" + b"y" * 9000 + b"\npart", "y" * 9000),
    ],
)
def test_last_complete_line(tmp_path: Path, content: bytes, expected: str | None) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(content)
    assert last_complete_line(p) == expected
    assert last_complete_line(p, chunk=3) == expected  # chunk boundaries anywhere


def test_last_complete_line_missing_file(tmp_path: Path) -> None:
    assert last_complete_line(tmp_path / "nope.jsonl") is None


def test_repair_tail_truncates_partial_line(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(b'{"seq":1}\n{"seq":2}\n{"seq":3,"ty')
    assert repair_tail(p) == len(b'{"seq":3,"ty')
    assert p.read_bytes() == b'{"seq":1}\n{"seq":2}\n'


def test_repair_tail_leaves_clean_file_alone(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(b"a\nb\n")
    assert repair_tail(p) == 0
    assert p.read_bytes() == b"a\nb\n"


def test_repair_tail_partial_longer_than_a_chunk(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(b"a\n" + b"x" * 10_000)
    assert repair_tail(p) == 10_000
    assert p.read_bytes() == b"a\n"


def test_repair_tail_single_partial_line(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(b"x" * 5000)
    assert repair_tail(p) == 5000
    assert p.read_bytes() == b""


def test_repair_tail_missing_and_empty(tmp_path: Path) -> None:
    assert repair_tail(tmp_path / "missing") == 0
    (tmp_path / "empty").write_bytes(b"")
    assert repair_tail(tmp_path / "empty") == 0


def test_read_complete_lines_offsets(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_bytes(b"one\ntwo\n")
    lines, off = read_complete_lines(p)
    assert (lines, off) == (["one", "two"], 8)
    with p.open("ab") as f:
        f.write(b"three\nfour-partial")
    lines, off2 = read_complete_lines(p, off)
    assert (lines, off2) == (["three"], 14)
    assert read_complete_lines(p, off2) == ([], off2)  # only a partial line left
    with p.open("ab") as f:
        f.write(b"\n")
    assert read_complete_lines(p, off2) == (["four-partial"], p.stat().st_size)


def test_read_complete_lines_edge_cases(tmp_path: Path) -> None:
    assert read_complete_lines(tmp_path / "missing", 5) == ([], 5)
    p = tmp_path / "e.jsonl"
    p.write_bytes("a\n\nbé\n".encode())
    lines, off = read_complete_lines(p)
    assert lines == ["a", "bé"]  # blank lines skipped, utf-8 decoded
    assert off == p.stat().st_size
    assert read_complete_lines(p, off + 100) == ([], off + 100)


# -- locks + atomic writes ---------------------------------------------------------------


def test_file_lock_acquire_release(tmp_path: Path) -> None:
    path = tmp_path / "sub" / ".lock"
    assert not FileLock.is_locked(path)  # does not exist yet
    a = FileLock(path)
    assert a.try_acquire()
    assert a.held
    assert FileLock.is_locked(path)  # held by this very process
    b = FileLock(path)
    assert not b.try_acquire()
    assert not b.held
    a.release()
    assert not a.held
    assert not FileLock.is_locked(path)
    assert b.try_acquire()
    assert FileLock.is_locked(path)
    b.release()
    b.release()  # idempotent
    assert not FileLock.is_locked(path)


def test_is_locked_does_not_take_the_lock(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    path.touch()
    assert not FileLock.is_locked(path)
    lock = FileLock(path)
    assert lock.try_acquire()
    lock.release()


def test_write_atomic(tmp_path: Path) -> None:
    p = tmp_path / "run.json"
    write_atomic(p, "first")
    write_atomic(p, "second é")
    assert p.read_text("utf-8") == "second é"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["run.json"]


# -- RunStore --------------------------------------------------------------------------------


def detail(run_id: str, *, created_ts: int = 1000, state: str = "created") -> RunDetail:
    return RunDetail(
        id=run_id,
        created_ts=created_ts,
        updated_ts=created_ts,
        state=state,  # type: ignore[arg-type]
        source=RunSource(kind="github", url="https://github.com/o/r"),
        mode="live",
        counts_by_outcome={"accepted": 2},
        last_seq=7,
    )


def make_run(store: RunStore, run_id: str, **kwargs) -> RunDetail:
    store.paths(run_id).mkdirs()
    d = detail(run_id, **kwargs)
    store.write_detail(d)
    return d


def test_write_then_read_detail(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    assert store.read_detail(run_id) is None
    assert not store.exists(run_id)
    d = make_run(store, run_id)
    assert store.exists(run_id)
    assert store.read_detail(run_id) == d
    assert not any(p.name.endswith(".tmp") for p in store.paths(run_id).root.iterdir())


def test_read_detail_tolerates_garbage(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    make_run(store, run_id)
    store.paths(run_id).run_json.write_text("{not json")
    assert store.read_detail(run_id) is None
    store.paths(run_id).run_json.write_text('{"id": "x"}')
    assert store.read_detail(run_id) is None
    assert store.read_detail("../etc") is None


def test_read_summary_and_cache_refresh(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    make_run(store, run_id)
    first_mtime_ns = store.paths(run_id).run_json.stat().st_mtime_ns
    s = store.read_summary(run_id)
    assert s is not None and s.state == "created" and s.counts_by_outcome == {"accepted": 2}
    assert type(s).__name__ == "RunSummary"
    assert store.read_summary(run_id) is s  # cached
    store.write_detail(detail(run_id, state="cloning"))
    newer = first_mtime_ns + 10**9  # independent of the filesystem's mtime granularity
    os.utime(store.paths(run_id).run_json, ns=(newer, newer))
    assert store.read_summary(run_id).state == "cloning"  # type: ignore[union-attr]
    assert store.read_summary("20261005-120000-missing") is None


def test_read_summary_sees_rewrite_with_same_mtime(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    make_run(store, run_id)
    mtime_ns = store.paths(run_id).run_json.stat().st_mtime_ns
    assert store.read_summary(run_id).state == "created"  # type: ignore[union-attr]
    store.write_detail(detail(run_id, state="cloning"))
    os.utime(store.paths(run_id).run_json, ns=(mtime_ns, mtime_ns))  # coarse-clock filesystem
    assert store.read_summary(run_id).state == "cloning"  # type: ignore[union-attr]


def test_list_summaries_newest_first_and_skips_junk(store: RunStore) -> None:
    make_run(store, "20261005-120000-old", created_ts=1)
    make_run(store, "20261005-130000-new", created_ts=3)
    make_run(store, "20261005-110000-mid", created_ts=2)
    (store.runs_dir / "20261005-140000-empty").mkdir()  # no run.json
    (store.runs_dir / "not-a-run").mkdir()
    (store.runs_dir / "20261005-150000-file").write_text("x")
    assert store.list_ids() == [
        "20261005-140000-empty",
        "20261005-130000-new",
        "20261005-120000-old",
        "20261005-110000-mid",
    ]
    assert [s.id for s in store.list_summaries()] == [
        "20261005-130000-new",
        "20261005-110000-mid",
        "20261005-120000-old",
    ]


def test_list_ids_without_runs_dir(tmp_path: Path) -> None:
    assert RunStore(tmp_path / "nope").list_ids() == []
    assert RunStore(tmp_path / "nope").list_summaries() == []


def test_iter_lines_and_last_seq_on_disk(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    rp = store.paths(run_id)
    rp.mkdirs()
    assert store.last_seq_on_disk(run_id) == 0
    assert list(store.iter_lines(run_id)) == []
    bus = EventBus(run_id, rp.events)
    for i in range(5):
        bus.emit("log", LogData(level="info", source="orchestrator", lines=[str(i)]))
    bus.close()
    with rp.events.open("ab") as f:
        f.write(b'{"seq":6,"ts":1,"run_')  # crash mid-write
    assert store.last_seq_on_disk(run_id) == 5
    assert [s for s, _ in store.iter_lines(run_id)] == [1, 2, 3, 4, 5]
    got = list(store.iter_lines(run_id, after=3))
    assert [s for s, _ in got] == [4, 5]
    assert all(line.startswith('{"seq":') for _, line in got)
    assert list(store.iter_lines(run_id, after=5)) == []


def test_is_owned_follows_the_run_lock(store: RunStore) -> None:
    run_id = "20261005-120000-a"
    store.paths(run_id).mkdirs()
    assert not store.is_owned(run_id)
    lock = FileLock(store.paths(run_id).lock)
    assert lock.try_acquire()
    assert store.is_owned(run_id)
    lock.release()
    assert not store.is_owned(run_id)


def test_run_paths_layout(tmp_path: Path) -> None:
    rp = RunPaths(tmp_path / "r")
    rp.mkdirs()
    for d in (rp.root, rp.logs, rp.tmp, rp.home, rp.out, rp.work):
        assert d.is_dir()
    assert rp.events.name == "events.jsonl"
    assert rp.fn("pkg.mod:f") == rp.root / "fn" / "pkg.mod__f"
    assert rp.candidate("pkg.mod:f", "B") == rp.work / "cand" / "pkg.mod__f" / "B"
