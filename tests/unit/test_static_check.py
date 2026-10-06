"""Static checks on generated test files and candidate rewrites."""

from __future__ import annotations

import textwrap

import pytest

from netzero.pipeline.discovery import DiscoveredFunction, StaticFeatures
from netzero.pipeline.static_check import (
    candidate_diff,
    check_candidate,
    strip_fences,
    validate_test_file,
)

MODULE = textwrap.dedent(
    '''\
    """Helpers."""

    import math
    from collections import OrderedDict


    class Bag:
        def __init__(self, items):
            self.items = list(items)

        def unique(self):
            out = []
            for x in self.items:
                if x not in out:
                    out.append(x)
            return out


    def dedupe(items, *, key=None):
        """Drop repeats, keep order."""
        out = []
        for x in items:
            if x not in out:
                out.append(x)
        return out
    '''
)
ORIGINAL = MODULE[MODULE.index("def dedupe") :]


def make_fn(qualname: str = "dedupe") -> DiscoveredFunction:
    name = qualname.rsplit(".", 1)[-1]
    return DiscoveredFunction(
        function_id=f"pkg.mod:{qualname}",
        module="pkg.mod",
        qualname=qualname,
        name=name,
        kind="method" if "." in qualname else "function",
        file="pkg/mod.py",
        line=1,
        end_line=2,
        def_line=1,
        source=ORIGINAL,
        import_line=f"from pkg.mod import {qualname.split('.')[0]}",
        call_hint="",
        import_root=".",
        class_path=qualname.split(".")[:-1],
        nested_in_function=False,
        features=StaticFeatures(loc=6, n_args=1),
    )


def d(code: str) -> str:
    return textwrap.dedent(code).lstrip("\n")


GOOD_TESTS = d(
    """
    import random

    import pytest

    from pkg.mod import dedupe


    def test_empty():
        assert dedupe([]) == []


    def test_keeps_first_occurrence():
        assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]


    @pytest.mark.nz_workload
    def test_workload():
        rng = random.Random(42)
        items = [rng.randrange(50) for _ in range(2000)]
        assert len(dedupe(items)) <= 50
    """
)


# -- test files -------------------------------------------------------------------


def test_good_test_file_passes_unchanged() -> None:
    check = validate_test_file(GOOD_TESTS, make_fn())
    assert check.ok, check.problems
    assert check.code == GOOD_TESTS
    assert check.test_names == ["test_empty", "test_keeps_first_occurrence", "test_workload"]
    assert check.workload_test == "test_workload"


def test_fences_are_stripped() -> None:
    check = validate_test_file(f"```python\n{GOOD_TESTS}```\n", make_fn())
    assert check.ok and check.code == GOOD_TESTS


@pytest.mark.parametrize(
    ("text", "code"),
    [("```py\nx = 1\n```", "x = 1"), ("```\nx = 1```", "x = 1"), ("x = 1\n", "x = 1\n")],
)
def test_strip_fences(text: str, code: str) -> None:
    assert strip_fences(text) == code


def test_missing_imports_are_added_after_the_docstring_and_imports() -> None:
    code = (
        '"""Tests."""\nimport random\n\n' + GOOD_TESTS.split("from pkg.mod import dedupe\n", 1)[1]
    )
    check = validate_test_file(code, make_fn())
    assert check.ok, check.problems
    assert check.code.startswith(
        '"""Tests."""\nimport random\nimport pytest\nfrom pkg.mod import dedupe\n\n'
    )


def test_an_import_line_covered_by_a_wider_import_is_not_repeated() -> None:
    code = GOOD_TESTS.replace("from pkg.mod import dedupe", "from pkg.mod import Bag, dedupe")
    assert validate_test_file(code, make_fn()).code == code


def test_too_few_tests() -> None:
    code = GOOD_TESTS.replace("def test_empty", "def _empty")
    assert validate_test_file(code, make_fn()).problems == ["only 2 tests; write at least 3"]


def test_exactly_one_workload_test() -> None:
    none = validate_test_file(GOOD_TESTS.replace("@pytest.mark.nz_workload\n", ""), make_fn())
    assert none.workload_test is None
    assert any("no test is marked" in p for p in none.problems)
    two = GOOD_TESTS.replace("def test_empty", "@pytest.mark.nz_workload\ndef test_empty")
    check = validate_test_file(two, make_fn())
    assert check.workload_test is None
    assert check.problems == [
        "2 tests are marked @pytest.mark.nz_workload (test_empty, test_workload); mark exactly one"
    ]


