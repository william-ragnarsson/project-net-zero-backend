"""Track the process groups a run spawns, so cancel and recovery can kill them.

Every sandboxed child starts its own session (``start_new_session=True``), so
its pid is also its process-group id. ``procs.jsonl`` is append-only:
``{"op": "start", "pid", "ct", "label"}`` and ``{"op": "exit", "pid"}``.
``ct`` (process create time) guards against killing a recycled pid after a
reboot or a long downtime.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import psutil


def _create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def _killpg(pgid: int) -> bool:
    try:
        os.killpg(pgid, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class ProcRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.live: dict[int, str] = {}  # pid (== pgid) -> label

    def _append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def add(self, pid: int, label: str = "") -> None:
        self.live[pid] = label
        self._append({"op": "start", "pid": pid, "ct": _create_time(pid), "label": label})

    def remove(self, pid: int) -> None:
        if self.live.pop(pid, None) is not None:
            self._append({"op": "exit", "pid": pid})

    def kill_all(self) -> int:
        n = 0
        for pid in list(self.live):
            n += _killpg(pid)
            self.remove(pid)
        return n


def kill_leftovers(path: Path) -> int:
    """Kill process groups recorded as started but never exited (crash recovery)."""
    if not path.exists():
        return 0
    started: dict[int, float | None] = {}
    for line in path.read_text("utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("op") == "start":
            started[rec["pid"]] = rec.get("ct")
        elif rec.get("op") == "exit":
            started.pop(rec.get("pid"), None)
    n = 0
    for pid, ct in started.items():
        if ct is None:
            continue
        now_ct = _create_time(pid)
        if now_ct is not None:
            # the leader is alive: only kill if it is still the process we started
            if abs(now_ct - ct) < 1.0:
                n += _killpg(pid)
        elif _group_alive_since(pid, ct):
            # leader gone, members left; a pgid is not reused while its group exists
            n += _killpg(pid)
    return n


def _group_alive_since(pgid: int, ct: float) -> bool:
    for proc in psutil.process_iter(["pid", "create_time"]):
        try:
            if os.getpgid(proc.info["pid"]) == pgid and proc.info["create_time"] >= ct - 1.0:
                return True
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
    return False
