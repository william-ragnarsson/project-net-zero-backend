"""``nz_equal``, the equality the differential check uses."""

from __future__ import annotations

import collections
import fractions
import io
import math
import random
import zlib
from dataclasses import dataclass
from typing import Generic, NamedTuple

import pytest

from netzero.sandbox.harness.equality import MAX_REPR, nz_equal, short_repr


def ok(e, a) -> bool:
    return nz_equal(e, a)[0]


@dataclass
class Point:
    x: float
    y: float


class Plain:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Slotted:
    __slots__ = ("a", "b")

    def __init__(self, a, b=None):
        self.a = a
        if b is not None:
            self.b = b


class SlottedChild(Slotted):
    __slots__ = ("c",)

    def __init__(self, a, c):
        super().__init__(a)
        self.c = c


class AlwaysEqual:
    """``__eq__`` says yes, but the fields differ: fields win."""

    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return True

    __hash__ = None  # type: ignore[assignment]


class Tagged(collections.deque):
    def __init__(self, items, tag):
        super().__init__(items)
        self.tag = tag


class Pair(NamedTuple):
    a: int
    b: int


class AppError(Exception):
    pass


# --- exact types -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("e", "a"),
    [(1, True), (1, 1.0), (0, False), ([1], (1,)), ({"a": 1}, collections.OrderedDict(a=1))],
)
def test_types_must_match_exactly(e, a):
    res = nz_equal(e, a)
    assert res[0] is False
    assert res[1] == ""
    assert res[2].startswith(type(e).__qualname__ + ": ")
    assert res[3].startswith(type(a).__qualname__ + ": ")


def test_namedtuple_is_not_a_tuple():
    assert ok(Pair(1, 2), Pair(1, 2))
    assert not ok(Pair(1, 2), (1, 2))


def test_equal_scalars_and_identity():
    assert nz_equal(3, 3) == (True, "", "", "")
    assert ok("x", "x")
    assert ok(b"x", b"x")
    assert ok(None, None)
    assert not ok("x", "y")


# --- floats and complex -----------------------------------------------------------


def test_floats_within_tolerance():
    assert ok(0.1 + 0.2, 0.3)
    assert ok(1e10, 1e10 * (1 + 1e-10))
    assert ok(0.0, 1e-13)  # absolute tolerance near zero
    assert not ok(1.0, 1.0 + 1e-6)
    assert not ok(0.0, 1e-9)


def test_nan_and_inf():
    assert ok(math.nan, math.nan)
    assert not ok(math.nan, 1.0)
    assert not ok(1.0, math.nan)
    assert ok(math.inf, math.inf)
    assert not ok(math.inf, -math.inf)


def test_complex():
    assert ok(complex(0.1 + 0.2, 1), complex(0.3, 1))
    assert ok(complex(math.nan, 1), complex(math.nan, 1))
    assert not ok(1 + 2j, 1 + 2.001j)


# --- containers and paths ------------------------------------------------------


def test_list_elementwise_with_path():
    assert ok([1, [2.0, 3]], [1, [2.0 + 1e-12, 3]])
    assert nz_equal([1, [2, 3]], [1, [2, 4]]) == (False, "[1][1]", "3", "4")


def test_length_mismatch_after_common_prefix():
    ok_, path, e, a = nz_equal([1, 2, 3], [1, 2])
    assert (ok_, path) == (False, "")
    assert e.startswith("len 3") and a.startswith("len 2")
    # a differing element in the common prefix is reported first
    assert nz_equal([1, 2, 3], [1, 9])[1] == "[1]"


def test_dict_order_insensitive():
    assert ok({"a": 1, "b": 2}, {"b": 2, "a": 1})


def test_dict_missing_and_extra_keys():
    assert nz_equal({"a": 1, "b": 2}, {"a": 1}) == (False, "['b']", "2", "<missing>")
    assert nz_equal({"a": 1}, {"a": 1, "c": 3}) == (False, "['c']", "<missing>", "3")


def test_nested_path_through_dicts_lists_and_attrs():
    e = [0, 1, {"key": Plain(attr=[1, 2])}]
    a = [0, 1, {"key": Plain(attr=[1, 5])}]
    assert nz_equal(e, a) == (False, "[2]['key'].attr[1]", "2", "5")


