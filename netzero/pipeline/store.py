"""The ``runs/`` folder: run ids, paths, ``run.json``, ``events.jsonl`` and locks.

There is no database. A run is a folder; ``run.json`` is the projection of
``events.jsonl`` (see ``projection.py``) and is rewritten atomically.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from netzero.events import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_STATES,
    RunDetail,
    RunSummary,
    line_seq,
    line_type,
)

RUN_ID_RE = re.compile(r"\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,47}")  # use fullmatch
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")[:max_len].strip("-")
    return slug or "run"


_FN_UNSAFE = re.compile(r"[^A-Za-z0-9_.]+")
FN_SLUG_MAX = 120


def fn_slug(function_id: str) -> str:
    """Filesystem-safe slug for a ``module:Qual.name`` function id, distinct per id.

    A plain id reads as ``pkg.mod__Qual.name``. When that mapping could confuse
    two ids (``__`` already in the id, unsafe characters, a leading dot, a long
    id) the slug is shortened and gets a hash suffix, so two functions never
    share an ``fn/`` or ``work/cand/`` directory.
    """
    plain = function_id.replace(":", "__")
    readable = _FN_UNSAFE.sub("_", plain).lstrip(".")
    if readable == plain and "__" not in function_id and 0 < len(plain) <= FN_SLUG_MAX:
        return plain
    digest = hashlib.sha256(function_id.encode()).hexdigest()[:12]
    return f"{readable[: FN_SLUG_MAX - 13] or '_'}-{digest}"


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def run_json(self) -> Path:
        return self.root / "run.json"

    @property
    def events(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def procs(self) -> Path:
        return self.root / "procs.jsonl"

    @property
    def lock(self) -> Path:
        return self.root / ".lock"

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def venv(self) -> Path:
        return self.root / "venv"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def trunk(self) -> Path:
        return self.work / "trunk"

    def candidate(self, function_id: str, candidate_id: str) -> Path:
        return self.work / "cand" / fn_slug(function_id) / candidate_id

    def fn(self, function_id: str) -> Path:
        return self.root / "fn" / fn_slug(function_id)

    @property
    def harness(self) -> Path:
        return self.root / "harness"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def mkdirs(self) -> None:
        for d in (self.root, self.logs, self.tmp, self.home, self.out, self.work, self.root / "fn"):
            d.mkdir(parents=True, exist_ok=True)


class FileLock:
    """Non-blocking ``flock`` held for the lifetime of the object (or until release)."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    @staticmethod
    def is_locked(path: Path) -> bool:
        """True if some open file description (any process) holds the lock."""
        if not path.exists():
            return False
        probe = FileLock(path)
        if probe.try_acquire():
            probe.release()
            return False
        return True


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