def test_class_tests_and_called_marks_count() -> None:
    code = d(
        """
        import pytest

        from pkg.mod import dedupe


        def helper():
            return [1, 1]


        class TestDedupe:
            def test_a(self):
                assert dedupe(helper()) == [1]

            def test_b(self):
                assert dedupe([]) == []

            @pytest.mark.nz_workload()
            def test_big(self):
                assert dedupe(list(range(100)) * 2) == list(range(100))

            def setup_method(self):
                pass


        class Helper:
            def test_not_collected(self):
                pass
        """
    )
    check = validate_test_file(code, make_fn())
    assert check.ok, check.problems
    assert check.test_names == ["TestDedupe::test_a", "TestDedupe::test_b", "TestDedupe::test_big"]
    assert check.workload_test == "TestDedupe::test_big"


METHOD_TESTS = (
    GOOD_TESTS.replace("import dedupe", "import Bag")
    .replace("dedupe(", "Bag(")
    .replace("== []", ".unique() == []")
)


def test_a_method_is_used_through_its_class() -> None:
    assert validate_test_file(METHOD_TESTS, make_fn("Bag.unique")).ok


def test_redefining_the_class_of_a_method_is_rejected() -> None:
    fake = "\n\nclass Bag:\n    def __init__(self, items):\n        pass\n\n    def unique(self):\n"
    code = METHOD_TESTS.replace(
        "\n\ndef test_empty", fake + "        return []\n\n\ndef test_empty"
    )
    problems = validate_test_file(code, make_fn("Bag.unique")).problems
    assert problems == ["the file defines its own Bag; import the real one instead"]


def test_a_helper_named_like_the_method_is_fine() -> None:
    code = METHOD_TESTS + "\n\ndef unique(xs):\n    return list(dict.fromkeys(xs))\n"
    assert validate_test_file(code, make_fn("Bag.unique")).ok


def test_redefining_the_function_is_rejected() -> None:
    code = GOOD_TESTS.replace(
        "\n\ndef test_empty", "\n\ndef dedupe(items):\n    return items\n\n\ndef test_empty"
    )
    problems = validate_test_file(code, make_fn()).problems
    assert problems == ["the file defines its own dedupe; import the real one instead"]


@pytest.mark.parametrize(
    "line",
    [
        "dedupe = lambda items: list(dict.fromkeys(items))",
        "dedupe: object = sorted",
        "dedupe, other = sorted, 1",
    ],
)
def test_rebinding_the_function_is_rejected(line: str) -> None:
    code = GOOD_TESTS.replace("\n\ndef test_empty", f"\n{line}\n\n\ndef test_empty", 1)
    problems = validate_test_file(code, make_fn()).problems
    assert problems == ["the file defines its own dedupe; import the real one instead"]


def test_the_function_must_be_used() -> None:
    code = GOOD_TESTS.replace("dedupe(", "sorted(").replace("from pkg.mod import dedupe", "")
    check = validate_test_file(code, make_fn())
    assert "no test uses dedupe" in check.problems
    assert "from pkg.mod import dedupe" in check.code  # re-added all the same


@pytest.mark.parametrize(
    ("extra", "problem"),
    [
        ("import socket", "imports socket;"),
        ("import subprocess as sp", "imports subprocess;"),
        ("from urllib.request import urlopen", "imports urllib.request;"),
        ("def test_io():\n    open('x').read()", "calls open;"),
        ("from os import remove as rm\n\ndef test_rm():\n    rm('x')", "calls os.remove;"),
        ("import os\n\ndef test_sh():\n    os.system('ls')", "calls os.system;"),
        ("def test_ev():\n    eval('1')", "calls eval;"),
        ("from unittest import mock", "uses mocking"),
        ("from unittest.mock import patch", "uses mocking"),
        ("import mock", "uses mocking"),
        (
            "import unittest\n\ndef test_m():\n    with unittest.mock.patch('a.b'):\n        pass",
            "uses mocking",
        ),
        ("def test_mp(monkeypatch):\n    monkeypatch.setattr('a.b', 1)", "uses mocking"),
        ("def test_mk(mocker):\n    mocker.patch('a.b')", "uses mocking"),
    ],
)
def test_forbidden_things_in_tests(extra: str, problem: str) -> None:
    check = validate_test_file(GOOD_TESTS + "\n\n" + extra + "\n", make_fn())
    assert [p for p in check.problems if p.startswith(problem)], check.problems