def test_root_prefixes_the_path():
    assert nz_equal((1, [2]), (1, [3]), root="args")[1] == "args[1][0]"


def test_sets_use_equality():
    assert ok({1, 2, 3}, {3, 2, 1})
    assert ok(frozenset({1}), frozenset({1}))
    assert not ok({1, 2}, {1, 3})
    assert not ok({1}, frozenset({1}))


# --- objects -------------------------------------------------------------------


def test_dataclass_fields():
    assert ok(Point(1.0, 2.0), Point(1.0, 2.0 + 1e-12))
    assert nz_equal(Point(1.0, 2.0), Point(1.0, 3.0)) == (False, ".y", "2.0", "3.0")


def test_plain_object_fields():
    assert ok(Plain(a=1, b=[1]), Plain(a=1, b=[1]))
    assert nz_equal(Plain(a=1), Plain(a=1, b=2)) == (False, ".b", "<missing>", "2")


def test_slots_including_inherited_and_unset():
    assert ok(Slotted(1, 2), Slotted(1, 2))
    assert nz_equal(Slotted(1, 2), Slotted(1, 3))[1] == ".b"
    assert ok(Slotted(1), Slotted(1))  # b unset on both
    assert nz_equal(Slotted(1), Slotted(1, 2))[1:3] == (".b", "<missing>")
    assert nz_equal(SlottedChild(1, 2), SlottedChild(1, 3))[1] == ".c"
    assert nz_equal(SlottedChild(1, 2), SlottedChild(0, 2))[1] == ".a"


def test_fields_beat_a_lenient_eq():
    assert ok(AlwaysEqual(1), AlwaysEqual(1))
    assert nz_equal(AlwaysEqual(1), AlwaysEqual(2))[1] == ".v"


def test_builtin_base_needs_fields_and_eq():
    assert ok(Tagged([1, 2], "t"), Tagged([1, 2], "t"))
    assert nz_equal(Tagged([1], "t"), Tagged([1], "u"))[1] == ".tag"
    assert not ok(Tagged([1, 2], "t"), Tagged([1, 3], "t"))


def test_builtin_object_falls_back_to_eq():
    assert ok(collections.deque([1, 2]), collections.deque([1, 2]))
    assert not ok(collections.deque([1, 2]), collections.deque([2, 1]))
    assert ok(range(3), range(3))
    assert ok(fractions.Fraction(1, 3), fractions.Fraction(2, 6))  # pure Python, slots
    assert not ok(fractions.Fraction(1, 3), fractions.Fraction(1, 4))


def test_exceptions_by_type_and_args():
    assert ok(ValueError("x", 1), ValueError("x", 1))
    assert nz_equal(ValueError("x"), ValueError("y"))[1] == ".args[0]"
    assert not ok(ValueError("x"), TypeError("x"))
    assert ok(AppError("x"), AppError("x"))
    err = AppError("x")
    err.code = 1  # type: ignore[attr-defined]
    other = AppError("x")
    other.code = 2  # type: ignore[attr-defined]
    assert nz_equal(err, other)[1] == ".code"


def test_functions_and_classes_by_identity():
    assert ok(len, len)
    assert ok(Point, Point)
    assert not ok(Point, Plain)
    assert not ok(lambda: 1, lambda: 1)


# --- cycles, depth, reprs ----------------------------------------------------------


def test_cycles_terminate():
    e: list = [1]
    e.append(e)
    a: list = [1]
    a.append(a)
    assert ok(e, a)
    b: list = [2]
    b.append(b)
    assert nz_equal(e, b)[1] == "[0]"


def test_object_cycle():
    e, a = Plain(v=1), Plain(v=1)
    e.me, a.me = e, a
    assert ok(e, a)


def test_too_deep_is_a_mismatch_not_a_crash():
    e: list = []
    a: list = []
    for _ in range(100_000):
        e, a = [e], [a]
    res = nz_equal(e, a)
    assert res[0] is False
    assert res[2] == "<too deeply nested to compare>"