def repair_tail(path: Path) -> int:
    """Truncate a partial trailing line (crash mid-write). Returns bytes removed."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    if size == 0:
        return 0
    with path.open("rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return 0
        # walk back to the last newline
        pos = size
        chunk = 4096
        while pos > 0:
            start = max(0, pos - chunk)
            f.seek(start)
            buf = f.read(pos - start)
            i = buf.rfind(b"\n")
            if i != -1:
                keep = start + i + 1
                f.truncate(keep)
                return size - keep
            pos = start
        f.truncate(0)
        return size


def read_complete_lines(path: Path, offset: int = 0) -> tuple[list[str], int]:
    """Complete lines from ``offset``; returns them and the offset after the last one."""
    try:
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n")
    if end == -1:
        return [], offset
    chunk = data[: end + 1]
    lines = [ln.decode("utf-8") for ln in chunk.split(b"\n")[:-1] if ln]
    return lines, offset + end + 1


def last_complete_line(path: Path, chunk: int = 8192) -> str | None:
    """The last complete line of a file, read from the end."""
    try:
        f = path.open("rb")
    except FileNotFoundError:
        return None
    with f:
        end = f.seek(0, os.SEEK_END)
        buf = b""
        pos = end
        while pos > 0:
            start = max(0, pos - chunk)
            f.seek(start)
            buf = f.read(pos - start) + buf
            pos = start
            last_nl = buf.rfind(b"\n")
            if last_nl == -1:
                continue  # no complete line yet
            prev_nl = buf.rfind(b"\n", 0, last_nl)
            if prev_nl != -1 or pos == 0:
                return buf[prev_nl + 1 : last_nl].decode("utf-8")
        return None


class RunStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self._summary_cache: dict[str, tuple[tuple[int, ...], RunSummary]] = {}

    # -- ids + paths ---------------------------------------------------------

    def new_run_id(self, label: str, now: float | None = None) -> str:
        stamp = datetime.fromtimestamp(now if now is not None else time.time()).strftime(
            "%Y%m%d-%H%M%S"
        )
        base = f"{stamp}-{slugify(label)}"
        run_id, n = base, 2
        while (self.runs_dir / run_id).exists():
            run_id = f"{base}-{n}"
            n += 1
        return run_id

    def valid_id(self, run_id: str) -> bool:
        return bool(RUN_ID_RE.fullmatch(run_id))

    def paths(self, run_id: str) -> RunPaths:
        if not self.valid_id(run_id):
            raise KeyError(run_id)
        return RunPaths(self.runs_dir / run_id)

    def exists(self, run_id: str) -> bool:
        return self.valid_id(run_id) and (self.runs_dir / run_id / "run.json").is_file()

    @property
    def active_lock_path(self) -> Path:
        return self.runs_dir / ".active.lock"

    # -- run.json ------------------------------------------------------------

    def read_detail(self, run_id: str) -> RunDetail | None:
        if not self.exists(run_id):
            return None
        try:
            return RunDetail.model_validate_json(self.paths(run_id).run_json.read_text("utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def write_detail(self, detail: RunDetail) -> None:
        write_atomic(self.paths(detail.id).run_json, detail.model_dump_json(indent=1))

    def read_summary(self, run_id: str) -> RunSummary | None:
        path = self.runs_dir / run_id / "run.json"
        try:
            st = path.stat()
        except OSError:
            return None
        # run.json is replaced atomically, so a rewrite within the filesystem's
        # mtime granularity still changes the inode
        key = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
        cached = self._summary_cache.get(run_id)
        if cached and cached[0] == key:
            return cached[1]
        detail = self.read_detail(run_id)
        if detail is None:
            return None
        summary = to_summary(detail)
        self._summary_cache[run_id] = (key, summary)
        return summary

    def list_ids(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        ids = [p.name for p in self.runs_dir.iterdir() if p.is_dir() and self.valid_id(p.name)]
        return sorted(ids, reverse=True)

    def list_summaries(self) -> list[RunSummary]:
        out = [s for rid in self.list_ids() if (s := self.read_summary(rid)) is not None]
        out.sort(key=lambda s: s.created_ts, reverse=True)
        return out

    def is_owned(self, run_id: str) -> bool:
        """Some process (this one or another) currently drives the run."""
        return FileLock.is_locked(self.paths(run_id).lock)

    # -- events.jsonl ----------------------------------------------------------

    def iter_lines(self, run_id: str, after: int = 0) -> Iterator[tuple[int, str]]:
        """Complete persisted lines with ``seq > after``, in order."""
        lines, _ = read_complete_lines(self.paths(run_id).events)
        for line in lines:
            seq = line_seq(line)
            if seq > after:
                yield seq, line

    def has_terminal_event(self, run_id: str) -> bool:
        """The log ends with a terminal event (a terminal state alone is not enough:
        the process can die between ``run.state_changed`` and the terminal event)."""
        line = last_complete_line(self.paths(run_id).events)
        return line is not None and line_type(line) in TERMINAL_EVENT_TYPES

    def last_seq_on_disk(self, run_id: str) -> int:
        lines, _ = read_complete_lines(self.paths(run_id).events)
        return line_seq(lines[-1]) if lines else 0


def to_summary(detail: RunDetail) -> RunSummary:
    return RunSummary.model_validate(detail.model_dump(include=set(RunSummary.model_fields)))


def is_terminal_state(state: str) -> bool:
    return state in TERMINAL_STATES