def test_seeded_random_and_math_are_fine() -> None:
    code = GOOD_TESTS + "\n\ndef test_math():\n    import math\n    assert math.isclose(1, 1)\n"
    assert validate_test_file(code, make_fn()).ok


def test_syntax_error() -> None:
    check = validate_test_file("def test_a(:\n    pass\n", make_fn())
    assert check.problems == ["syntax error on line 1: invalid syntax"]
    assert check.test_names == [] and check.workload_test is None


# -- candidates -------------------------------------------------------------------

FAST = d(
    '''
    def dedupe(items, *, key=None):
        """Drop repeats, keep order."""
        return list(dict.fromkeys(items))
    '''
)


def problems(code: str, **kwargs) -> list[str]:
    return check_candidate(MODULE, code, make_fn(), **kwargs).static.problems


def test_good_candidate() -> None:
    check = check_candidate(MODULE, FAST, make_fn(), module_source=MODULE)
    assert check.static.ok, check.static.problems
    assert check.import_modules == []
    assert check.code == FAST


def test_original_may_be_just_the_function() -> None:
    assert check_candidate(ORIGINAL, FAST, make_fn()).static.ok


def test_fenced_and_indented_candidates_are_normalized() -> None:
    check = check_candidate(
        MODULE, "```python\n" + textwrap.indent(FAST, "    ") + "```", make_fn()
    )
    assert check.static.ok and check.code == FAST


def test_method_candidate() -> None:
    method = "    def unique(self):\n        return list(dict.fromkeys(self.items))\n"
    check = check_candidate(MODULE, method, make_fn("Bag.unique"), module_source=MODULE)
    assert check.static.ok, check.static.problems
    assert check.code.startswith("def unique(self):")
    renamed = check_candidate(MODULE, method.replace("self)", "me)"), make_fn("Bag.unique"))
    assert any("signature" in p for p in renamed.static.problems)


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("import itertools\n\n" + FAST, "code must be exactly one function definition"),
        (FAST + "\n\ndef helper():\n    pass\n", "code must be exactly one function definition"),
        ("x = 1\n", "code must be exactly one function definition"),
        (FAST.replace("def dedupe", "def dedupe_fast"), "renamed dedupe to dedupe_fast"),
        (FAST.replace("def dedupe", "async def dedupe"), "changed whether the function is async"),
        (
            FAST.replace(", *, key", ", key"),
            "changed the signature from (items, *, key=None) to (items, key=None)",
        ),
        (FAST.replace("items, *", "items: list, *"), "changed the signature"),
        (FAST.replace("key=None)", "key=None) -> list"), "changed the signature"),
        (FAST.replace("key=None", "key=0"), "changed the signature"),
        ("import functools\n@functools.cache\n" + FAST, "changed the decorators"),
        (
            FAST.replace('"""\n', '"""\n    global SEEN\n'),
            "uses global or nonlocal",
        ),
        (FAST.replace("return", "print(items)\n    return"), "calls print, which the original"),
        (FAST.replace("return", "open('log')\n    return"), "calls open, which the original"),
        (FAST.replace("return", "import subprocess\n    return"), "imports subprocess;"),
        (FAST.replace("return", "import os\n    os.remove('x')\n    return"), "calls os.remove"),
        (ORIGINAL.replace('    """Drop repeats, keep order."""\n', ""), "the code is identical"),
        (ORIGINAL.replace("keep order.", "keep the order."), "the code is identical"),
        ("def dedupe(items:\n", "syntax error on line 1"),
    ],
)
def test_candidate_problems(code: str, problem: str) -> None:
    found = problems(code)
    assert [p for p in found if p.startswith(problem)], found


def test_io_the_original_had_is_allowed() -> None:
    original = ORIGINAL.replace("out = []", "print(len(items))\n    out = []")
    code = FAST.replace("return", "print(len(items))\n    return")
    assert check_candidate(original, code, make_fn()).static.ok


