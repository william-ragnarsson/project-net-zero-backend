"""``nz_equal``: the equality the differential check uses for returns and mutated arguments.

Stricter than ``==`` where a rewrite could change behaviour callers can see
(``1`` vs ``True`` vs ``1.0``, a dict turned into a list, an object's fields),
looser where reordering arithmetic is legitimate (floats within ``rel_tol=1e-9``).
Dict order is ignored; numpy is used only if the code under test already imported it.
Objects whose ``==`` is identity (``random.Random``, ``object()``) compare by the
state pickle would save. Code of the objects' own that raises makes them unequal.

Stdlib only; runs inside the target venv.
"""

from __future__ import annotations

import math
import reprlib
import sys
import types
from typing import Any

REL_TOL = 1e-9
ABS_TOL = 1e-12
MAX_REPR = 200

Result = tuple[bool, str, str, str]  # (ok, path, expected_repr, actual_repr)
_OK: Result = (True, "", "", "")
_IMMUTABLETYPE = 1 << 8  # Py_TPFLAGS_IMMUTABLETYPE: set on C types (3.10+)
_HEAPTYPE = 1 << 9  # Py_TPFLAGS_HEAPTYPE: Python classes, but also many C types
_IDENTITY_TYPES = (
    type,
    types.FunctionType,
    types.BuiltinFunctionType,
    types.MethodType,
    types.ModuleType,
)

_repr = reprlib.Repr(maxlevel=3, maxlist=8, maxtuple=8, maxdict=8, maxset=8, maxstring=80)
_repr.maxother = MAX_REPR


def short_repr(obj: Any) -> str:
    try:
        text = _repr.repr(obj)
    except Exception as exc:  # a broken __repr__ must not break the check
        text = f"<{type(obj).__qualname__} (repr failed: {type(exc).__name__})>"
    return text if len(text) <= MAX_REPR else text[: MAX_REPR - 3] + "..."


def nz_equal(expected: Any, actual: Any, *, root: str = "") -> Result:
    """``(ok, path, expected_repr, actual_repr)`` for the first difference found;
    ``path`` (e.g. ``"[2]['key'].attr"``) is relative to ``root``."""
    try:
        return _Comparer().compare(expected, actual, root)
    except RecursionError:
        return False, root, "<too deeply nested to compare>", short_repr(actual)
    except Exception as exc:  # the objects' own code (__eq__, __hash__...) raised: not equal
        return False, root, f"<cannot compare: {type(exc).__name__}>", short_repr(actual)


