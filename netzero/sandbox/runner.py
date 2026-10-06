"""Run child processes in their own process group, with a timeout and capped output.

Every child starts a new session (``start_new_session=True``), so its pid is
its process-group id and one ``killpg`` takes down grandchildren too. Each
group is registered in the run's ``procs.jsonl`` while it lives, so a cancel
or a crash recovery can kill it. ``stdin`` is ``/dev/null`` unless the caller
talks to the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from netzero.sandbox.procs import ProcRegistry

LineCallback = Callable[[str, str], None]  # (stream, line)

MAX_OUTPUT = 256_000  # chars of tail kept per stream
MAX_PARTIAL = 64_000  # a "line" longer than this is split


@dataclass
class ProcResult:
    argv: list[str]
    returncode: int | None  # None when killed on timeout
    stdout: str  # tail, at most MAX_OUTPUT chars
    stderr: str
    timed_out: bool
    duration_s: float
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, n: int = 40) -> str:
        lines = self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr
        return "\n".join(lines.splitlines()[-n:])


def kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class _Tail:
    def __init__(self, cap: int):
        self.cap = cap
        self.chunks: deque[str] = deque()
        self.size = 0
        self.truncated = False

    def add(self, line: str) -> None:
        self.chunks.append(line)
        self.size += len(line) + 1
        while self.size > self.cap and len(self.chunks) > 1:
            self.size -= len(self.chunks.popleft()) + 1
            self.truncated = True

    def text(self) -> str:
        return "\n".join(self.chunks)


async def _pump(
    stream: asyncio.StreamReader | None, name: str, tail: _Tail, on_line: LineCallback | None
) -> None:
    if stream is None:
        return
    partial = ""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        partial += chunk.decode("utf-8", errors="replace")
        *lines, partial = partial.split("\n")
        if len(partial) > MAX_PARTIAL:
            lines.append(partial)
            partial = ""
        for line in lines:
            line = line.rstrip("\r")
            tail.add(line)
            if on_line:
                on_line(name, line)
    if partial:
        tail.add(partial)
        if on_line:
            on_line(name, partial)


async def _wait_exit(proc: asyncio.subprocess.Process, deadline: float) -> bool:
    """Wait for the child to exit; True on timeout. ``proc.wait()`` also waits
    for the pipes to close, which a daemonised grandchild can hold open, so the
    return code is polled alongside it."""
    waiter = asyncio.ensure_future(proc.wait())
    delay = 0.01
    try:
        while not waiter.done() and proc.returncode is None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            await asyncio.wait({waiter}, timeout=min(delay, remaining))
            delay = min(delay * 2, 0.25)
        return False
    finally:
        waiter.cancel()


async def spawn(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    procs: ProcRegistry | None = None,
    label: str = "",
    stdin_pipe: bool = False,
    merge_stderr: bool = False,
) -> asyncio.subprocess.Process:
    """Start a child in its own session; registered in ``procs`` until ``reap``."""
    proc = await asyncio.create_subprocess_exec(
        *[os.fspath(a) for a in argv],
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.PIPE if stdin_pipe else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=2**20,
    )
    if procs is not None:
        procs.add(proc.pid, label)
    return proc


async def reap(proc: asyncio.subprocess.Process, procs: ProcRegistry | None = None) -> None:
    """Kill the child's whole group and wait for the child; safe to call twice."""
    kill_group(proc.pid)
    with contextlib.suppress(ProcessLookupError):
        await asyncio.shield(proc.wait())
    if procs is not None:
        procs.remove(proc.pid)


async def run(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    procs: ProcRegistry | None = None,
    label: str = "",
    on_line: LineCallback | None = None,
    stdin_data: bytes | None = None,
    max_output: int = MAX_OUTPUT,
) -> ProcResult:
    """Run to completion (or ``timeout``); the group is always killed afterwards,
    so daemons a test leaves behind do not outlive it. Cancellation kills the
    group and re-raises."""
    t0 = time.perf_counter()
    proc = await spawn(
        argv, cwd=cwd, env=env, procs=procs, label=label, stdin_pipe=stdin_data is not None
    )
    out, err = _Tail(max_output), _Tail(max_output)
    pumps = asyncio.ensure_future(
        asyncio.gather(
            _pump(proc.stdout, "stdout", out, on_line), _pump(proc.stderr, "stderr", err, on_line)
        )
    )
    timed_out = False
    try:
        if stdin_data is not None and proc.stdin is not None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
            proc.stdin.close()
        timed_out = await _wait_exit(proc, t0 + timeout)
        # kills the leader on timeout, and anything left in its group either way
        # (a daemon holding the pipes would otherwise keep the pumps open)
        kill_group(proc.pid)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pumps), 5)
    finally:
        if not pumps.done():
            pumps.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pumps
        await reap(proc, procs)
    return ProcResult(
        argv=[os.fspath(a) for a in argv],
        returncode=None if timed_out else proc.returncode,
        stdout=out.text(),
        stderr=err.text(),
        timed_out=timed_out,
        duration_s=time.perf_counter() - t0,
        truncated=out.truncated or err.truncated,
    )