@pytest.mark.parametrize(
    ("header", "call", "problem"),
    [
        ("import subprocess", "subprocess.run(['ls'])", "calls subprocess.run, which"),
        ("import subprocess as sp", "sp.run(['ls'])", "calls subprocess.run, which"),
        ("from os import remove as rm", "rm('x')", "calls os.remove, which"),
        ("import os", "os.system('ls')", "calls os.system, which"),
    ],
)
def test_io_through_the_modules_own_imports_is_caught(header: str, call: str, problem: str) -> None:
    module = f"{header}\n{MODULE}"
    code = FAST.replace("return", f"{call}\n    return")
    found = problems(code, module_source=module)
    assert [p for p in found if p.startswith(problem)], found


def test_io_through_module_imports_the_original_had_is_allowed() -> None:
    original = d(
        """
        from os import remove as rm


        def dedupe(items, *, key=None):
            rm("cache")
            return items
        """
    )
    code = "def dedupe(items, *, key=None):\n    rm('cache')\n    return list(set(items))\n"
    check = check_candidate(original, code, make_fn(), module_source=original)
    assert check.static.ok, check.static.problems


def test_type_parameters_are_bound_names() -> None:
    module = "import typing\n\n\ndef dedupe(items, *, key=None):\n    return items\n"
    code = d(
        """
        def dedupe[T](items, *, key=None):
            out: list[T] = typing.cast(list[T], list(dict.fromkeys(items)))
            return out
        """
    )
    assert check_candidate(module, code, make_fn(), module_source=module).static.ok


def test_missing_original() -> None:
    check = check_candidate("def other():\n    pass\n", FAST, make_fn())
    assert check.static.problems == ["could not find the original dedupe to compare against"]


@pytest.mark.parametrize(
    "line", ["from . import helpers", "from os import *", "x = 1", "import a; import b", "import"]
)
def test_new_imports_must_be_single_absolute_imports(line: str) -> None:
    found = problems(FAST, new_imports=[line])
    assert found == [f"new_imports entry {line!r} is not one absolute import statement"]


def test_imports_must_be_stdlib_or_already_in_the_module() -> None:
    code = FAST.replace("return list(dict.fromkeys(items))", "return np.unique(items).tolist()")
    found = problems(code, new_imports=["import numpy as np"], module_source=MODULE)
    assert found == ["imports numpy, which is neither stdlib nor imported by the module"]
    with_numpy = "import numpy as np\n" + MODULE
    assert problems(code, new_imports=["import numpy as np"], module_source=with_numpy) == []


def test_undefined_names_need_new_imports() -> None:
    code = FAST.replace("dict.fromkeys(items)", "Counter(items)")
    assert problems(code, module_source=MODULE) == [
        "uses undefined names Counter; import them via new_imports"
    ]
    fixed = problems(code, new_imports=["from collections import Counter"], module_source=MODULE)
    assert fixed == []
    # names the module already binds are fine, and so is everything bound locally
    local = d(
        """
        def dedupe(items, *, key=None):
            seen = OrderedDict()
            for i, x in enumerate(items):
                seen.setdefault(x, i)
            try:
                return [k for k in seen if math.isfinite(1.0)]
            except ValueError as err:
                raise RuntimeError(Bag) from err
        """
    )
    assert problems(local, module_source=MODULE) == []
    # without module_source names are not checked
    assert problems(code) == []


def test_star_imports_silence_the_undefined_name_check() -> None:
    code = FAST.replace("dict.fromkeys(items)", "Counter(items)")
    assert problems(code, module_source="from collections import *\n" + MODULE) == []


def test_import_modules_are_ordered_and_unique() -> None:
    code = FAST.replace(
        "return", "import heapq\n    import itertools.chain as _c\n    import heapq\n    return"
    )
    check = check_candidate(
        MODULE, code, make_fn(), new_imports=["import heapq", "from collections import Counter"]
    )
    assert check.import_modules == ["heapq", "collections", "itertools.chain"]


# -- diffs ------------------------------------------------------------------------


def test_candidate_diff() -> None:
    diff = candidate_diff(ORIGINAL, FAST, "pkg/mod.py")
    assert diff.startswith(
        "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ "
    )
    assert "-    out = []\n" in diff
    assert "+    return list(dict.fromkeys(items))\n" in diff
    assert ' def dedupe(items, *, key=None):\n     """Drop repeats, keep order."""\n' in diff


def test_candidate_diff_is_empty_when_nothing_changed() -> None:
    assert candidate_diff(FAST, FAST, "pkg/mod.py") == ""
    assert candidate_diff(FAST.rstrip("\n"), FAST, "pkg/mod.py") == ""