def test_reprs_are_truncated():
    res = nz_equal("x" * 10_000, "y" * 10_000)
    assert not res[0]
    assert len(res[2]) <= MAX_REPR and len(res[3]) <= MAX_REPR
    res = nz_equal(list(range(1000)), tuple(range(1000)))
    assert len(res[2]) <= MAX_REPR and len(res[3]) <= MAX_REPR


def test_broken_repr_does_not_break_the_check():
    class Bad:
        def __repr__(self):
            raise RuntimeError("no")

    assert "Bad" in short_repr(Bad())
    assert nz_equal(Bad(), Bad())[0]  # same type, no fields: equal despite identity __eq__


# --- numpy -------------------------------------------------------------------------


def test_numpy_arrays():
    np = pytest.importorskip("numpy")
    assert ok(np.array([0.1 + 0.2, np.nan]), np.array([0.3, np.nan]))
    assert nz_equal(np.array([[1, 2], [3, 4]]), np.array([[1, 2], [3, 5]]))[1] == "[1, 1]"
    assert nz_equal(np.zeros(3), np.zeros(4))[2:] == ("shape (3,)", "shape (4,)")
    assert nz_equal(np.zeros(3), np.zeros(3, dtype=np.float32))[2:] == (
        "dtype float64",
        "dtype float32",
    )
    assert ok(np.array([1, "a"], dtype=object), np.array([1, "a"], dtype=object))
    assert not ok(np.array([1, "a"], dtype=object), np.array([1, "b"], dtype=object))
    assert not ok(np.array(1.0), np.array(2.0))


def test_numpy_scalars():
    np = pytest.importorskip("numpy")
    assert ok(np.float64(0.1 + 0.2), np.float64(0.3))
    assert not ok(np.float64(1.0), 1.0)  # exact type
    assert ok(np.int64(3), np.int64(3))
    assert not ok(np.int64(3), np.int64(4))


# --- extension types, identity-only objects, code that raises ----------------------


def test_extension_type_state_is_not_ignored():
    """PyO3 types are mutable heap types without ``__dict__`` or slots: not fields."""
    url = pytest.importorskip("pydantic_core").Url
    assert ok(url("http://a.com"), url("http://a.com"))
    assert not ok(url("http://a.com"), url("http://b.com"))


class Holder[T]:  # implicitly a ``Generic`` subclass
    def __init__(self, v):
        self.v = v


def test_a_stateless_base_keeps_field_comparison():
    assert Generic in Holder.__mro__
    assert ok(Holder(1), Holder(1))  # not ``==`` (identity) because of ``Generic``
    assert nz_equal(Holder(1), Holder(2))[1] == ".v"


def test_random_state_is_compared():
    e, a = random.Random(1), random.Random(1)
    assert ok(e, a)
    a.random()
    assert not ok(e, a)
    e.random()
    assert ok(e, a)


def test_identity_only_objects_compare_by_pickled_state():
    assert ok(object(), object())  # a sentinel argument is not a mutation
    assert ok(io.BytesIO(b"a"), io.BytesIO(b"a"))
    assert not ok(io.BytesIO(b"a"), io.BytesIO(b"b"))
    assert ok(iter([1, 2]), iter([1, 2]))
    assert not ok(iter([1, 2]), iter([1, 3]))
    assert not ok(zlib.compressobj(), zlib.compressobj())  # no state to pickle: identity


def test_numpy_generator_state_is_compared():
    np = pytest.importorskip("numpy")
    e, a = np.random.default_rng(1), np.random.default_rng(1)
    assert ok(e, a)
    a.random()
    assert not ok(e, a)


class GetattrRaises:
    __slots__ = ("a",)

    def __getattr__(self, name):
        raise KeyError(name)


def test_a_raising_getattr_is_not_state():
    e, a = GetattrRaises(), GetattrRaises()
    assert ok(e, a)  # unset on both
    e.a = 1
    assert nz_equal(e, a)[1:] == (".a", "1", "<missing>")


class Touchy:
    def __hash__(self):
        return 0

    def __eq__(self, other):
        raise RuntimeError("no")


def test_the_objects_own_code_raising_is_a_mismatch_not_a_crash():
    res = nz_equal({Touchy(): 1}, {Touchy(): 1}, root="args")
    assert res[:3] == (False, "args", "<cannot compare: RuntimeError>")