def _floats_equal(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _is_python_class(t: type) -> bool:
    """Defined by a class statement, so its state is its fields (``deque`` is a heap
    type on 3.12 but immutable, like most C types built from a spec).

    Extension types built from a spec *without* the immutable flag (PyO3, pybind11:
    ``pydantic_core.Url``) are heap types too, but keep their state in C, with no
    ``__dict__`` and no ``__slots__``; a class statement always gives one or the
    other. A stateless type (``typing.Generic``) has no fields either way."""
    if t.__flags__ & (_HEAPTYPE | _IMMUTABLETYPE) != _HEAPTYPE:
        return False
    stateless = t.__basicsize__ <= object.__basicsize__ and not t.__itemsize__
    return t.__dictoffset__ != 0 or "__slots__" in t.__dict__ or stateless


def _has_builtin_state(t: type) -> bool:
    """True when a builtin base (``deque``, ``int``...) keeps state outside the fields."""
    return any(not _is_python_class(b) for b in t.__mro__[1:] if b is not object)


def _slot_names(t: type) -> list[str]:
    names: list[str] = []
    for klass in t.__mro__:
        slots = klass.__dict__.get("__slots__", ())
        for name in [slots] if isinstance(slots, str) else slots:
            if name not in ("__dict__", "__weakref__") and name not in names:
                names.append(name)
    return names


_MISSING = object()


def _state(obj: Any, name: str) -> Any:
    """A field as stored (``__getattr__`` fallbacks are not state); ``_MISSING`` when unset."""
    try:
        return object.__getattribute__(obj, name)
    except Exception:
        return _MISSING


class _Comparer:
    def __init__(self) -> None:
        self.active: set[tuple[int, int]] = set()  # pairs being compared: cycles count as equal

    def mismatch(self, path: str, expected: Any, actual: Any) -> Result:
        exp = "<missing>" if expected is _MISSING else short_repr(expected)
        act = "<missing>" if actual is _MISSING else short_repr(actual)
        return False, path, exp, act

    def compare(self, e: Any, a: Any, path: str) -> Result:
        if e is a:
            return _OK
        if type(e) is not type(a):
            return False, path, _typed(e), _typed(a)
        key = (id(e), id(a))
        if key in self.active:
            return _OK
        self.active.add(key)
        try:
            return self._compare_same_type(e, a, path)
        finally:
            self.active.discard(key)

    def _compare_same_type(self, e: Any, a: Any, path: str) -> Result:
        if isinstance(e, float):
            return _OK if _floats_equal(e, a) else self.mismatch(path, e, a)
        if isinstance(e, complex):
            ok = _floats_equal(e.real, a.real) and _floats_equal(e.imag, a.imag)
            return _OK if ok else self.mismatch(path, e, a)
        if isinstance(e, (str, bytes, int)):  # bool included
            return _OK if e == a else self.mismatch(path, e, a)
        if isinstance(e, (list, tuple)):
            return self._sequences(e, a, path)
        if isinstance(e, dict):
            return self._dicts(e, a, path)
        if isinstance(e, (set, frozenset)):
            return _OK if e == a else self.mismatch(path, e, a)
        np = sys.modules.get("numpy")
        if np is not None and isinstance(e, np.ndarray):
            return self._arrays(np, e, a, path)
        if np is not None and isinstance(e, np.generic):
            if np.issubdtype(e.dtype, np.inexact):
                return self.compare(complex(e), complex(a), path)
            return self._fallback(e, a, path)
        if isinstance(e, _IDENTITY_TYPES):
            return self._fallback(e, a, path)
        if isinstance(e, BaseException):
            res = self.compare(e.args, a.args, f"{path}.args")
            if not res[0]:
                return res
        if _is_python_class(type(e)):
            res = self._fields(e, a, path)
            if not res[0] or not _has_builtin_state(type(e)):
                return res
        if isinstance(e, BaseException):
            return _OK  # same type and args; builtin exceptions compare by identity
        res = self._fallback(e, a, path)
        if not res[0] and type(e).__eq__ is object.__eq__:
            return self._pickled_state(e, a, path)
        return res

    def _sequences(self, e: list | tuple, a: list | tuple, path: str) -> Result:
        for i, (x, y) in enumerate(zip(e, a, strict=False)):
            res = self.compare(x, y, f"{path}[{i}]")
            if not res[0]:
                return res
        if len(e) != len(a):
            return False, path, f"len {len(e)}: {short_repr(e)}", f"len {len(a)}: {short_repr(a)}"
        return _OK

    def _dicts(self, e: dict, a: dict, path: str) -> Result:
        for k in e:
            if k not in a:
                return self.mismatch(f"{path}[{short_repr(k)}]", e[k], _MISSING)
        for k in a:
            if k not in e:
                return self.mismatch(f"{path}[{short_repr(k)}]", _MISSING, a[k])
        for k, v in e.items():
            res = self.compare(v, a[k], f"{path}[{short_repr(k)}]")
            if not res[0]:
                return res
        return _OK

    def _fields(self, e: Any, a: Any, path: str) -> Result:
        """Same type, so compare state: ``__dict__`` plus any ``__slots__``."""
        fields: dict[str, tuple[Any, Any]] = {}
        ed, ad = _state(e, "__dict__"), _state(a, "__dict__")
        if isinstance(ed, dict) and isinstance(ad, dict):
            for k in ed.keys() | ad.keys():
                fields[k] = (ed.get(k, _MISSING), ad.get(k, _MISSING))
        for name in _slot_names(type(e)):
            fields[name] = (_state(e, name), _state(a, name))
        for name in sorted(fields, key=str):
            x, y = fields[name]
            if x is _MISSING or y is _MISSING:
                if x is not y:
                    return self.mismatch(f"{path}.{name}", x, y)
                continue
            res = self.compare(x, y, f"{path}.{name}")
            if not res[0]:
                return res
        return _OK

    def _arrays(self, np: Any, e: Any, a: Any, path: str) -> Result:
        if e.shape != a.shape:
            return False, path, f"shape {e.shape}", f"shape {a.shape}"
        if e.dtype != a.dtype:
            return False, path, f"dtype {e.dtype}", f"dtype {a.dtype}"
        try:
            if np.issubdtype(e.dtype, np.inexact):
                same = np.isclose(e, a, rtol=REL_TOL, atol=ABS_TOL, equal_nan=True)
            elif e.dtype == object:
                return self._sequences(e.tolist(), a.tolist(), path)
            else:
                same = e == a
            bad = np.argwhere(~np.asarray(same, dtype=bool))
        except Exception:
            return self._fallback(e, a, path)
        if len(bad) == 0:
            return _OK
        idx = tuple(int(i) for i in bad[0])
        where = f"{path}[{', '.join(map(str, idx))}]" if idx else path
        return self.mismatch(where, e[idx], a[idx])

    def _fallback(self, e: Any, a: Any, path: str) -> Result:
        try:
            ok = bool(e == a)
        except Exception:
            equals = getattr(e, "equals", None)  # pandas and friends
            try:
                ok = bool(equals(a)) if callable(equals) else False
            except Exception:
                ok = False
        return _OK if ok else self.mismatch(path, e, a)

    def _pickled_state(self, e: Any, a: Any, path: str) -> Result:
        """No ``__eq__`` beyond identity (``random.Random``, a numpy ``Generator``):
        compare what pickle would save, which is the state the copy was rebuilt from."""
        try:
            same = self.compare(_reduced(e), _reduced(a), path)[0]
        except RecursionError:
            raise
        except Exception:
            same = False
        return _OK if same else self.mismatch(path, e, a)


def _reduced(obj: Any) -> tuple[Any, ...]:
    """``__reduce_ex__`` with its item iterators drained, so they compare by content."""
    parts = obj.__reduce_ex__(5)
    if not isinstance(parts, tuple):  # pickled by name: identity was the answer
        raise TypeError("pickled as a global")
    parts += (None,) * (5 - len(parts))
    items = tuple(None if it is None else list(it) for it in parts[3:5])
    return (*parts[:3], *items, *parts[5:])


def _typed(obj: Any) -> str:
    text = f"{type(obj).__qualname__}: {short_repr(obj)}"
    return text if len(text) <= MAX_REPR else text[: MAX_REPR - 3] + "..."
