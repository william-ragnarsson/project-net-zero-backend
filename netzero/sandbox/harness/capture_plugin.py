"""Pytest plugin that records the arguments of every outermost call to one function.

Loaded with ``-p _netzero_harness.capture_plugin`` and configured by the env:
``NZ_CAPTURE_MODULE``, ``NZ_CAPTURE_QUALNAME`` and ``NZ_CAPTURE_OUT`` (the
``inputs.pkl`` to write). Without them it does nothing.

The target is wrapped on its owner before collection, so ``from pkg.mod import f``
in the test module, and the module's own global lookups, reach the wrapper.
Arguments are pickled *before* the call (the function may mutate them).
Recursive calls are not samples; identical blobs are kept once. At session
finish the samples go to ``inputs.pkl`` (see ``common``) and one record is
emitted: ``{n_calls, n_unpicklable, n_kept, total_bytes, n_dropped}``, or
``{error}`` when the target could not be wrapped.
"""

from __future__ import annotations

import functools
import hashlib
import io
import os
import pickle
import sys
import threading
from typing import Any

import pytest
from _netzero_harness import common

MAX_SAMPLES = 256
MAX_BYTES = 32 * 1024 * 1024
MAX_BLOB = 4 * 1024 * 1024
# calls outside the workload test may fill only this share, so the workload always fits
NON_WORKLOAD_SHARE = 0.75

_MODULE = os.environ.get("NZ_CAPTURE_MODULE")
_QUALNAME = os.environ.get("NZ_CAPTURE_QUALNAME")
_OUT = os.environ.get("NZ_CAPTURE_OUT")


class _Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.local = threading.local()
        self.samples: list[dict[str, Any]] = []
        self.index: dict[bytes, int] = {}  # blob digest -> sample position
        self.n_calls = 0
        self.n_unpicklable = 0
        self.n_dropped = 0  # over the caps
        self.total_bytes = 0
        self.nodeid = ""
        self.workload = False
        self.test_dir: str | None = None
        self.error: str | None = None

    def full(self, workload: bool) -> bool:
        share = 1.0 if workload else NON_WORKLOAD_SHARE
        return len(self.samples) >= MAX_SAMPLES * share or self.total_bytes >= MAX_BYTES * share

    def record(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        workload, nodeid = self.workload, self.nodeid
        with self.lock:
            self.n_calls += 1
            if self.full(workload):
                self.n_dropped += 1
                return
        try:
            blob = pickle.dumps((args, kwargs), protocol=common.PICKLE_PROTOCOL)
            _PortableUnpickler(io.BytesIO(blob), self.test_dir).load()
        except Exception:
            with self.lock:
                self.n_unpicklable += 1
            return
        digest = hashlib.sha1(blob).digest()
        with self.lock:
            if digest in self.index:
                if workload:
                    self.samples[self.index[digest]]["workload"] = True
                return
            if len(blob) > MAX_BLOB or self.full(workload):
                self.n_dropped += 1
                return
            self.index[digest] = len(self.samples)
            self.samples.append({"blob": blob, "workload": workload, "nodeid": nodeid})
            self.total_bytes += len(blob)


class _PortableUnpickler(pickle.Unpickler):
    """Round-trip check that also refuses classes from the generated test module:
    diffcheck and the bench worker run without it on ``sys.path``."""

    def __init__(self, file: io.BytesIO, test_dir: str | None):
        super().__init__(file)
        self.test_dir = test_dir

    def find_class(self, module: str, name: str) -> Any:
        path = getattr(sys.modules.get(module), "__file__", None)
        if self.test_dir and path and os.path.realpath(path).startswith(self.test_dir + os.sep):
            raise pickle.UnpicklingError(f"{module}.{name} is defined by the test file")
        return super().find_class(module, name)


_rec = _Recorder()


def _wrap(func: Any) -> Any:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        depth = getattr(_rec.local, "depth", 0)
        if depth == 0:
            try:
                _rec.record(args, kwargs)
            except Exception:  # bookkeeping must never change the call
                _rec.n_unpicklable += 1
        _rec.local.depth = depth + 1
        try:
            return func(*args, **kwargs)
        finally:
            _rec.local.depth = depth

    wrapper.__nz_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


def install(module: str, qualname: str) -> None:
    """Replace the target on its owner (keeping a staticmethod/classmethod wrapper);
    a module-level function is also replaced in modules that re-export it."""
    owner, attr, raw = common.lookup(module, qualname)
    func = common.callable_target(raw)
    wrapper = _wrap(func)
    if isinstance(raw, staticmethod):
        setattr(owner, attr, staticmethod(wrapper))
    elif isinstance(raw, classmethod):
        setattr(owner, attr, classmethod(wrapper))
    else:
        setattr(owner, attr, wrapper)
    if "." not in qualname:
        for mod in list(sys.modules.values()):
            ns = getattr(mod, "__dict__", None)
            if not isinstance(ns, dict):
                continue
            for name, value in list(ns.items()):
                if value is func:
                    ns[name] = wrapper


if _MODULE and _QUALNAME and _OUT:
    common.redirect_stdout()  # pytest's own report goes to stderr; stdout carries the record

    def pytest_sessionstart(session: pytest.Session) -> None:
        _rec.test_dir = os.path.realpath(str(session.config.rootpath))
        try:
            install(_MODULE, _QUALNAME)
        except BaseException as exc:
            _rec.error = f"cannot wrap {_MODULE}:{_QUALNAME}: {type(exc).__name__}: {exc}"[:500]
            common.emit({"error": _rec.error})
            pytest.exit(_rec.error, returncode=3)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
        _rec.nodeid = item.nodeid
        _rec.workload = item.get_closest_marker("nz_workload") is not None
        try:
            return (yield)
        finally:
            _rec.nodeid, _rec.workload = "", False

    def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
        if _rec.error is not None:
            return
        with _rec.lock:
            samples = list(_rec.samples)
        common.save_samples(_OUT, _MODULE, _QUALNAME, samples)
        common.emit(
            {
                "n_calls": _rec.n_calls,
                "n_unpicklable": _rec.n_unpicklable,
                "n_kept": len(samples),
                "total_bytes": _rec.total_bytes,
                "n_dropped": _rec.n_dropped,
            }
        )
