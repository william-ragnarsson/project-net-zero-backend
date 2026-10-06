"""Rank discovered functions by how likely a greener rewrite is, and skip the untestable.

Two passes, so the UI can show a list early and re-sort it later:

1. ``heuristic_items``: static rules only, right after discovery. Every
   function gets a ``heuristic_score`` in [0, 1] and, if it cannot be
   optimized safely, a ``skip_reason`` (the 11 rules below).
2. ``refine``: one LLM call rates the top eligible functions for potential and
   testability; ``score = 0.5·heuristic + 0.5·potential·testability``. Without
   an LLM (or if the call fails) the heuristic score stands.

Skip rules (discovery already drops test files, excluded directories and code
under ``if __name__ == "__main__"``, rule 1):

2. nested functions        3. async functions, generators   4. dunder methods
5. property/abstract/overload                               6. too short or too long
7. I/O                     8. nondeterminism                9. global/nonlocal writes
10. module failed the import probe                          11. compiled extension module
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from netzero.events import ImportProbe, Rating, TriageItem
from netzero.pipeline.discovery import DiscoveredFunction

MIN_LOC = 4
MAX_LOC = 150
SKIP_DECORATORS = {
    "property",
    "cached_property",
    "abstractmethod",
    "abstractproperty",
    "overload",
    "setter",
    "getter",
    "deleter",
}
RATING_VALUE: dict[Rating, float] = {"high": 1.0, "medium": 0.6, "low": 0.25, "none": 0.0}
COMPILED_PREFIX = "a compiled extension"


@dataclass(frozen=True)
class LlmRating:
    potential: Rating
    testability: Rating
    reason: str = ""


LlmRanker = Callable[[Sequence[DiscoveredFunction]], Awaitable[dict[str, LlmRating]]]
"""Rates functions by id; ids it leaves out keep their heuristic score."""


def skip_reason(fn: DiscoveredFunction, probe: ImportProbe | None = None) -> str | None:
    f = fn.features
    if fn.nested_in_function:
        return "nested function"
    if f.is_async:
        return "async function"
    if f.is_generator:
        return "generator"
    if fn.name.startswith("__") and fn.name.endswith("__"):
        return "dunder method"
    decorators = {d.rsplit(".", 1)[-1] for d in f.decorators}
    if hit := sorted(decorators & SKIP_DECORATORS):
        return f"@{hit[0]}"
    if f.loc < MIN_LOC:
        return f"too short ({f.loc} lines)"
    if f.loc > MAX_LOC:
        return f"too long ({f.loc} lines)"
    if f.io_calls:
        return f"does I/O ({', '.join(f.io_calls[:3])})"
    if f.nondet_calls:
        return f"nondeterministic ({', '.join(f.nondet_calls[:3])})"
    if f.writes_global:
        return "writes global or nonlocal state"
    if probe is not None and fn.module in probe.failed:
        why = probe.failed[fn.module]
        if why.startswith(COMPILED_PREFIX):
            return "compiled extension module"
        return f"module does not import: {why[:160]}"
    return None


def heuristic(fn: DiscoveredFunction) -> tuple[float, list[str]]:
    """Static potential in [0, 1] and the reasons for it."""
    f = fn.features
    reasons = list(f.anti_patterns)
    score = min(0.18 * len(f.anti_patterns), 0.55)
    if f.max_loop_depth >= 2:
        score += 0.2
        reasons.append(f"loops nested {f.max_loop_depth} deep")
    elif f.max_loop_depth == 1:
        score += 0.1
    if f.n_loops + f.n_comprehensions:
        score += 0.05
    if 8 <= f.loc <= 60:
        score += 0.1
    if f.n_args == 0:
        score *= 0.3
        reasons.append("takes no arguments")
    if not f.returns_value:
        score *= 0.6
        reasons.append("returns nothing")
    if fn.kind == "method":
        score *= 0.85
    return round(min(max(score, 0.0), 1.0), 4), reasons


def _item(fn: DiscoveredFunction, probe: ImportProbe | None) -> TriageItem:
    h, reasons = heuristic(fn)
    return TriageItem(
        function_id=fn.function_id,
        module=fn.module,
        qualname=fn.qualname,
        kind=fn.kind,
        file=fn.file,
        line=fn.line,
        end_line=fn.end_line,
        loc=fn.features.loc,
        import_line=fn.import_line,
        call_hint=fn.call_hint,
        heuristic_score=h,
        score=h,
        reasons=reasons,
        skip_reason=skip_reason(fn, probe),
    )


def unique_functions(functions: Sequence[DiscoveredFunction]) -> list[DiscoveredFunction]:
    """One function per id: a later definition replaces an earlier one, as at runtime."""
    by_id: dict[str, DiscoveredFunction] = {}
    for fn in functions:
        by_id.pop(fn.function_id, None)
        by_id[fn.function_id] = fn
    return list(by_id.values())


def rank(items: Sequence[TriageItem], *, max_items: int) -> list[TriageItem]:
    """Eligible functions by score, then skipped ones; at most ``max_items`` of each."""
    key = lambda it: (-it.score, -it.heuristic_score, it.function_id)  # noqa: E731
    eligible = sorted((it for it in items if not it.skip_reason), key=key)
    skipped = sorted((it for it in items if it.skip_reason), key=key)
    return eligible[:max_items] + skipped[:max_items]


def heuristic_items(
    functions: Sequence[DiscoveredFunction],
    probe: ImportProbe | None,
    *,
    max_items: int,
) -> list[TriageItem]:
    return rank([_item(fn, probe) for fn in unique_functions(functions)], max_items=max_items)


def llm_score(r: LlmRating) -> float:
    return RATING_VALUE[r.potential] * RATING_VALUE[r.testability]


def apply_ratings(
    items: Sequence[TriageItem], ratings: dict[str, LlmRating], *, max_items: int
) -> list[TriageItem]:
    out = []
    for it in items:
        r = ratings.get(it.function_id)
        if r is None or it.skip_reason:
            out.append(it)
            continue
        reasons = [*it.reasons, r.reason] if r.reason else list(it.reasons)
        out.append(
            it.model_copy(
                update={
                    "llm_potential": r.potential,
                    "llm_testability": r.testability,
                    "score": round(0.5 * it.heuristic_score + 0.5 * llm_score(r), 4),
                    "reasons": reasons,
                }
            )
        )
    return rank(out, max_items=max_items)


def llm_shortlist(
    items: Sequence[TriageItem], functions: Sequence[DiscoveredFunction], top: int
) -> list[DiscoveredFunction]:
    """The eligible functions worth an LLM rating, best heuristic first."""
    by_id = {fn.function_id: fn for fn in functions}
    return [by_id[it.function_id] for it in items if not it.skip_reason][:top]


def preselect(items: Sequence[TriageItem], *, n: int, min_score: float) -> list[TriageItem]:
    """Mark the top ``n`` eligible items with ``score >= min_score`` as preselected."""
    chosen = {
        it.function_id
        for it in [it for it in items if not it.skip_reason and it.score >= min_score][:n]
    }
    return [it.model_copy(update={"preselected": it.function_id in chosen}) for it in items]
