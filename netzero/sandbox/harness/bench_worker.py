"""Persistent bench worker: times the target on its captured workload, on command.

``python -P -m _netzero_harness.bench_worker --module M --qualname Q --inputs inputs.pkl``
with ``PYTHONPATH`` = the harness root + the tree's import roots.

First record: ``{"ready": true, "samples": m}``, or ``{"error"}`` and exit 2
when the target or the samples do not load. Then one JSON command per stdin
line and one record per command:

* ``{"cmd": "warmup"}`` -> ``{"ok": true, "samples": m, "wall_s"}``: every
  workload sample once (imports, caches and lazy setup happen here, untimed).
* ``{"cmd": "time", "n": k}`` -> ``{"wall_s", "n"}``: wall time of k calls.
* ``{"cmd": "trial", "n": k, "start": i}`` -> ``{"cpu_s", "wall_s", "n"}``.
* ``{"cmd": "prepare", "n": k, "start": i}`` -> ``{"ok": true, "n": k}``, then
  ``{"cmd": "run"}`` -> ``{"cpu_s", "wall_s", "n"}``: a trial in two steps, so
  a meter the host wraps around ``run`` (RAPL) sees only the timed calls, not
  the argument copies or the collection before them.
* ``{"cmd": "stop"}``: exit (so does EOF on stdin).

Calls cycle round-robin over the workload samples from ``start`` (default 0),
so both arms of a comparison can replay the same samples in trial j. The
argument copies (``fresh_args``) are unpickled *before* the timers start, so the
timed loop only calls the target. ``gc.collect()`` runs first and the collector
is off during the loop, so one arm does not pay for the other's garbage.
``time.thread_time()`` counts this thread only (what the energy model charges);
``perf_counter()`` gives the wall time.

The target raising (anything, ``SystemExit`` included) replies ``{"error",
"traceback"}`` and the worker keeps serving; the host fails the bench.

Commands are read from a duplicate of fd 0, and fd 0 itself is pointed at
``/dev/null``, so code under test that reads stdin cannot swallow a command.
Likewise records go to a private duplicate of fd 1, and fd 1 itself is pointed
at stderr, so nothing the code under test writes to fd 1 (``os.write(1, ...)``,
``sys.__stdout__``, a child process) can forge, delay or hold open the record
stream.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
import traceback
from typing import IO, Any

from _netzero_harness import common

MAX_ERROR = 500
MAX_TRACEBACK = 3000

Calls = list[tuple[tuple, dict]]


def take_stdin() -> IO[str]:
    """The command stream; fd 0 and ``sys.stdin`` become ``/dev/null``."""
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdin = open(os.devnull, encoding="utf-8")
    return commands


def build_calls(blobs: list[bytes], n: int, start: int) -> Calls:
    m = len(blobs)
    return [common.fresh_args(blobs[(start + i) % m]) for i in range(n)]


def timed(target: Any, calls: Calls, *, collect: bool = True) -> tuple[float, float]:
    """``(cpu_s, wall_s)`` of calling ``target`` on each prepared argument set."""
    if collect:
        gc.collect()
    gc.disable()
    try:
        c0 = time.thread_time()
        w0 = time.perf_counter()
        for args, kwargs in calls:
            target(*args, **kwargs)
        w1 = time.perf_counter()
        c1 = time.thread_time()
    finally:
        gc.enable()
    return c1 - c0, w1 - w0


def count(cmd: dict[str, Any]) -> int:
    n = int(cmd.get("n", 1))
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    return n


def handle(
    target: Any, blobs: list[bytes], cmd: dict[str, Any], prepared: list[Calls] | None = None
) -> dict[str, Any]:
    """One command's record. ``prepared`` holds the calls a ``prepare`` built
    for the next ``run`` (at most one set)."""
    prepared = [] if prepared is None else prepared
    name = cmd.get("cmd")
    if name == "warmup":
        _, wall = timed(target, build_calls(blobs, len(blobs), 0))
        return {"ok": True, "samples": len(blobs), "wall_s": wall}
    if name == "prepare":
        prepared.clear()
        n = count(cmd)
        prepared.append(build_calls(blobs, n, int(cmd.get("start", 0))))
        gc.collect()
        return {"ok": True, "n": n}
    if name == "run":
        if not prepared:
            raise ValueError("run without prepare")
        calls = prepared.pop()
        cpu, wall = timed(target, calls, collect=False)
        return {"cpu_s": cpu, "wall_s": wall, "n": len(calls)}
    if name not in ("time", "trial"):
        raise ValueError(f"unknown command {name!r}")
    n = count(cmd)
    calls = build_calls(blobs, n, int(cmd.get("start", 0)))
    cpu, wall = timed(target, calls)
    del calls
    if name == "time":
        return {"wall_s": wall, "n": n}
    return {"cpu_s": cpu, "wall_s": wall, "n": n}


def serve(target: Any, blobs: list[bytes], commands: IO[str]) -> int:
    prepared: list[Calls] = []
    for line in commands:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            if not isinstance(cmd, dict):
                raise ValueError("not an object")
        except ValueError:
            common.emit({"error": f"bad command: {line[:200]}"})
            continue
        if cmd.get("cmd") == "stop":
            return 0
        try:
            rec = handle(target, blobs, cmd, prepared)
        except BaseException as e:  # the code under test may raise anything
            tb = "".join(traceback.format_exception(e))[-MAX_TRACEBACK:]
            rec = {"error": f"{type(e).__name__}: {e}"[:MAX_ERROR], "traceback": tb}
        common.emit(rec)
    return 0


def main(argv: list[str]) -> int:
    commands = take_stdin()
    common.redirect_stdout()
    parser = argparse.ArgumentParser(prog="bench_worker")
    parser.add_argument("--module", required=True)
    parser.add_argument("--qualname", required=True)
    parser.add_argument("--inputs", required=True)
    opts = parser.parse_args(argv)
    try:
        blobs = common.workload_blobs(common.load_samples(opts.inputs))
        if not blobs:
            raise ValueError("no captured samples")
        target = common.load_target(opts.module, opts.qualname)
    except BaseException as e:
        common.emit({"error": f"{type(e).__name__}: {e}"[:MAX_ERROR]})
        return 2
    common.emit({"ready": True, "samples": len(blobs)})
    return serve(target, blobs, commands)


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # threads or atexit hooks left by the code under test must not hold us
