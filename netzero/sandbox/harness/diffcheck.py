"""Differential check: replay the captured inputs and compare with the original's behaviour.

``python -m _netzero_harness.diffcheck record|check --module M --qualname Q
--inputs inputs.pkl --refs refs.pkl [--sample-timeout S]``

``record`` (original tree on ``PYTHONPATH``) calls the target once per sample
and writes ``refs.pkl``: per sample the pickled return value or the exception,
the pickled arguments *after* the call (mutations) and the wall time. Record:
``{n_samples, n_usable, mutates_args, raises, previews, ref_total_s}``.

``check`` (whichever tree ``PYTHONPATH`` points at) replays every usable sample
and compares: return values with ``nz_equal``, exceptions by type name (not
message), argument mutations with ``nz_equal``, and total time over samples
whose reference took at least 1 ms. Record: ``{ok, n_samples, mismatches (at
most 5), slowdown_ratio}``, shaped like ``DiffCheckResult``. A reference that no
longer unpickles on this tree, or a comparison that raises or runs past the
per-sample limit, is a ``return`` mismatch, not a crash.

Either mode emits ``{error}`` and exits 2 when it cannot start.
"""

from __future__ import annotations

import argparse
import os
import pickle
import signal
import sys
import time
from typing import Any

from _netzero_harness import common
from _netzero_harness.equality import nz_equal, short_repr

REFS_VERSION = 1
PER_SAMPLE_S = 10.0
SLOWDOWN_LIMIT = 4.0
MIN_TIMED_S = 0.001  # faster samples are timer noise
MAX_MISMATCHES = 5
N_PREVIEWS = 3
MAX_MESSAGE = 300
MAX_REFS_BYTES = 256 * 1024 * 1024  # results kept in refs.pkl; later samples become unusable


class _SampleTimeout(BaseException):
    """BaseException, so the target's ``except Exception`` cannot swallow it."""


def _on_alarm(signum: int, frame: Any) -> None:
    raise _SampleTimeout()


def _text(exc: BaseException) -> str:
    """``str(exc)``, even when the code under test broke its ``__str__``."""
    try:
        return str(exc)
    except Exception:
        return f"<{type(exc).__name__}.__str__ failed>"


Outcome = tuple[Any, tuple[str, str] | None, tuple[Any, ...], dict[str, Any], float]


def call(target: Any, blob: bytes, limit: float = PER_SAMPLE_S) -> Outcome:
    """``(return, (exc type, message) | None, args after, kwargs after, seconds)``;
    raises ``_SampleTimeout`` past ``limit`` seconds."""
    args, kwargs = common.fresh_args(blob)
    ret, exc = None, None
    signal.setitimer(signal.ITIMER_REAL, limit)
    t0 = time.perf_counter()
    try:
        ret = target(*args, **kwargs)
    except _SampleTimeout:
        raise
    except BaseException as e:  # SystemExit from the code under test is an outcome too
        exc = (type(e).__name__, _text(e)[:MAX_MESSAGE])
    finally:
        dt = time.perf_counter() - t0
        signal.setitimer(signal.ITIMER_REAL, 0)
    return ret, exc, args, kwargs, dt


def preview(name: str, blob: bytes) -> str:
    try:
        args, kwargs = common.fresh_args(blob)
    except Exception:
        return f"{name}(<inputs do not unpickle>)"
    parts = [short_repr(a) for a in args] + [f"{k}={short_repr(v)}" for k, v in kwargs.items()]
    text = f"{name}({', '.join(parts)})"
    return text if len(text) <= 160 else text[:157] + "..."


def record(
    target: Any, data: dict[str, Any], refs_path: str, limit: float = PER_SAMPLE_S
) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    mutates = raises = False
    kept = 0  # bytes of results in refs
    for sample in data["samples"]:
        blob = sample["blob"]
        try:
            ret, exc, args, kwargs, dt = call(target, blob, limit)
        except _SampleTimeout:
            refs.append({"usable": False, "why": f"took longer than {limit:g} s"})
            continue
        except Exception as e:  # the inputs themselves do not unpickle
            refs.append({"usable": False, "why": f"inputs: {type(e).__name__}: {_text(e)}"[:200]})
            continue
        try:
            ret_blob = None if exc else pickle.dumps(ret, protocol=common.PICKLE_PROTOCOL)
            after = pickle.dumps((args, kwargs), protocol=common.PICKLE_PROTOCOL)
        except Exception as e:
            refs.append({"usable": False, "why": f"result does not pickle: {type(e).__name__}"})
            continue
        size = len(ret_blob or b"") + len(after)
        if kept + size > MAX_REFS_BYTES:
            refs.append({"usable": False, "why": f"result too large to keep ({size} bytes)"})
            continue
        kept += size
        before = common.fresh_args(blob)
        mutates = mutates or not nz_equal(before, (args, kwargs))[0]
        raises = raises or exc is not None
        refs.append({"usable": True, "ret": ret_blob, "exc": exc, "after": after, "duration_s": dt})
    out = {
        "version": REFS_VERSION,
        "module": data["module"],
        "qualname": data["qualname"],
        "refs": refs,
    }
    tmp = f"{refs_path}.tmp"
    with open(tmp, "wb") as f:
        pickle.dump(out, f, protocol=common.PICKLE_PROTOCOL)
    os.replace(tmp, refs_path)
    name = data["qualname"].rsplit(".", 1)[-1]
    return {
        "n_samples": len(refs),
        "n_usable": sum(r["usable"] for r in refs),
        "mutates_args": mutates,
        "raises": raises,
        "previews": [preview(name, s["blob"]) for s in data["samples"][:N_PREVIEWS]],
        "ref_total_s": sum(r.get("duration_s", 0.0) for r in refs),
    }


