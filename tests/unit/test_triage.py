"""Skip rules, heuristic scores, LLM refinement and preselection."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from netzero.events import ImportProbe, TriageItem
from netzero.pipeline import triage
from netzero.pipeline.discovery import DiscoveredFunction, discover
from netzero.pipeline.triage import LlmRating

SOURCE = {
    "pkg/__init__.py": "",
    "pkg/core.py": """
        import random
        import time
        from functools import lru_cache, cached_property
        from typing import overload

        COUNTER = 0


        def dedupe(items):
            out = []
            for x in items:
                if x not in out:
                    out.append(x)
            return out


        def pairs(xs, target):
            hits = []
            for i in range(len(xs)):
                for j in range(len(xs)):
                    if i != j and xs[i] + xs[j] == target:
                        hits.append((i, j))
            return hits


        def tiny(x):
            return x + 1


        def reads(path):
            with open(path) as f:
                data = f.read()
            lines = data.splitlines()
            return [line.strip() for line in lines]


        def jitter(xs):
            out = []
            for x in xs:
                out.append(x + random.random())
            return out


        def stamp(xs):
            now = time.time()
            out = [x for x in xs]
            out.append(now)
            return out


        def bump(n):
            global COUNTER
            for _ in range(n):
                COUNTER += 1
            return COUNTER


        def gen(xs):
            for x in xs:
                if x:
                    yield x
            return None


        async def fetch(xs):
            out = []
            for x in xs:
                out.append(x)
            return out


        def outer(xs):
            def inner(y):
                total = 0
                for v in y:
                    total += v
                return total
            return inner(xs)


        @lru_cache
        def cached(n):
            total = 0
            for i in range(n):
                total += i
            return total


        def no_args():
            out = []
            for i in range(10):
                out.append(i)
            return out


        def no_return(xs):
            out = []
            for x in xs:
                out.append(x)
            out.sort()


        class Box:
            def __init__(self, items):
                self.items = list(items)
                self.n = len(self.items)
                self.ok = True

            @cached_property
            def total(self):
                t = 0
                for x in self.items:
                    t += x
                return t

            def find(self, x):
                for i, v in enumerate(self.items):
                    if v == x:
                        return i
                return -1


        def dup(xs):
            return [x for x in xs]


        def dup(xs):
            out = []
            for x in xs:
                out.append(x)
            return out
        """,
    "pkg/broken.py": """
        import does_not_exist

        def walk(xs):
            out = []
            for x in xs:
                out.append(x * 2)
            return out
        """,
    "pkg/fast.py": """
        def kernel(xs):
            out = []
            for x in xs:
                out.append(x * 2)
            return out
        """,
}


@pytest.fixture(scope="module")
def functions(tmp_path_factory: pytest.TempPathFactory) -> dict[str, DiscoveredFunction]:
    root = tmp_path_factory.mktemp("repo")
    for rel, body in SOURCE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    found = discover(Path(root))
    return {fn.function_id: fn for fn in triage.unique_functions(found.functions)}


PROBE = ImportProbe(
    ok_modules=["pkg", "pkg.core"],
    failed={
        "pkg.broken": "ModuleNotFoundError: No module named 'does_not_exist'",
        "pkg.fast": "a compiled extension shadows the source (/x/fast.so)",
    },
)


@pytest.mark.parametrize(
    ("fid", "reason"),
    [
        ("pkg.core:dedupe", None),
        ("pkg.core:pairs", None),
        ("pkg.core:Box.find", None),
        ("pkg.core:tiny", "too short (2 lines)"),
        ("pkg.core:reads", "does I/O (open)"),
        ("pkg.core:jitter", "nondeterministic (random.random)"),
        ("pkg.core:stamp", "nondeterministic (time.time)"),
        ("pkg.core:bump", "writes global or nonlocal state"),
        ("pkg.core:gen", "generator"),
        ("pkg.core:fetch", "async function"),
        ("pkg.core:Box.__init__", "dunder method"),
        ("pkg.core:Box.total", "@cached_property"),
        ("pkg.broken:walk", "module does not import: ModuleNotFoundError: No module named"),
        ("pkg.fast:kernel", "compiled extension module"),
    ],
)
def test_skip_reason(functions: dict[str, DiscoveredFunction], fid: str, reason: str | None):
    got = triage.skip_reason(functions[fid], PROBE)
    if reason is None:
        assert got is None
    else:
        assert got is not None and got.startswith(reason)


def test_nested_functions_are_skipped(functions: dict[str, DiscoveredFunction]):
    nested = [fn for fn in functions.values() if fn.nested_in_function]
    assert [fn.function_id for fn in nested] == ["pkg.core:outer.inner"]
    for fn in nested:
        assert triage.skip_reason(fn) == "nested function"


def test_skip_rules_without_a_probe(functions: dict[str, DiscoveredFunction]):
    assert triage.skip_reason(functions["pkg.broken:walk"]) is None


def test_later_definition_wins(functions: dict[str, DiscoveredFunction]):
    assert "pkg.core:dup" in functions
    assert functions["pkg.core:dup"].features.n_loops == 1


def test_heuristic_prefers_nested_loops_and_anti_patterns(
    functions: dict[str, DiscoveredFunction],
):
    pairs, reasons = triage.heuristic(functions["pkg.core:pairs"])
    dedupe, dedupe_reasons = triage.heuristic(functions["pkg.core:dedupe"])
    kernel, _ = triage.heuristic(functions["pkg.fast:kernel"])
    assert 0 < kernel < dedupe and kernel < pairs
    assert "loops nested 2 deep" in reasons
    assert "membership test on a list inside a loop" in dedupe_reasons
    for fn in functions.values():
        score, _ = triage.heuristic(fn)
        assert 0.0 <= score <= 1.0


def test_heuristic_penalties(functions: dict[str, DiscoveredFunction]):
    no_args, r1 = triage.heuristic(functions["pkg.core:no_args"])
    no_return, r2 = triage.heuristic(functions["pkg.core:no_return"])
    kernel, _ = triage.heuristic(functions["pkg.fast:kernel"])
    assert "takes no arguments" in r1 and no_args < kernel
    assert "returns nothing" in r2


def test_heuristic_items_rank_eligible_first(functions: dict[str, DiscoveredFunction]):
    items = triage.heuristic_items(list(functions.values()), PROBE, max_items=50)
    eligible = [it for it in items if not it.skip_reason]
    skipped = [it for it in items if it.skip_reason]
    assert items == eligible + skipped
    assert [it.score for it in eligible] == sorted((it.score for it in eligible), reverse=True)
    assert len({it.function_id for it in items}) == len(items)
    assert all(it.score == it.heuristic_score for it in items)
    assert all(it.llm_potential is None and not it.preselected for it in items)


def test_heuristic_items_caps_each_group(functions: dict[str, DiscoveredFunction]):
    items = triage.heuristic_items(list(functions.values()), PROBE, max_items=2)
    assert len([it for it in items if not it.skip_reason]) == 2
    assert len([it for it in items if it.skip_reason]) == 2


def item(fid: str, score: float, *, skip: str | None = None, h: float | None = None) -> TriageItem:
    module, _, qual = fid.partition(":")
    return TriageItem(
        function_id=fid,
        module=module,
        qualname=qual,
        kind="function",
        file=f"{module.replace('.', '/')}.py",
        line=1,
        end_line=10,
        loc=10,
        import_line=f"from {module} import {qual}",
        call_hint=f"{qual}(...)",
        heuristic_score=score if h is None else h,
        score=score,
        reasons=[],
        skip_reason=skip,
    )


def test_rank_ties_break_by_heuristic_then_id():
    items = [item("m:b", 0.5, h=0.2), item("m:a", 0.5, h=0.2), item("m:c", 0.5, h=0.4)]
    assert [it.function_id for it in triage.rank(items, max_items=9)] == ["m:c", "m:a", "m:b"]


def test_apply_ratings_blends_and_resorts():
    items = [item("m:a", 0.6), item("m:b", 0.4), item("m:s", 0.9, skip="generator")]
    ratings = {
        "m:a": LlmRating("low", "low"),
        "m:b": LlmRating("high", "high", "quadratic scan"),
        "m:s": LlmRating("high", "high"),
        "m:unknown": LlmRating("high", "high"),
    }
    out = triage.apply_ratings(items, ratings, max_items=9)
    assert [it.function_id for it in out] == ["m:b", "m:a", "m:s"]
    b, a, s = out
    assert b.score == round(0.5 * 0.4 + 0.5 * 1.0, 4)
    assert a.score == round(0.5 * 0.6 + 0.5 * 0.0625, 4)
    assert b.llm_potential == "high" and b.reasons == ["quadratic scan"]
    assert s.score == 0.9 and s.llm_potential is None  # skipped items are not re-rated


def test_apply_ratings_without_ratings_is_a_rerank():
    items = [item("m:a", 0.2), item("m:b", 0.7)]
    out = triage.apply_ratings(items, {}, max_items=9)
    assert [it.function_id for it in out] == ["m:b", "m:a"]
    assert out[0] == items[1]


def test_preselect_takes_top_n_above_threshold():
    items = triage.rank(
        [
            item("m:a", 0.9),
            item("m:b", 0.5),
            item("m:c", 0.1),
            item("m:s", 0.95, skip="generator"),
        ],
        max_items=9,
    )
    out = triage.preselect(items, n=8, min_score=0.15)
    assert {it.function_id for it in out if it.preselected} == {"m:a", "m:b"}
    out = triage.preselect(items, n=1, min_score=0.15)
    assert [it.function_id for it in out if it.preselected] == ["m:a"]
    assert [it.function_id for it in out] == [it.function_id for it in items]


def test_preselect_clears_old_marks():
    items = [item("m:a", 0.9).model_copy(update={"preselected": True})]
    assert not triage.preselect(items, n=0, min_score=0.0)[0].preselected


def test_llm_shortlist_only_eligible(functions: dict[str, DiscoveredFunction]):
    fns = list(functions.values())
    items = triage.heuristic_items(fns, PROBE, max_items=50)
    short = triage.llm_shortlist(items, fns, 3)
    assert len(short) == 3
    assert [fn.function_id for fn in short] == [it.function_id for it in items[:3]]
    assert all(triage.skip_reason(fn, PROBE) is None for fn in short)
