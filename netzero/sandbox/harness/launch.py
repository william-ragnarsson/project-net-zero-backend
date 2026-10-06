"""Run a module or script under resource limits.

``python -P -m _netzero_harness.launch [--cpu S] [--fsize-mb N] [--nofile N]
(-m module | script) args...``

The limits are a backstop behind the host's wall-clock timeout: a CPU-bound
loop gets ``SIGXCPU``, a runaway writer gets ``EFBIG`` and a descriptor leak
gets ``EMFILE``. A limit the OS refuses is skipped (macOS rejects some values).
The arguments are parsed by hand so the target's own options pass through untouched.
"""

from __future__ import annotations

import os
import resource
import runpy
import sys

USAGE = "usage: launch [--cpu S] [--fsize-mb N] [--nofile N] (-m module | script) args..."


def _set_limit(which: int, soft: int, hard: int) -> None:
    """Lower ``which`` to ``(soft, hard)``, never above the current hard limit."""
    _, cur_hard = resource.getrlimit(which)
    if cur_hard != resource.RLIM_INFINITY:
        soft, hard = min(soft, cur_hard), min(hard, cur_hard)
    try:
        resource.setrlimit(which, (soft, hard))
    except (ValueError, OSError):
        pass


def apply_limits(cpu_s: int | None, fsize_mb: int | None, nofile: int | None) -> None:
    if cpu_s is not None:
        # soft first: SIGXCPU ends the process; the hard limit (SIGKILL) is the fallback
        _set_limit(resource.RLIMIT_CPU, cpu_s, cpu_s + 2)
    if fsize_mb is not None:
        size = fsize_mb * 1024 * 1024
        _set_limit(resource.RLIMIT_FSIZE, size, size)
    if nofile is not None:
        _set_limit(resource.RLIMIT_NOFILE, nofile, nofile)


def parse(argv: list[str]) -> tuple[dict[str, int], str | None, str | None, list[str]]:
    """``(limits, module, script, rest)``; raises ``SystemExit`` on bad usage."""
    flags = {"--cpu": "cpu_s", "--fsize-mb": "fsize_mb", "--nofile": "nofile"}
    limits: dict[str, int] = {}
    i = 0
    while i < len(argv) and argv[i] in flags:
        if i + 1 >= len(argv):
            raise SystemExit(USAGE)
        try:
            limits[flags[argv[i]]] = int(argv[i + 1])
        except ValueError:
            raise SystemExit(USAGE) from None
        i += 2
    if i >= len(argv):
        raise SystemExit(USAGE)
    if argv[i] == "-m":
        if i + 1 >= len(argv):
            raise SystemExit(USAGE)
        return limits, argv[i + 1], None, argv[i + 2 :]
    return limits, None, argv[i], argv[i + 1 :]


def main(argv: list[str]) -> None:
    limits, module, script, rest = parse(argv)
    apply_limits(limits.get("cpu_s"), limits.get("fsize_mb"), limits.get("nofile"))
    if module is not None:
        sys.argv = [module, *rest]  # run_module replaces argv[0] with the module's path
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    else:
        assert script is not None
        sys.argv = [script, *rest]
        sys.path.insert(0, os.path.dirname(os.path.abspath(script)))  # as ``python script``
        runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