def _mismatch(idx: int, kind: str, path: str, expected: str, actual: str) -> dict[str, Any]:
    return {"sample_idx": idx, "kind": kind, "path": path, "expected": expected, "actual": actual}


def _compare(idx: int, ref: dict[str, Any], out: Outcome) -> list[dict[str, Any]]:
    ret, exc, args, kwargs, _ = out
    found: list[dict[str, Any]] = []
    want_exc = ref["exc"]
    if want_exc is not None:
        if exc is None:
            found.append(
                _mismatch(
                    idx, "exception", "", f"raises {want_exc[0]}", f"returns {short_repr(ret)}"
                )
            )
        elif exc[0] != want_exc[0]:
            found.append(
                _mismatch(
                    idx, "exception", "", f"raises {want_exc[0]}", f"raises {exc[0]}: {exc[1]}"
                )
            )
    elif exc is not None:
        expected = short_repr(pickle.loads(ref["ret"]))
        found.append(
            _mismatch(idx, "exception", "", f"returns {expected}", f"raises {exc[0]}: {exc[1]}")
        )
    else:
        ok, path, e, a = nz_equal(pickle.loads(ref["ret"]), ret)
        if not ok:
            found.append(_mismatch(idx, "return", path, e, a))
    want_args, want_kwargs = pickle.loads(ref["after"])
    ok, path, e, a = nz_equal(tuple(want_args), args, root="args")
    if ok:
        ok, path, e, a = nz_equal(dict(want_kwargs), kwargs, root="kwargs")
    if not ok:
        found.append(_mismatch(idx, "mutation", path, e, a))
    return found


def _checked(idx: int, ref: dict[str, Any], out: Outcome, limit: float) -> list[dict[str, Any]]:
    """``_compare`` under the per-sample limit. A reference that does not unpickle on
    this tree (a class renamed or removed) or a result whose own ``__eq__`` raises or
    hangs is a mismatch of this candidate, not a crash of the check."""
    try:
        signal.setitimer(signal.ITIMER_REAL, limit)
        try:
            return _compare(idx, ref, out)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)  # disarmed before the handlers run
    except _SampleTimeout:
        why = f"comparing took over {limit:g} s"
        return [_mismatch(idx, "return", "", "the original's result", why)]
    except Exception as e:
        why = f"cannot compare: {type(e).__name__}: {_text(e)}"[:MAX_MESSAGE]
        return [_mismatch(idx, "return", "", "the original's result", why)]


def check(
    target: Any, data: dict[str, Any], refs: list[dict[str, Any]], limit: float = PER_SAMPLE_S
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    ref_s = cand_s = 0.0
    worst = (0.0, -1)  # (per-sample ratio, index) for the slowdown report
    n = 0
    for idx, (sample, ref) in enumerate(zip(data["samples"], refs, strict=False)):
        if not ref["usable"]:
            continue
        n += 1
        try:
            out = call(target, sample["blob"], limit)
        except _SampleTimeout:
            ref_ms = ref["duration_s"] * 1000
            mismatches.append(
                _mismatch(idx, "slowdown", "", f"{ref_ms:.1f} ms", f"over {limit:g} s (stopped)")
            )
            break  # the rest would likely hang too
        except Exception as e:  # inputs that loaded for the original fail on this tree
            why = f"inputs do not load: {type(e).__name__}: {_text(e)}"[:MAX_MESSAGE]
            mismatches.append(_mismatch(idx, "exception", "", "inputs load", why))
            continue
        mismatches += _checked(idx, ref, out, limit)
        if ref["duration_s"] >= MIN_TIMED_S:
            ref_s += ref["duration_s"]
            cand_s += out[4]
            worst = max(worst, (out[4] / ref["duration_s"], idx))
    ratio = cand_s / ref_s if ref_s > 0 else None
    if ratio is not None and ratio > SLOWDOWN_LIMIT:
        mismatches.append(
            _mismatch(
                worst[1],
                "slowdown",
                "",
                f"{ref_s * 1000:.1f} ms in total",
                f"{cand_s * 1000:.1f} ms in total ({ratio:.1f}x, limit {SLOWDOWN_LIMIT:g}x)",
            )
        )
    return {
        "ok": not mismatches,
        "n_samples": n,
        "mismatches": mismatches[:MAX_MISMATCHES],
        "slowdown_ratio": ratio,
    }


def load_refs(path: str) -> list[dict[str, Any]]:
    with open(path, "rb") as f:
        data = pickle.load(f)
    if not isinstance(data, dict) or data.get("version") != REFS_VERSION:
        raise ValueError(f"{path}: not a version {REFS_VERSION} refs file")
    return data["refs"]


def main(argv: list[str]) -> int:
    common.redirect_stdout()
    parser = argparse.ArgumentParser(prog="diffcheck")
    parser.add_argument("mode", choices=["record", "check"])
    parser.add_argument("--module", required=True)
    parser.add_argument("--qualname", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--refs", required=True)
    parser.add_argument("--sample-timeout", type=float, default=PER_SAMPLE_S)
    opts = parser.parse_args(argv)
    signal.signal(signal.SIGALRM, _on_alarm)
    try:
        data = common.load_samples(opts.inputs)
        refs = load_refs(opts.refs) if opts.mode == "check" else []
        target = common.load_target(opts.module, opts.qualname)
    except BaseException as e:
        common.emit({"error": f"{type(e).__name__}: {e}"[:500]})
        return 2
    if opts.mode == "record":
        common.emit(record(target, data, opts.refs, opts.sample_timeout))
    else:
        common.emit(check(target, data, refs, opts.sample_timeout))
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # threads or atexit hooks left by the code under test must not hold us
