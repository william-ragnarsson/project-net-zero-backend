"""Shared by the harness scripts: the record protocol, target lookup and the samples file.

Stdlib only, and nothing here may import ``netzero`` (see the package docstring).

Records: a harness script writes ``\\n__NZ__ <json>\\n`` to a private copy of its
stdout. ``redirect_stdout()`` makes fd 1 (and ``sys.stdout``) a copy of stderr
first, so the repo's code, its C extensions and its subprocesses can neither
split nor forge a record. The host keeps stdout lines that start with ``MARK``.

``inputs.pkl`` (written by the capture plugin, read by diffcheck and the bench
worker)::

    {"version": 1, "module": str, "qualname": str,
     "samples": [{"blob": bytes, "workload": bool, "nodeid": str}, ...]}

``blob`` is ``pickle.dumps((args, kwargs))`` taken *before* the call, so every
``fresh_args(blob)`` is an independent copy of the original arguments.
``workload`` marks calls made while the ``nz_workload`` test was running.
"""

from __future__ import annotations

import importlib
import json
import os
import pickle
import sys
from typing import IO, Any

MARK = "__NZ__ "
FORMAT_VERSION = 1
PICKLE_PROTOCOL = 5

_REAL_STDOUT: IO[str] = sys.stdout


def redirect_stdout() -> None:
    """Send everything else written to stdout to stderr; records keep a private,
    non-inheritable duplicate of the real fd 1."""
    global _REAL_STDOUT
    sys.stdout.flush()
    private = os.dup(1)
    os.dup2(2, 1)
    _REAL_STDOUT = os.fdopen(private, "w", encoding="utf-8")
    sys.stdout = sys.stderr


def emit(record: dict[str, Any]) -> None:
    """One record on its own line (the leading newline keeps a partial line apart)."""
    _REAL_STDOUT.write(f"\n{MARK}{json.dumps(record, default=repr)}\n")
    _REAL_STDOUT.flush()


def parse_record(line: str) -> dict[str, Any] | None:
    if not line.startswith(MARK):
        return None
    try:
        rec = json.loads(line[len(MARK) :])
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def add_roots(roots: list[str]) -> None:
    """Put the repo's import roots first on ``sys.path`` (after the harness imports)."""
    real = [os.path.realpath(r) for r in roots]
    sys.path[:0] = [r for r in real if r not in sys.path]


def lookup(module: str, qualname: str) -> tuple[Any, str, Any]:
    """``(owner, attr, raw)``: the module or class that holds the function, the
    attribute name, and the object stored there (a ``staticmethod`` or
    ``classmethod`` wrapper is returned as-is)."""
    owner: Any = importlib.import_module(module)
    parts = qualname.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part)
    attr = parts[-1]
    raw = owner.__dict__[attr] if isinstance(owner, type) else getattr(owner, attr)
    return owner, attr, raw


def callable_target(raw: Any) -> Any:
    """What the captured arguments are passed to: the function itself, with
    ``self`` (or ``cls``) as the first captured argument for methods."""
    if isinstance(raw, (staticmethod, classmethod)):
        return raw.__func__
    return raw


def load_target(module: str, qualname: str) -> Any:
    return callable_target(lookup(module, qualname)[2])


def save_samples(
    path: str | os.PathLike[str], module: str, qualname: str, samples: list[dict[str, Any]]
) -> None:
    data = {"version": FORMAT_VERSION, "module": module, "qualname": qualname, "samples": samples}
    tmp = f"{os.fspath(path)}.tmp"
    with open(tmp, "wb") as f:
        pickle.dump(data, f, protocol=PICKLE_PROTOCOL)
    os.replace(tmp, path)


def load_samples(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = pickle.load(f)
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise ValueError(f"{os.fspath(path)}: not a version {FORMAT_VERSION} samples file")
    return data


def workload_blobs(data: dict[str, Any]) -> list[bytes]:
    """The samples to benchmark: the workload test's calls, or every call if it made none."""
    samples = data["samples"]
    chosen = [s["blob"] for s in samples if s.get("workload")]
    return chosen or [s["blob"] for s in samples]


def fresh_args(blob: bytes) -> tuple[tuple[Any, ...], dict[str, Any]]:
    args, kwargs = pickle.loads(blob)
    return tuple(args), dict(kwargs)
