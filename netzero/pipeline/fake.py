"""Scripted pipeline: a deterministic, grammar-valid run without git, uv or an LLM.

``FakePipeline`` drives a ``RunContext`` through every state and emits the same
events, in the same shapes, as the real pipeline. Its repository is a built-in
catalog of 16 small functions (one module each) whose fates are scripted, so
the frontend can be developed against every outcome the UI has to show
(``NETZERO_FAKE_PIPELINE=1``) and the tests can exercise the orchestrator, the
bus and the grammar end to end.

Everything is real where it is cheap: the test files are runnable pytest code,
the rewrites are runnable Python (the syntax-error candidate excepted), the
diffs are real unified diffs and the artifacts are written to disk. Numbers
come from the bench's energy model (kWh = (cpu_s * P_core + wall_s * P_ram) /
3.6e6), so deltas sit inside their CIs, savings are original - candidate and
the decision follows the configured alpha / minimum effect with Holm's
correction.

Scenarios: ``demo`` (8 preselected functions: 6 accepted, 1 all_rejected,
1 no_significant_win), ``short`` (3 functions) and ``fail_env`` (the install
step fails). ``speed`` divides every sleep; at 1 a demo run takes ~2.5 min,
at >= 1000 sleeps become ``asyncio.sleep(0)``.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import random
import textwrap
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal
from urllib.parse import quote

from scipy.stats import t as student_t

from netzero.errors import EnvError, LlmError, StepTimeout, ValidationFailed
from netzero.events import (
    CI,
    ArtifactRef,
    BenchStats,
    Calibration,
    CandidateBenchQueuedData,
    CandidateBenchStartedData,
    CandidateCode,
    CandidateId,
    CandidateRejectedData,
    CandidateWriteStartedData,
    DecisionRule,
    DiffCheckResult,
    FunctionDecisionData,
    FunctionDiffRef,
    FunctionInfo,
    FunctionKind,
    FunctionOutcome,
    GridInfo,
    ImportProbe,
    Interval,
    LlmStage,
    LlmUsageData,
    MeasureStats,
    Mismatch,
    PackageInfo,
    PatchRef,
    PowerInfo,
    PytestFailure,
    PytestResult,
    RankingEntry,
    Rating,
    RejectReason,
    RunCloneStartedData,
    RunEnvStartedData,
    RunPowerDetectedData,
    RunTriageStartedData,
    StaticCheck,
    TestFile,
    TestsRunStartedData,
    TestsWriteStartedData,
    TriageItem,
)
from netzero.pipeline.common import CANDIDATES, STRATEGY_HINTS, unified_diff
from netzero.pipeline.common import fmt_p as _fmt_p
from netzero.pipeline.common import pct as _pct
from netzero.pipeline.common import reap as _reap
from netzero.pipeline.run import FunctionScope, RunContext

__all__ = [
    "CATALOG",
    "DEMO_SELECTION",
    "SCENARIOS",
    "SHORT_SELECTION",
    "Draft",
    "FakeFunction",
    "FakePipeline",
    "FakePlan",
    "FakeRewrite",
    "Scenario",
]

Scenario = Literal["demo", "short", "fail_env"]
SCENARIOS: tuple[str, ...] = ("demo", "short", "fail_env")

Fate = Literal[
    "accepted",
    "reverted",
    "no_significant_win",
    "all_rejected",
    "skipped_untestable",
    "skipped_capture",
    "not_selectable",
]

# ---------------------------------------------------------------------------
# Catalog model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Draft:
    """One generated test file. ``failing`` names the test that fails on the original."""

    body: str
    failing: str | None = None
    message: str = ""
    diagnosis: str = ""  # what a repair changed


@dataclass(frozen=True)
class FakeRewrite:
    code: str  # the whole function, dedented
    strategy: str
    rationale: str
    new_imports: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakePlan:
    """What happens to one candidate attempt.

    ``rewrite`` keys ``FakeFunction.rewrites`` (None: the LLM call fails).
    ``reject`` None means the candidate passes its checks and is benched at
    ``delta`` percent CO2 per call with per-trial log-noise ``sigma``.
    """

    rewrite: str | None
    reject: RejectReason | None = None
    delta: float = 0.0
    sigma: float = 0.04
    failing: str = ""  # tests_failed: the failing test
    message: str = ""  # tests_failed: the assertion message
    mismatch: tuple[str, str, str] | None = None  # differential: (path, expected, actual)
    problems: tuple[str, ...] = ()  # static check problems
    detail: str = ""
    diagnosis: str = ""  # repairs: what the model says it fixed
    repair: FakePlan | None = None


@dataclass(frozen=True)
class FakeFunction:
    module: str
    qualname: str
    doc: str  # module docstring
    source: str  # the function, dedented
    fate: Fate
    heuristic: float
    heuristic_reasons: tuple[str, ...]
    potential: Rating
    testability: Rating
    score: float
    reasons: tuple[str, ...]
    est_call_s: float = 1e-3
    imports: tuple[str, ...] = ()
    kind: FunctionKind = "function"
    owner: str = ""  # enclosing class of a method
    owner_head: str = ""  # class text before the method
    skip_reason: str | None = None
    drafts: tuple[Draft, ...] = ()
    rewrites: Mapping[str, FakeRewrite] = field(default_factory=dict)
    plans: Mapping[str, FakePlan] = field(default_factory=dict)
    n_calls: int = 64
    previews: tuple[str, ...] = ()
    mutates_args: bool = False
    raises: bool = False
    merge_mismatches: tuple[Mismatch, ...] = ()  # merge differential fails -> reverted
    capture_problem: str = ""  # capture is not deterministic -> skipped_capture

    # -- identity --------------------------------------------------------------

    @property
    def function_id(self) -> str:
        return f"{self.module}:{self.qualname}"

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def file(self) -> str:
        return self.module.replace(".", "/") + ".py"

    @property
    def test_path(self) -> str:
        return f"tests/netzero/test_{self.module.replace('.', '_')}.py"

    # -- module text -------------------------------------------------------------

    def render(self, func_src: str, extra_imports: tuple[str, ...] = ()) -> str:
        """The module file with ``func_src`` in place of the function."""
        imports = sorted(set(self.imports) | set(extra_imports), key=_import_key)
        head = f'"""{self.doc}"""\n\n'
        if imports:
            head += "\n".join(imports) + "\n\n"
        head += "\n"
        if self.owner:
            return head + self.owner_head + textwrap.indent(func_src, "    ")
        return head + func_src

    @cached_property
    def module_text(self) -> str:
        return self.render(self.source)

    def candidate_text(self, rewrite: str) -> str:
        rw = self.rewrites[rewrite]
        return self.render(rw.code, rw.new_imports)

    def diff(self, rewrite: str) -> str:
        return unified_diff(self.file, self.module_text, self.candidate_text(rewrite))

    # -- discovery metadata ------------------------------------------------------

    @cached_property
    def line(self) -> int:
        before = self.render("")
        return len(before.splitlines()) + 1

    @property
    def end_line(self) -> int:
        return self.line + len(self.source.splitlines()) - 1

    @property
    def loc(self) -> int:
        return sum(
            1 for ln in self.source.splitlines() if ln.strip() and not ln.strip().startswith("#")
        )

    @property
    def import_line(self) -> str:
        return f"from {self.module} import {self.owner or self.name}"

    @cached_property
    def call_hint(self) -> str:
        node = ast.parse(self.source).body[0]
        assert isinstance(node, ast.FunctionDef)
        params = [a.arg for a in node.args.posonlyargs + node.args.args]
        if self.kind == "method":
            params = params[1:]
        params += [f"{a.arg}=..." for a in node.args.kwonlyargs]
        args = ", ".join(params)
        if self.kind == "function":
            return f"{node.name}({args})"
        return f"{self.owner}(...).{node.name}({args})"

    def info(self) -> FunctionInfo:
        return FunctionInfo(
            function_id=self.function_id,
            module=self.module,
            qualname=self.qualname,
            kind=self.kind,
            file=self.file,
            line=self.line,
            end_line=self.end_line,
            loc=self.loc,
            import_line=self.import_line,
            call_hint=self.call_hint,
            source=self.source,
        )

    def triage_item(self, *, llm: bool, preselected: bool = False) -> TriageItem:
        return TriageItem(
            function_id=self.function_id,
            module=self.module,
            qualname=self.qualname,
            kind=self.kind,
            file=self.file,
            line=self.line,
            end_line=self.end_line,
            loc=self.loc,
            import_line=self.import_line,
            call_hint=self.call_hint,
            heuristic_score=self.heuristic,
            llm_potential=self.potential if llm else None,
            llm_testability=self.testability if llm else None,
            score=self.score if llm else self.heuristic,
            reasons=list(self.reasons if llm else self.heuristic_reasons),
            skip_reason=self.skip_reason,
            preselected=preselected,
        )

    # -- tests -------------------------------------------------------------------

    def draft(self, attempt: int) -> Draft:
        return self.drafts[min(attempt, len(self.drafts) - 1)]

    def test_code(self, attempt: int) -> str:
        body = self.draft(attempt).body.strip("\n")
        return f"import pytest\n\n{self.import_line}\n\n\n{body}\n"

    def test_file(self, attempt: int) -> TestFile:
        code = self.test_code(attempt)
        names, workload = _test_names(code)
        draft = self.draft(attempt)
        return TestFile(
            path=self.test_path,
            code=code,
            test_names=names,
            workload_test=workload,
            notes=f"{len(names)} tests; `{workload}` is the benchmark workload",
            diagnosis=draft.diagnosis if attempt else "",
        )


def _import_key(line: str) -> tuple[bool, str]:
    return (line.startswith("from "), line.split()[1])


def _test_names(code: str) -> tuple[list[str], str]:
    names: list[str] = []
    workload = ""
    for node in ast.parse(code).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            names.append(node.name)
            if any("nz_workload" in ast.unparse(d) for d in node.decorator_list):
                workload = node.name
    return names, workload


# ---------------------------------------------------------------------------
# Catalog: the fake repository
# ---------------------------------------------------------------------------

_DEDUPE = '''\
def dedupe_preserve_order(items):
    """Return the unique items of ``items`` in first-seen order."""
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
'''

_DEDUPE_TESTS = """\
def test_keeps_first_occurrence_order():
    assert dedupe_preserve_order([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_empty_input():
    assert dedupe_preserve_order([]) == []


def test_accepts_any_iterable():
    assert dedupe_preserve_order("abracadabra") == ["a", "b", "r", "c", "d"]


@pytest.mark.nz_workload
def test_workload_many_duplicates():
    items = [(i * 7919) % 1000 for i in range(5000)]
    out = dedupe_preserve_order(items)
    assert len(out) == 1000
    assert out[:3] == [0, 919, 838]
"""

_FREQ = '''\
def word_frequencies(text):
    """Count case-insensitive, whitespace-separated words in ``text``."""
    counts = {}
    for word in text.split():
        if word.lower() in counts:
            counts[word.lower()] += 1
        else:
            counts[word.lower()] = 1
    return counts
'''

_FREQ_TESTS = r"""
def test_counts_case_insensitively():
    assert word_frequencies("The cat the CAT") == {"the": 2, "cat": 2}


def test_empty_text():
    assert word_frequencies("") == {}


def test_splits_on_any_whitespace():
    assert word_frequencies("a\tb\na  a") == {"a": 3, "b": 1}


@pytest.mark.nz_workload
def test_workload_long_text():
    text = " ".join(["Lorem", "ipsum", "dolor", "SIT", "amet", "lorem"] * 2000)
    freq = word_frequencies(text)
    assert freq["lorem"] == 4000
    assert len(freq) == 5
"""

_PRIMES = '''\
def primes_below(n):
    """Return all primes p with 2 <= p < n, ascending."""
    primes = []
    for candidate in range(2, n):
        is_prime = True
        for divisor in range(2, candidate):
            if candidate % divisor == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(candidate)
    return primes
'''

_PRIMES_TESTS = """\
def test_small_primes():
    assert primes_below(20) == [2, 3, 5, 7, 11, 13, 17, 19]


def test_upper_bound_is_exclusive():
    assert primes_below(7) == [2, 3, 5]


def test_no_primes_below_two():
    assert primes_below(2) == []
    assert primes_below(0) == []


@pytest.mark.nz_workload
def test_workload_primes_below_5000():
    primes = primes_below(5000)
    assert len(primes) == 669
    assert primes[-1] == 4999
"""

_PAIRS = '''\
def has_pair_with_sum(numbers, target):
    """True if two different positions in ``numbers`` add up to ``target``."""
    for i in range(len(numbers)):
        for j in range(len(numbers)):
            if i != j and numbers[i] + numbers[j] == target:
                return True
    return False
'''

_PAIRS_TESTS = """\
def test_finds_pair():
    assert has_pair_with_sum([8, 3, 5, 1], 9)


def test_does_not_reuse_one_element():
    assert not has_pair_with_sum([5, 1], 10)


def test_empty_and_single():
    assert not has_pair_with_sum([], 0)
    assert not has_pair_with_sum([4], 8)


@pytest.mark.nz_workload
def test_workload_2000_even_numbers():
    numbers = list(range(0, 4000, 2))
    assert not has_pair_with_sum(numbers, 1)
    assert has_pair_with_sum(numbers, 7994)
"""

_LEV = '''\
def levenshtein(a, b):
    """Edit distance between ``a`` and ``b`` (insert, delete, substitute)."""
    rows = len(a) + 1
    cols = len(b) + 1
    dist = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dist[i][0] = i
    for j in range(cols):
        dist[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dist[i][j] = min(
                dist[i - 1][j] + 1,
                dist[i][j - 1] + 1,
                dist[i - 1][j - 1] + cost,
            )
    return dist[rows - 1][cols - 1]
'''

_LEV_TESTS = """\
def test_classic_example():
    assert levenshtein("kitten", "sitting") == {expected}


def test_empty_strings():
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "") == 3
    assert levenshtein("", "") == 0


def test_symmetric():
    assert levenshtein("flaw", "lawn") == levenshtein("lawn", "flaw") == 2


@pytest.mark.nz_workload
def test_workload_long_strings():
    a = "the quick brown fox jumps over the lazy dog" * 3
    b = "the quick brown cat leaps over the lazy hog" * 3
    assert levenshtein(a, b) == 21
"""

_GRAPH_HEAD = """\
class Graph:
    def __init__(self):
        self.adj = {}

    def add_edge(self, a, b):
        self.adj.setdefault(a, []).append(b)
        self.adj.setdefault(b, []).append(a)

"""

_GRAPH = '''\
def shortest_path_lengths(self, source):
    """BFS distances from ``source`` to every reachable node."""
    dist = {source: 0}
    queue = [source]
    while queue:
        node = queue.pop(0)
        for nxt in self.adj.get(node, []):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                queue.append(nxt)
    return dist
'''

_GRAPH_TESTS = """\
def _grid(n):
    g = Graph()
    for r in range(n):
        for c in range(n):
            if r + 1 < n:
                g.add_edge((r, c), (r + 1, c))
            if c + 1 < n:
                g.add_edge((r, c), (r, c + 1))
    return g


def test_path_graph():
    g = Graph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    assert g.shortest_path_lengths("a") == {"a": 0, "b": 1, "c": 2}


def test_isolated_source():
    assert Graph().shortest_path_lengths("x") == {"x": 0}


def test_unreachable_nodes_are_absent():
    g = Graph()
    g.add_edge(1, 2)
    g.add_edge(3, 4)
    assert g.shortest_path_lengths(1) == {1: 0, 2: 1}


@pytest.mark.nz_workload
def test_workload_grid_60x60():
    dist = _grid(60).shortest_path_lengths((0, 0))
    assert len(dist) == 3600
    assert dist[(59, 59)] == 118
"""

_TOTALS = '''\
def total_by_category(rows):
    """Sum ``row["amount"]`` per ``row["category"]``, categories in first-seen order."""
    categories = []
    for row in rows:
        if row["category"] not in categories:
            categories.append(row["category"])
    totals = {}
    for category in categories:
        total = 0
        for row in rows:
            if row["category"] == category:
                total += row["amount"]
        totals[category] = total
    return totals
'''

_TOTALS_TESTS = """\
def test_sums_per_category():
    rows = [
        {"category": "food", "amount": 12},
        {"category": "rent", "amount": 800},
        {"category": "food", "amount": 30},
    ]
    assert total_by_category(rows) == {"food": 42, "rent": 800}


def test_first_seen_order():
    rows = [{"category": c, "amount": 1} for c in "bab"]
    assert list(total_by_category(rows)) == ["b", "a"]


def test_no_rows():
    assert total_by_category([]) == {}


@pytest.mark.nz_workload
def test_workload_10k_rows_40_categories():
    rows = [{"category": f"c{i % 40}", "amount": i} for i in range(10_000)]
    totals = total_by_category(rows)
    assert len(totals) == 40
    assert totals["c0"] == sum(range(0, 10_000, 40))
"""

_MOVING = '''\
def moving_average(values, window):
    """Means of each full ``window``-sized slice of ``values``."""
    if window <= 0:
        raise ValueError("window must be positive")
    averages = []
    for start in range(len(values) - window + 1):
        chunk = values[start : start + window]
        averages.append(sum(chunk) / window)
    return averages
'''

_MOVING_TESTS = """\
def test_simple_window():
    assert moving_average([1, 2, 3, 4, 5], 2) == [1.5, 2.5, 3.5, 4.5]


def test_window_equal_to_length():
    assert moving_average([1, 2, 3], 3) == [2.0]


def test_float_values():
    assert moving_average([0.1, 0.2, 0.3, 0.4], 2) == pytest.approx([0.15, 0.25, 0.35])


def test_rejects_non_positive_window():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], 0)


@pytest.mark.nz_workload
def test_workload_6000_points_window_50():
    values = [(i * 37) % 101 for i in range(6000)]
    out = moving_average(values, 50)
    assert len(out) == 5951
    assert out[0] == sum(values[:50]) / 50
"""

_JOIN = '''\
def join_fields(fields, sep=","):
    """Join ``fields`` with ``sep``, converting each field with ``str``."""
    parts = []
    for field in fields:
        parts.append(str(field))
    return sep.join(parts)
'''

_JOIN_TESTS = """\
def test_joins_with_default_comma():
    assert join_fields(["a", 1, 2.5]) == "a,1,2.5"


def test_custom_separator():
    assert join_fields(["x", "y"], sep="|") == "x|y"


def test_empty():
    assert join_fields([]) == ""


@pytest.mark.nz_workload
def test_workload_wide_record():
    record = list(range(200))
    assert join_fields(record).count(",") == 199
"""

_SEARCH = '''\
def index_of_sorted(items, target):
    """Index of ``target`` in the ascending list ``items``, or -1."""
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
'''

_SEARCH_TESTS = """\
def test_finds_each_element():
    items = [1, 3, 5, 7, 9]
    assert [index_of_sorted(items, x) for x in items] == [0, 1, 2, 3, 4]


def test_missing_returns_minus_one():
    assert index_of_sorted([], 1) == -1
    assert index_of_sorted([1, 3, 5], 4) == -1


def test_bounds():
    assert index_of_sorted([2, 4], 1) == -1
    assert index_of_sorted([2, 4], 5) == -1


@pytest.mark.nz_workload
def test_workload_lookups_in_10k():
    items = list(range(0, 20_000, 2))
    hits = sum(1 for t in range(0, 20_000, 7) if index_of_sorted(items, t) >= 0)
    assert hits == 1429
"""

_ZSCORE = '''\
def zscore(values):
    """Standard scores of ``values`` (population standard deviation)."""
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    return [(v - mean) / std for v in values]
'''

_ZSCORE_TESTS = """\
def test_known_values():
    z = zscore([2, 4, 4, 4, 5, 5, 7, 9])
    assert z[0] == pytest.approx(-1.5)
    assert z[-1] == pytest.approx(2.0)


def test_mean_is_zero():
    assert sum(zscore([1.5, 2.5, 10.0])) == pytest.approx(0.0, abs=1e-12)


def test_unit_variance():
    z = zscore([3, 1, 4, 1, 5, 9, 2, 6])
    assert sum(x * x for x in z) / len(z) == pytest.approx(1.0)


@pytest.mark.nz_workload
def test_workload_5000_values():
    values = [((i * 7919) % 1000) / 10 for i in range(5000)]
    z = zscore(values)
    assert len(z) == 5000
    assert sum(z) == pytest.approx(0.0, abs=1e-6)
"""

_TOPK = '''\
def top_k_inplace(values, k):
    """The ``k`` largest values, descending. Sorts ``values`` in place."""
    values.sort(reverse=True)
    return values[:k]
'''

_TOPK_TESTS = """\
def test_returns_k_largest_descending():
    assert top_k_inplace([3, 9, 1, 7, 5], 2) == [9, 7]


def test_k_zero_returns_empty():
    assert top_k_inplace([4, 2, 8], 0) == []


def test_k_larger_than_list():
    assert top_k_inplace([2, 1], 5) == [2, 1]


@pytest.mark.nz_workload
def test_workload_top_10_of_20k():
    values = [(i * 7919) % 20_011 for i in range(20_000)]
    assert len(top_k_inplace(values, 10)) == 10
"""

_NORMALIZE = r'''def normalize_whitespace(text):
    """Collapse runs of whitespace to one space and strip both ends."""
    out = ""
    pending_space = False
    for ch in text:
        if ch in " \t\n\r":
            pending_space = bool(out)
        else:
            if pending_space:
                out += " "
                pending_space = False
            out += ch
    return out
'''

_NORMALIZE_TESTS = r"""
def test_collapses_runs():
    assert normalize_whitespace("a  b\t\tc") == "a b c"


def test_strips_ends():
    assert normalize_whitespace("  hello \n") == "hello"


def test_empty():
    assert normalize_whitespace("") == ""


def test_{name}():
    assert normalize_whitespace("a{ch}b") == "a b"


@pytest.mark.nz_workload
def test_workload_long_text():
    text = "lorem \t ipsum\n\n dolor " * 500
    assert normalize_whitespace(text).count(" ") == 1499
"""

_PARSE = '''\
def parse_records(lines):
    """Parse ``k=v;k=v`` lines into dicts, stamping each with the parse time."""
    records = []
    for line in lines:
        record = {}
        for pair in line.split(";"):
            if pair:
                key, _, value = pair.partition("=")
                record[key.strip()] = value.strip()
        record["_parsed_at"] = time.time()
        records.append(record)
    return records
'''

_PARSE_TESTS = """\
def test_parses_pairs():
    [rec] = parse_records(["a=1;b=2"])
    assert rec["a"] == "1" and rec["b"] == "2"


def test_ignores_empty_pairs():
    [rec] = parse_records(["a=1;;"])
    assert set(rec) == {"a", "_parsed_at"}


def test_one_record_per_line():
    assert len(parse_records(["a=1", "b=2", "c=3"])) == 3


@pytest.mark.nz_workload
def test_workload_2000_lines():
    lines = [f"id={i};name=item{i};qty={i % 7}" for i in range(2000)]
    assert len(parse_records(lines)) == 2000
"""

_RATES = '''\
def fetch_exchange_rates(base="EUR"):
    """Latest exchange rates for ``base`` from the rates service."""
    url = f"https://rates.example.com/latest?base={base}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)["rates"]
'''

_WALK = '''\
def random_walk(steps):
    """Positions of a +1/-1 random walk starting at 0."""
    position = 0
    path = [position]
    for _ in range(steps):
        position += random.choice((-1, 1))
        path.append(position)
    return path
'''


def _rw(code: str, strategy: str, rationale: str, *imports: str) -> FakeRewrite:
    return FakeRewrite(code=code, strategy=strategy, rationale=rationale, new_imports=imports)


def _build_catalog() -> list[FakeFunction]:
    fns: list[FakeFunction] = []

    fns.append(
        FakeFunction(
            module="textkit.dedupe",
            qualname="dedupe_preserve_order",
            doc="Order-preserving de-duplication.",
            source=_DEDUPE,
            fate="accepted",
            heuristic=0.58,
            heuristic_reasons=("list membership test inside a loop",),
            potential="high",
            testability="high",
            score=0.92,
            reasons=(
                "O(n²): `item not in result` scans a list on every iteration",
                "pure function of its input; easy to test",
            ),
            est_call_s=0.0184,
            drafts=(Draft(_DEDUPE_TESTS),),
            rewrites={
                "seen_set": _rw(
                    '''\
def dedupe_preserve_order(items):
    """Return the unique items of ``items`` in first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
''',
                    "track seen items in a set",
                    "Set membership is O(1), so the loop is O(n) instead of O(n²).",
                ),
                "fromkeys": _rw(
                    '''\
def dedupe_preserve_order(items):
    """Return the unique items of ``items`` in first-seen order."""
    return list(dict.fromkeys(items))
''',
                    "dict.fromkeys keeps first-seen order",
                    "dicts preserve insertion order; building one dedupes in C in a single pass.",
                ),
                "alias": _rw(
                    '''\
def dedupe_preserve_order(items):
    """Return the unique items of ``items`` in first-seen order."""
    result = []
    append = result.append
    for item in items:
        if item not in result:
            append(item)
    return result
''',
                    "hoist the bound method out of the loop",
                    "Saves an attribute lookup per new item; the scan itself is unchanged.",
                ),
            },
            plans={
                "A": FakePlan("seen_set", delta=-71.8),
                "B": FakePlan("fromkeys", delta=-78.6),
                "C": FakePlan("alias", delta=-2.1, sigma=0.05),
            },
            n_calls=7,
            previews=(
                "dedupe_preserve_order([3, 1, 3, 2, 1]) -> [3, 1, 2]",
                "dedupe_preserve_order('abracadabra') -> ['a', 'b', 'r', 'c', 'd']",
                "dedupe_preserve_order([0, 919, 838, 757, …5000 items]) -> [0, 919, 838, …1000 items]",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="textkit.freq",
            qualname="word_frequencies",
            doc="Word counts.",
            source=_FREQ,
            fate="accepted",
            heuristic=0.47,
            heuristic_reasons=("repeated method call in a loop",),
            potential="medium",
            testability="high",
            score=0.81,
            reasons=(
                "`word.lower()` is computed up to three times per word",
                "collections.Counter does the counting in C",
            ),
            est_call_s=0.00212,
            drafts=(Draft(_FREQ_TESTS),),
            rewrites={
                "get_loop": _rw(
                    '''\
def word_frequencies(text):
    """Count case-insensitive, whitespace-separated words in ``text``."""
    counts = {}
    get = counts.get
    for word in text.lower().split():
        counts[word] = get(word, 0) + 1
    return counts
''',
                    "lower-case once, then count with dict.get",
                    "One `lower()` over the whole text and one dict lookup per word.",
                ),
                "counter": _rw(
                    '''\
def word_frequencies(text):
    """Count case-insensitive, whitespace-separated words in ``text``."""
    return dict(Counter(text.lower().split()))
''',
                    "collections.Counter",
                    "Counter's update loop runs in C; dict() keeps the plain-dict return type.",
                    "from collections import Counter",
                ),
            },
            plans={
                "A": FakePlan("get_loop", delta=-38.4),
                "B": FakePlan("counter", delta=-46.9),
                "C": FakePlan(
                    None,
                    reject="llm_error",
                    detail="no code: the response hit max_tokens before the function was complete",
                ),
            },
            n_calls=5,
            previews=(
                "word_frequencies('The cat the CAT') -> {'the': 2, 'cat': 2}",
                "word_frequencies('') -> {}",
                "word_frequencies('Lorem ipsum dolor SIT amet lorem Lorem …71999 chars') -> {'lorem': 4000, …5 keys}",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="algos.primes",
            qualname="primes_below",
            doc="Prime numbers.",
            source=_PRIMES,
            fate="accepted",
            heuristic=0.78,
            heuristic_reasons=("nested loops", "modulo in the inner loop"),
            potential="high",
            testability="high",
            score=0.90,
            reasons=(
                "trial division by every smaller number: ~O(n²)",
                "a sieve is the textbook fix",
            ),
            est_call_s=0.094,
            drafts=(Draft(_PRIMES_TESTS),),
            rewrites={
                "sieve": _rw(
                    '''\
def primes_below(n):
    """Return all primes p with 2 <= p < n, ascending."""
    if n < 3:
        return []
    sieve = bytearray([1]) * n
    sieve[0] = sieve[1] = 0
    for p in range(2, int(n ** 0.5) + 1):
        if sieve[p]:
            sieve[p * p :: p] = bytes(len(range(p * p, n, p)))
    return [i for i, flag in enumerate(sieve) if flag]
''',
                    "sieve of Eratosthenes over a bytearray",
                    "O(n log log n); slice assignment clears multiples in C.",
                ),
                "sieve_inclusive": _rw(
                    '''\
def primes_below(n):
    """Return all primes p with 2 <= p < n, ascending."""
    if n < 3:
        return []
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for p in range(2, math.isqrt(n) + 1):
        if is_prime[p]:
            is_prime[p * p :: p] = [False] * len(range(p * p, n + 1, p))
    return [i for i, flag in enumerate(is_prime) if flag]
''',
                    "list sieve with math.isqrt bound",
                    "Sieve with an integer square-root bound.",
                    "import math",
                ),
                "compress": _rw(
                    '''\
def primes_below(n):
    """Return all primes p with 2 <= p < n, ascending."""
    if n < 3:
        return []
    is_prime = [True] * n
    is_prime[0] = is_prime[1] = False
    for p in range(2, math.isqrt(n - 1) + 1):
        if is_prime[p]:
            is_prime[p * p :: p] = [False] * len(range(p * p, n, p))
    return list(itertools.compress(range(n), is_prime))
''',
                    "list sieve, exclusive bound, itertools.compress",
                    "Sieve sized to n so n itself is never reported; compress builds the result in C.",
                    "import itertools",
                    "import math",
                ),
                "sqrt_trial": _rw(
                    '''\
def primes_below(n):
    """Return all primes p with 2 <= p < n, ascending."""
    primes = []
    for candidate in range(2, n):
        limit = math.isqrt(candidate)
        for p in primes:
            if p > limit:
                primes.append(candidate)
                break
            if candidate % p == 0:
                break
        else:
            primes.append(candidate)
    return primes
''',
                    "trial division by known primes up to sqrt",
                    "Only primes up to the square root can be the smallest factor.",
                    "import math",
                ),
            },
            plans={
                "A": FakePlan("sieve", delta=-97.8),
                "B": FakePlan(
                    "sieve_inclusive",
                    reject="tests_failed",
                    failing="test_upper_bound_is_exclusive",
                    message="assert [2, 3, 5, 7] == [2, 3, 5]",
                    repair=FakePlan(
                        "compress",
                        delta=-96.9,
                        diagnosis="The sieve covered n itself, so primes_below(7) returned 7. "
                        "Sized the table to n to keep the bound exclusive.",
                    ),
                ),
                "C": FakePlan("sqrt_trial", delta=-84.5),
            },
            n_calls=6,
            previews=(
                "primes_below(20) -> [2, 3, 5, 7, 11, 13, 17, 19]",
                "primes_below(7) -> [2, 3, 5]",
                "primes_below(5000) -> [2, 3, 5, 7, 11, …669 items]",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="algos.pairs",
            qualname="has_pair_with_sum",
            doc="Pair searches.",
            source=_PAIRS,
            fate="accepted",
            heuristic=0.71,
            heuristic_reasons=("nested loops over the same sequence",),
            potential="high",
            testability="high",
            score=0.88,
            reasons=("O(n²) scan over all ordered pairs; a set of complements is O(n)",),
            est_call_s=0.21,
            drafts=(Draft(_PAIRS_TESTS),),
            rewrites={
                "set": _rw(
                    '''\
def has_pair_with_sum(numbers, target):
    """True if two different positions in ``numbers`` add up to ``target``."""
    seen = set()
    for x in numbers:
        if target - x in seen:
            return True
        seen.add(x)
    return False
''',
                    "one pass with a set of seen values",
                    "Each value only needs its complement among earlier values: O(n).",
                ),
                "two_pointers": _rw(
                    '''\
def has_pair_with_sum(numbers, target):
    """True if two different positions in ``numbers`` add up to ``target``."""
    values = sorted(numbers)
    lo, hi = 0, len(values) - 1
    while lo < hi:
        total = values[lo] + values[hi]
        if total == target:
            return True
        if total < target:
            lo += 1
        else:
            hi -= 1
    return False
''',
                    "sort, then walk two pointers inwards",
                    "O(n log n) with a C sort and a linear scan.",
                ),
                "missing_colon": _rw(
                    '''\
def has_pair_with_sum(numbers, target):
    """True if two different positions in ``numbers`` add up to ``target``."""
    seen = set()
    for x in numbers:
        complement = target - x
        if complement in seen
            return True
        seen.add(x)
    return False
''',
                    "complement lookup with a local",
                    "Store the complement once per element and check the set.",
                ),
            },
            plans={
                "A": FakePlan("set", delta=-97.1),
                "B": FakePlan("two_pointers", delta=-93.4),
                "C": FakePlan(
                    "missing_colon",
                    reject="syntax",
                    problems=("SyntaxError: expected ':' (line 6)",),
                ),
            },
            n_calls=6,
            previews=(
                "has_pair_with_sum([8, 3, 5, 1], 9) -> True",
                "has_pair_with_sum([5, 1], 10) -> False",
                "has_pair_with_sum([0, 2, 4, 6, …2000 items], 1) -> False",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="algos.strings",
            qualname="levenshtein",
            doc="String distances.",
            source=_LEV,
            fate="accepted",
            heuristic=0.74,
            heuristic_reasons=("nested loops", "2-D list allocation"),
            potential="medium",
            testability="high",
            score=0.71,
            reasons=("allocates the full (m+1)×(n+1) matrix where two rows suffice",),
            est_call_s=0.0032,
            drafts=(
                Draft(
                    _LEV_TESTS.format(expected=2),
                    failing="test_classic_example",
                    message="assert 3 == 2",
                ),
                Draft(
                    _LEV_TESTS.format(expected=3),
                    diagnosis="kitten -> sitting needs 3 edits (k→s, e→i, +g); "
                    "the expected value in test_classic_example was wrong.",
                ),
            ),
            rewrites={
                "two_rows": _rw(
                    '''\
def levenshtein(a, b):
    """Edit distance between ``a`` and ``b`` (insert, delete, substitute)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]
''',
                    "two rolling rows",
                    "Only the previous row is needed; memory drops to O(min(m, n)).",
                ),
                "two_rows_enumerate": _rw(
                    '''\
def levenshtein(a, b):
    """Edit distance between ``a`` and ``b`` (insert, delete, substitute)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        append = current.append
        for j, cb in enumerate(b, 1):
            append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]
''',
                    "two rows, enumerate over characters",
                    "Iterating characters directly avoids index arithmetic and double subscripts.",
                ),
                "lru_recursive": _rw(
                    '''\
def levenshtein(a, b):
    """Edit distance between ``a`` and ``b`` (insert, delete, substitute)."""

    @functools.lru_cache(maxsize=None)
    def dist(i, j):
        if i == 0 or j == 0:
            return i + j
        cost = 0 if a[i - 1] == b[j - 1] else 1
        return min(dist(i - 1, j) + 1, dist(i, j - 1) + 1, dist(i - 1, j - 1) + cost)

    return dist(len(a), len(b))
''',
                    "memoised recursion",
                    "Top-down DP: only reachable cells are computed.",
                    "import functools",
                ),
            },
            plans={
                "A": FakePlan("two_rows", delta=-36.2),
                "B": FakePlan("two_rows_enumerate", delta=-42.7),
                "C": FakePlan(
                    "lru_recursive",
                    reject="static_rule",
                    problems=(
                        "NZ003 recursion: `dist` calls itself; depth grows with len(a) + len(b)",
                        "NZ004 unbounded cache: functools.lru_cache(maxsize=None) on a per-call closure",
                    ),
                ),
            },
            n_calls=8,
            previews=(
                "levenshtein('kitten', 'sitting') -> 3",
                "levenshtein('', 'abc') -> 3",
                "levenshtein('the quick brown fox jumps over …129 chars', 'the quick brown cat leaps over …129 chars') -> 21",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="algos.graph",
            qualname="Graph.shortest_path_lengths",
            doc="Unweighted graphs.",
            source=_GRAPH,
            kind="method",
            owner="Graph",
            owner_head=_GRAPH_HEAD,
            fate="accepted",
            heuristic=0.44,
            heuristic_reasons=("list.pop(0) inside a loop",),
            potential="medium",
            testability="high",
            score=0.55,
            reasons=("`queue.pop(0)` shifts the whole list: use collections.deque",),
            est_call_s=0.0055,
            drafts=(Draft(_GRAPH_TESTS),),
            rewrites={
                "deque": _rw(
                    '''\
def shortest_path_lengths(self, source):
    """BFS distances from ``source`` to every reachable node."""
    dist = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        d = dist[node] + 1
        for nxt in self.adj.get(node, ()):
            if nxt not in dist:
                dist[nxt] = d
                queue.append(nxt)
    return dist
''',
                    "collections.deque for the BFS queue",
                    "popleft is O(1); the next distance is computed once per node.",
                    "from collections import deque",
                ),
                "frontier": _rw(
                    '''\
def shortest_path_lengths(self, source):
    """BFS distances from ``source`` to every reachable node."""
    dist = {source: 0}
    frontier = [source]
    depth = 0
    adj = self.adj
    while frontier:
        depth += 1
        next_frontier = []
        for node in frontier:
            for nxt in adj.get(node, ()):
                if nxt not in dist:
                    dist[nxt] = depth
                    next_frontier.append(nxt)
        frontier = next_frontier
    return dist
''',
                    "level-by-level frontier lists",
                    "No queue at all: each BFS level is a plain list.",
                ),
                "head_pointer": _rw(
                    '''\
def shortest_path_lengths(self, source):
    """BFS distances from ``source`` to every reachable node."""
    dist = {source: 0}
    queue = [source]
    head = 0
    while head < len(queue):
        node = queue[head]
        for nxt in self.adj.get(node, []):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                queue.append(nxt)
    return dist
''',
                    "read pointer instead of pop(0)",
                    "Advance an index over the list instead of shifting it.",
                ),
            },
            plans={
                "A": FakePlan("deque", delta=-61.8),
                "B": FakePlan("frontier", delta=-57.2),
                "C": FakePlan(
                    "head_pointer",
                    reject="timeout",
                    detail="the candidate's tests did not finish in time: `head` is never advanced",
                ),
            },
            n_calls=4,
            previews=(
                "Graph(...).shortest_path_lengths('a') -> {'a': 0, 'b': 1, 'c': 2}",
                "Graph(...).shortest_path_lengths('x') -> {'x': 0}",
                "Graph(...).shortest_path_lengths((0, 0)) -> {(0, 0): 0, (1, 0): 1, …3600 keys}",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.aggregate",
            qualname="total_by_category",
            doc="Grouped totals.",
            source=_TOTALS,
            fate="accepted",
            heuristic=0.81,
            heuristic_reasons=("nested loops", "list membership test inside a loop"),
            potential="medium",
            testability="high",
            score=0.52,
            reasons=(
                "one pass over all rows per category: O(rows × categories)",
                "gains depend on how many categories real data has",
            ),
            est_call_s=0.0108,
            drafts=(Draft(_TOTALS_TESTS),),
            rewrites={
                "defaultdict": _rw(
                    '''\
def total_by_category(rows):
    """Sum ``row["amount"]`` per ``row["category"]``, categories in first-seen order."""
    totals = defaultdict(int)
    for row in rows:
        totals[row["category"]] += row["amount"]
    return dict(totals)
''',
                    "single pass with collections.defaultdict",
                    "One pass over rows; insertion order gives first-seen category order.",
                    "from collections import defaultdict",
                ),
                "get": _rw(
                    '''\
def total_by_category(rows):
    """Sum ``row["amount"]`` per ``row["category"]``, categories in first-seen order."""
    totals = {}
    for row in rows:
        category = row["category"]
        totals[category] = totals.get(category, 0) + row["amount"]
    return totals
''',
                    "single pass with dict.get",
                    "One pass, no imports; dict order is first-seen order.",
                ),
                "fromkeys": _rw(
                    '''\
def total_by_category(rows):
    """Sum ``row["amount"]`` per ``row["category"]``, categories in first-seen order."""
    totals = dict.fromkeys(row["category"] for row in rows)
    for category in totals:
        totals[category] = sum(row["amount"] for row in rows if row["category"] == category)
    return totals
''',
                    "dict.fromkeys for the categories, sum() per category",
                    "Replaces the list-membership scan; per-category passes remain.",
                ),
            },
            plans={
                "A": FakePlan("defaultdict", delta=-64.3),
                "B": FakePlan("get", delta=-58.8),
                "C": FakePlan("fromkeys", delta=-9.7),
            },
            n_calls=9,
            previews=(
                "total_by_category([{'category': 'food', 'amount': 12}, …3 items]) -> {'food': 42, 'rent': 800}",
                "total_by_category([]) -> {}",
                "total_by_category([{'category': 'c0', 'amount': 0}, …10000 items]) -> {'c0': 1245000, …40 keys}",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.series",
            qualname="moving_average",
            doc="Time series helpers.",
            source=_MOVING,
            fate="reverted",
            heuristic=0.52,
            heuristic_reasons=("slice + sum inside a loop",),
            potential="medium",
            testability="high",
            score=0.49,
            reasons=(
                "re-sums every window: O(n·w); a running sum is O(n)",
                "float inputs: a running sum can drift from the per-window sums",
            ),
            est_call_s=0.0262,
            drafts=(Draft(_MOVING_TESTS),),
            rewrites={
                "running_sum": _rw(
                    '''\
def moving_average(values, window):
    """Means of each full ``window``-sized slice of ``values``."""
    if window <= 0:
        raise ValueError("window must be positive")
    if window > len(values):
        return []
    total = sum(values[:window])
    averages = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        averages.append(total / window)
    return averages
''',
                    "running window sum",
                    "Add the entering value and subtract the leaving one: O(n).",
                ),
                "prefix": _rw(
                    '''\
def moving_average(values, window):
    """Means of each full ``window``-sized slice of ``values``."""
    if window <= 0:
        raise ValueError("window must be positive")
    prefix = [0, *itertools.accumulate(values)]
    return [(prefix[i + window] - prefix[i]) / window for i in range(len(values) - window + 1)]
''',
                    "prefix sums with itertools.accumulate",
                    "Each window sum is a difference of two prefix sums.",
                    "import itertools",
                ),
                "off_by_one": _rw(
                    '''\
def moving_average(values, window):
    """Means of each full ``window``-sized slice of ``values``."""
    if window <= 0:
        raise ValueError("window must be positive")
    if window >= len(values):
        return []
    total = sum(values[:window])
    averages = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        averages.append(total / window)
    return averages
''',
                    "running sum with an early exit for short inputs",
                    "O(n) running sum; inputs shorter than the window return early.",
                ),
            },
            plans={
                "A": FakePlan("running_sum", delta=-83.1),
                "B": FakePlan("prefix", delta=-80.2),
                "C": FakePlan(
                    "off_by_one",
                    reject="tests_failed",
                    failing="test_window_equal_to_length",
                    message="assert [] == [2.0]",
                ),
            },
            n_calls=412,
            raises=True,
            previews=(
                "moving_average([1, 2, 3, 4, 5], 2) -> [1.5, 2.5, 3.5, 4.5]",
                "moving_average([1, 2, 3], 0) raised ValueError('window must be positive')",
                "moving_average([0.1, 0.2, 0.3, 0.4], 2) -> [0.15000000000000002, 0.25, 0.35]",
            ),
            merge_mismatches=(
                Mismatch(
                    sample_idx=37,
                    kind="return",
                    path="[18]",
                    expected="0.4875",
                    actual="0.48750000000000004",
                ),
                Mismatch(
                    sample_idx=211,
                    kind="return",
                    path="[1203]",
                    expected="12.06",
                    actual="12.059999999999999",
                ),
                Mismatch(
                    sample_idx=388,
                    kind="return",
                    path="[4410]",
                    expected="-0.73",
                    actual="-0.7300000000000001",
                ),
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="textkit.fields",
            qualname="join_fields",
            doc="Delimited records.",
            source=_JOIN,
            fate="no_significant_win",
            heuristic=0.22,
            heuristic_reasons=("append inside a loop",),
            potential="low",
            testability="high",
            score=0.58,
            reasons=("already linear; at best a small constant-factor win",),
            est_call_s=2.4e-5,
            drafts=(Draft(_JOIN_TESTS),),
            rewrites={
                "map_str": _rw(
                    '''\
def join_fields(fields, sep=","):
    """Join ``fields`` with ``sep``, converting each field with ``str``."""
    return sep.join(map(str, fields))
''',
                    "str.join over map(str, ...)",
                    "Skips the intermediate list and its append calls.",
                ),
                "listcomp": _rw(
                    '''\
def join_fields(fields, sep=","):
    """Join ``fields`` with ``sep``, converting each field with ``str``."""
    return sep.join([str(field) for field in fields])
''',
                    "list comprehension",
                    "A comprehension avoids the bound-method call per field.",
                ),
                "comment_only": _rw(
                    '''\
def join_fields(fields, sep=","):
    """Join ``fields`` with ``sep``, converting each field with ``str``."""
    # already linear; nothing to gain
    parts = []
    for field in fields:
        parts.append(str(field))
    return sep.join(parts)
''',
                    "leave as is",
                    "The function is already linear and allocation-light.",
                ),
            },
            plans={
                "A": FakePlan("map_str", delta=-4.2, sigma=0.03),
                "B": FakePlan("listcomp", delta=-2.6, sigma=0.06),
                "C": FakePlan(
                    "comment_only",
                    reject="identical",
                    problems=("the candidate's AST is identical to the original's",),
                ),
            },
            n_calls=6,
            previews=(
                "join_fields(['a', 1, 2.5]) -> 'a,1,2.5'",
                "join_fields(['x', 'y'], sep='|') -> 'x|y'",
                "join_fields([0, 1, 2, 3, …200 items]) -> '0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,1…'",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="algos.search",
            qualname="index_of_sorted",
            doc="Searching sorted sequences.",
            source=_SEARCH,
            fate="no_significant_win",
            heuristic=0.28,
            heuristic_reasons=("while loop with index arithmetic",),
            potential="low",
            testability="high",
            score=0.33,
            reasons=("already O(log n); bisect only saves interpreter overhead",),
            est_call_s=3.1e-6,
            drafts=(Draft(_SEARCH_TESTS),),
            rewrites={
                "bisect_left": _rw(
                    '''\
def index_of_sorted(items, target):
    """Index of ``target`` in the ascending list ``items``, or -1."""
    i = bisect_left(items, target)
    if i < len(items) and items[i] == target:
        return i
    return -1
''',
                    "bisect.bisect_left",
                    "The binary search runs in C.",
                    "from bisect import bisect_left",
                ),
                "bisect_module": _rw(
                    '''\
def index_of_sorted(items, target):
    """Index of ``target`` in the ascending list ``items``, or -1."""
    i = bisect.bisect_left(items, target)
    return i if i != len(items) and items[i] == target else -1
''',
                    "bisect module, conditional expression",
                    "C binary search and a single return.",
                    "import bisect",
                ),
                "step_back": _rw(
                    '''\
def index_of_sorted(items, target):
    """Index of ``target`` in the ascending list ``items``, or -1."""
    i = bisect.bisect_right(items, target) - 1
    return i if items[i] == target else -1
''',
                    "bisect_right, then step back one",
                    "The last position <= target is the only place target can be.",
                    "import bisect",
                ),
            },
            plans={
                "A": FakePlan("bisect_left", delta=-4.6, sigma=0.025),
                "B": FakePlan("bisect_module", delta=-2.9, sigma=0.06),
                "C": FakePlan(
                    "step_back",
                    reject="tests_failed",
                    failing="test_missing_returns_minus_one",
                    message="IndexError: list index out of range",
                ),
            },
            n_calls=2867,
            previews=(
                "index_of_sorted([1, 3, 5, 7, 9], 5) -> 2",
                "index_of_sorted([1, 3, 5], 4) -> -1",
                "index_of_sorted([0, 2, 4, 6, …10000 items], 14) -> 7",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.stats",
            qualname="zscore",
            doc="Descriptive statistics.",
            source=_ZSCORE,
            fate="no_significant_win",
            heuristic=0.30,
            heuristic_reasons=("generator expression inside sum()",),
            potential="low",
            testability="high",
            score=0.27,
            reasons=("three linear passes; little left to gain",),
            est_call_s=0.0018,
            drafts=(Draft(_ZSCORE_TESTS),),
            rewrites={
                "list_sum": _rw(
                    '''\
def zscore(values):
    """Standard scores of ``values`` (population standard deviation)."""
    n = len(values)
    mean = sum(values) / n
    variance = sum([(v - mean) ** 2 for v in values]) / n
    std = variance ** 0.5
    return [(v - mean) / std for v in values]
''',
                    "list instead of generator inside sum()",
                    "sum() over a list avoids generator resumption overhead.",
                ),
                "deviations": _rw(
                    '''\
def zscore(values):
    """Standard scores of ``values`` (population standard deviation)."""
    n = len(values)
    mean = sum(values) / n
    deviations = [v - mean for v in values]
    std = (sum(d * d for d in deviations) / n) ** 0.5
    return [d / std for d in deviations]
''',
                    "compute deviations once",
                    "Each deviation is computed once and reused.",
                ),
                "inline": _rw(
                    '''\
def zscore(values):
    """Standard scores of ``values`` (population standard deviation)."""
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    return [(v - mean) / std for v in values]
''',
                    "inline the temporaries",
                    "Fewer local variables.",
                ),
            },
            plans={
                "A": FakePlan("list_sum", delta=-3.4, sigma=0.05),
                "B": FakePlan("deviations", delta=-4.4, sigma=0.025),
                "C": FakePlan("inline", delta=0.6, sigma=0.05),
            },
            n_calls=5,
            previews=(
                "zscore([2, 4, 4, 4, 5, 5, 7, 9]) -> [-1.5, -0.5, -0.5, -0.5, 0.0, 0.0, 1.0, 2.0]",
                "zscore([1.5, 2.5, 10.0]) -> [-0.8320502943378437, -0.5547001962252291, 1.386750490563073]",
                "zscore([0.0, 91.9, 83.8, …5000 items]) -> [-1.7303…, 1.4524…, …5000 items]",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.rank",
            qualname="top_k_inplace",
            doc="Ranking helpers.",
            source=_TOPK,
            fate="all_rejected",
            heuristic=0.12,
            heuristic_reasons=("short function",),
            potential="medium",
            testability="medium",
            score=0.74,
            reasons=(
                "sorts the whole list to take k items; heapq.nlargest is O(n log k)",
                "mutates its argument: a rewrite must keep the in-place sort",
            ),
            est_call_s=0.0075,
            drafts=(Draft(_TOPK_TESTS),),
            rewrites={
                "nlargest": _rw(
                    '''\
def top_k_inplace(values, k):
    """The ``k`` largest values, descending. Sorts ``values`` in place."""
    return heapq.nlargest(k, values)
''',
                    "heapq.nlargest",
                    "O(n log k) selection instead of a full O(n log n) sort.",
                    "import heapq",
                ),
                "nlargest_rest": _rw(
                    '''\
def top_k_inplace(values, k):
    """The ``k`` largest values, descending. Sorts ``values`` in place."""
    top = heapq.nlargest(k, values)
    rest = [v for v in values if v not in top]
    values[:] = top + rest
    return top
''',
                    "heapq.nlargest, written back into values",
                    "Keeps the selection cheap and puts the top k at the front of `values`.",
                    "import heapq",
                ),
                "slice_negative_k": _rw(
                    '''\
def top_k_inplace(values, k):
    """The ``k`` largest values, descending. Sorts ``values`` in place."""
    return sorted(values)[-k:][::-1]
''',
                    "ascending sort and a negative slice",
                    "Take the last k of an ascending sort and reverse them.",
                ),
                "sorted_copy": _rw(
                    '''\
def top_k_inplace(values, k):
    """The ``k`` largest values, descending. Sorts ``values`` in place."""
    ranked = sorted(values, reverse=True)
    return ranked[:k]
''',
                    "sorted() copy",
                    "Same sort, without touching the caller's list.",
                ),
            },
            plans={
                "A": FakePlan(
                    "nlargest",
                    reject="differential_mismatch",
                    mismatch=("args[0]", "[9, 7, 5, 3, 1]", "[3, 9, 1, 7, 5]"),
                    repair=FakePlan(
                        "nlargest_rest",
                        reject="differential_mismatch",
                        mismatch=("args[0]", "[9, 7, 5, 3, 1]", "[9, 7, 3, 1, 5]"),
                        diagnosis="Callers rely on `values` being sorted after the call; "
                        "the top k are now written back to the front of the list.",
                    ),
                ),
                "B": FakePlan(
                    "slice_negative_k",
                    reject="tests_failed",
                    failing="test_k_zero_returns_empty",
                    message="assert [8, 4, 2] == []",
                ),
                "C": FakePlan(
                    "sorted_copy",
                    reject="differential_mismatch",
                    mismatch=("args[0]", "[9, 7, 5, 3, 1]", "[3, 9, 1, 7, 5]"),
                ),
            },
            n_calls=6,
            mutates_args=True,
            previews=(
                "top_k_inplace([3, 9, 1, 7, 5], 2) -> [9, 7]  (args[0] -> [9, 7, 5, 3, 1])",
                "top_k_inplace([4, 2, 8], 0) -> []  (args[0] -> [8, 4, 2])",
                "top_k_inplace([0, 7919, 15838, …20000 items], 10) -> [20010, 20009, …10 items]",
            ),
        )
    )

    fns.append(
        FakeFunction(
            module="textkit.normalize",
            qualname="normalize_whitespace",
            doc="Whitespace normalisation.",
            source=_NORMALIZE,
            fate="skipped_untestable",
            heuristic=0.62,
            heuristic_reasons=("string concatenation inside a loop",),
            potential="medium",
            testability="low",
            score=0.24,
            reasons=(
                "`out +=` per character; ' '.join(text.split()) is the idiom",
                "its notion of whitespace is narrower than str.split()'s",
            ),
            est_call_s=0.0009,
            drafts=(
                Draft(
                    _NORMALIZE_TESTS.replace("{name}", "non_breaking_space").replace("{ch}", r" "),
                    failing="test_non_breaking_space",
                    message=r"assert 'a\xa0b' == 'a b'",
                ),
                Draft(
                    _NORMALIZE_TESTS.replace("{name}", "form_feed").replace("{ch}", r"\f"),
                    failing="test_form_feed",
                    message=r"assert 'a\x0cb' == 'a b'",
                    diagnosis="Dropped the non-breaking-space case; "
                    "checking another whitespace character instead.",
                ),
                Draft(
                    _NORMALIZE_TESTS.replace("{name}", "vertical_tab").replace("{ch}", r"\v"),
                    failing="test_vertical_tab",
                    message=r"assert 'a\x0bb' == 'a b'",
                    diagnosis="Form feed is not collapsed either; trying vertical tab.",
                ),
            ),
            n_calls=4,
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.io_free",
            qualname="parse_records",
            doc="Record parsing.",
            source=_PARSE,
            imports=("import time",),
            fate="skipped_capture",
            heuristic=0.41,
            heuristic_reasons=("nested loops", "calls time.time()"),
            potential="low",
            testability="low",
            score=0.19,
            reasons=("stamps each record with time.time(): outputs are not reproducible",),
            est_call_s=0.0041,
            drafts=(Draft(_PARSE_TESTS),),
            n_calls=5,
            previews=(
                "parse_records(['a=1;b=2']) -> [{'a': '1', 'b': '2', '_parsed_at': 1767225600.104}]",
                "parse_records(['a=1;b=2']) -> [{'a': '1', 'b': '2', '_parsed_at': 1767225600.231}]",
            ),
            capture_problem="two identical calls returned different values at [0]['_parsed_at']",
        )
    )

    fns.append(
        FakeFunction(
            module="datakit.rates",
            qualname="fetch_exchange_rates",
            doc="Exchange rates.",
            source=_RATES,
            imports=("import json", "import urllib.request"),
            fate="not_selectable",
            heuristic=0.37,
            heuristic_reasons=("network I/O",),
            potential="none",
            testability="none",
            score=0.0,
            reasons=("performs network I/O; cannot be benchmarked in the sandbox",),
            skip_reason="io: network call (urllib.request)",
        )
    )

    fns.append(
        FakeFunction(
            module="algos.walk",
            qualname="random_walk",
            doc="Random walks.",
            source=_WALK,
            imports=("import random",),
            fate="not_selectable",
            heuristic=0.55,
            heuristic_reasons=("loop with append",),
            potential="none",
            testability="none",
            score=0.0,
            reasons=("uses the global random generator; results are not reproducible",),
            skip_reason="nondeterministic: random without a seed",
        )
    )
    return fns


CATALOG: dict[str, FakeFunction] = {fn.function_id: fn for fn in _build_catalog()}
"""Every function of the fake repository, by function id."""

DEMO_SELECTION: tuple[str, ...] = (
    "textkit.dedupe:dedupe_preserve_order",
    "algos.primes:primes_below",
    "algos.pairs:has_pair_with_sum",
    "textkit.freq:word_frequencies",
    "datakit.rank:top_k_inplace",
    "algos.strings:levenshtein",
    "textkit.fields:join_fields",
    "algos.graph:Graph.shortest_path_lengths",
)
SHORT_SELECTION: tuple[str, ...] = (
    "textkit.dedupe:dedupe_preserve_order",
    "datakit.rank:top_k_inplace",
    "textkit.fields:join_fields",
)

_MODULE_FILES = sorted({fn.file for fn in CATALOG.values()})
_PACKAGES = sorted({fn.module.split(".")[0] for fn in CATALOG.values()})
_N_SOURCE_FILES = len(_MODULE_FILES) + len(_PACKAGES)  # + one __init__.py per package
_N_FUNCTIONS = len(CATALOG) + 2  # + Graph.__init__ and Graph.add_edge
_REPO_TESTS = 14  # tests in the repository's own suite

_DEFAULT_POWER = PowerInfo(
    power_source="tdp_estimate",
    badge="estimated",
    method="codecarbon_model",
    cpu_model="Apple M3 Pro",
    tdp_w=30.0,
    cpu_count=12,
    p_core_w=2.5,
    p_ram_w=0.4,
    grid=GridInfo(country_iso="WORLD", kg_per_kwh=0.475, source="codecarbon world average"),
    notes=["estimated from CodeCarbon's TDP model × measured CPU time"],
)

_PRICES_PER_MTOK = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0)}
_SYSTEM_TOKENS: dict[str, int] = {
    "triage": 1850,
    "tests": 2420,
    "tests_repair": 2420,
    "rewrite": 2160,
    "rewrite_repair": 2160,
}

# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _t_crit(df: float) -> float:
    return float(student_t.ppf(0.975, df)) if df > 0 else 0.0


def _interval(xs: list[float]) -> Interval:
    m, s = _mean(xs), _std(xs)
    half = _t_crit(len(xs) - 1) * s / math.sqrt(len(xs))
    return Interval(mean=m, ci_low=m - half, ci_high=m + half, std=s)


def _std_normal(rng: random.Random, n: int) -> list[float]:
    """n standard-normal draws, standardized to mean 0 / sample std 1."""
    z = [rng.gauss(0.0, 1.0) for _ in range(n)]
    if n < 2:
        return [0.0] * n
    m, s = _mean(z), _std(z)
    return [(x - m) / s for x in z] if s > 0 else [0.0] * n


def _measure(
    rng: random.Random, *, cpu_s: float, sigma: float, n: int, calls: int, power: PowerInfo
) -> tuple[MeasureStats, list[float]]:
    """One bench arm under the energy model; also returns log(g) per trial."""
    z = _std_normal(rng, n)
    w = _std_normal(rng, n)
    cpu = [cpu_s * math.exp(sigma * zi) for zi in z]
    wall = [c * 1.04 * math.exp(0.01 * wi) for c, wi in zip(cpu, w, strict=True)]
    k_cpu = [c * power.p_core_w / 3.6e6 for c in cpu]
    k_ram = [x * power.p_ram_w / 3.6e6 for x in wall]
    kwh = [a + b for a, b in zip(k_cpu, k_ram, strict=True)]
    g = [k * power.grid.kg_per_kwh * 1000.0 for k in kwh]
    stats = MeasureStats(
        g_per_call=_interval(g),
        kwh_per_call=_interval(kwh),
        kwh_cpu_per_call=_mean(k_cpu),
        kwh_ram_per_call=_mean(k_ram),
        cpu_s_per_call=_interval(cpu),
        wall_s_per_call=_interval(wall),
        n_trials=n,
        calls_per_trial=calls,
        trials_g=g,
    )
    return stats, [math.log(x) for x in g]


@dataclass(frozen=True)
class _Comparison:
    delta_pct: float
    ci: CI
    p_value: float


def _compare(
    orig: MeasureStats, orig_log: list[float], cand: MeasureStats, cand_log: list[float]
) -> _Comparison:
    """Delta of mean g/call with a 95% CI and a one-sided Welch t on log g."""
    n_o, n_c = len(orig_log), len(cand_log)
    ratio = cand.g_per_call.mean / orig.g_per_call.mean
    va = _std(orig_log) ** 2 / n_o
    vb = _std(cand_log) ** 2 / n_c
    se = math.sqrt(va + vb)
    diff = _mean(cand_log) - _mean(orig_log)
    if se == 0:
        p = 0.0 if diff < 0 else 1.0
        t = 0.0
    else:
        df = (va + vb) ** 2 / (
            (va**2 / (n_o - 1) if n_o > 1 else 0) + (vb**2 / (n_c - 1) if n_c > 1 else 0)
        )
        p = float(student_t.cdf(diff / se, df))
        t = _t_crit(df)
    lo = (ratio * math.exp(-t * se) - 1) * 100
    hi = (ratio * math.exp(t * se) - 1) * 100
    return _Comparison(
        delta_pct=round((ratio - 1) * 100, 2),
        ci=CI(lo=math.floor(lo * 100) / 100, hi=math.ceil(hi * 100) / 100),
        p_value=p,
    )


def _holm(p_values: dict[str, float]) -> dict[str, float]:
    m = len(p_values)
    out: dict[str, float] = {}
    running = 0.0
    for j, (cid, p) in enumerate(sorted(p_values.items(), key=lambda kv: (kv[1], kv[0]))):
        running = max(running, min(1.0, (m - j) * p))
        out[cid] = running
    return out


def _hex(*parts: object, n: int = 40) -> str:
    return hashlib.sha1(":".join(map(str, parts)).encode()).hexdigest()[:n]


def _tb_tail(code: str, path: str, test: str, message: str) -> str:
    """A pytest-style traceback tail pointing at the first assert of ``test``."""
    head, sep, _ = message.partition(":")
    exc = head if sep and head.isidentifier() and head.endswith("Error") else "AssertionError"
    lines = code.splitlines()
    for node in ast.parse(code).body:
        if isinstance(node, ast.FunctionDef) and node.name == test:
            target = next((n for n in ast.walk(node) if isinstance(n, ast.Assert)), node)
            src = lines[target.lineno - 1]
            return (
                f"    def {test}():\n>{src[1:]}\nE       {message}\n\n{path}:{target.lineno}: {exc}"
            )
    return f"E       {message}"


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


class FakePipeline:
    """A scripted stand-in for the real pipeline: ``await FakePipeline()(ctx)``.

    One instance serves any number of runs (all per-run state lives in
    ``_FakeRun``). ``speed`` divides every sleep; >= 1000 means no waiting.
    The same ``seed`` and scenario give the same events, ``ts`` and durations aside.
    """

    def __init__(self, speed: float = 1.0, *, scenario: Scenario = "demo", seed: int = 0):
        if not speed > 0:
            raise ValueError(f"speed must be > 0, got {speed!r}")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
        self.speed = float(speed)
        self.scenario: Scenario = scenario
        self.seed = seed

    async def __call__(self, ctx: RunContext) -> None:
        await _FakeRun(self, ctx).run()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0 if self.speed >= 1000 else max(0.0, seconds) / self.speed)

    def __repr__(self) -> str:
        return f"FakePipeline(speed={self.speed:g}, scenario={self.scenario!r}, seed={self.seed})"


@dataclass
class _Candidate:
    cid: CandidateId
    attempt: int = 0
    stats: BenchStats | None = None
    reject: RejectReason | None = None
    detail: str = ""
    diff: str = ""


class _Bench:
    """The per-function bench: one measurement at a time, baseline first."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.waiting = 0
        self.ready = asyncio.Event()
        self.original: MeasureStats | None = None
        self.calls_per_trial = 1


class _FakeRun:
    def __init__(self, pipe: FakePipeline, ctx: RunContext):
        self.pipe = pipe
        self.ctx = ctx
        self.s = ctx.settings
        self.power = ctx.detail.power or _DEFAULT_POWER
        self.warm_stages: set[str] = set()
        self.merged: list[tuple[str, str]] = []  # (function_id, diff), merge order
        self.suite_tests = _REPO_TESTS
        self.trunk_sha = _hex(pipe.seed, "trunk")

    # -- helpers -----------------------------------------------------------------

    def rng(self, *parts: object) -> random.Random:
        return random.Random(":".join(map(str, (self.pipe.seed, *parts))))

    async def sleep(self, nominal: float, rng: random.Random | None = None) -> None:
        await self.pipe.sleep(nominal * (rng.uniform(0.85, 1.15) if rng else 1.0))

    def usage(
        self,
        stage: LlmStage,
        *,
        label: str,
        nominal_s: float,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
        stop_reason: str = "end_turn",
    ) -> None:
        """Emit ``llm.usage`` for one (fake) model call."""
        cfg = self.s.stages.get(stage)
        rng = self.rng(function_id or "run", "usage", label)
        system = _SYSTEM_TOKENS[stage]
        cache_write = 0 if stage in self.warm_stages else system
        cache_read = system - cache_write
        self.warm_stages.add(stage)
        if stage == "triage":
            inp, out = rng.randint(5200, 6400), rng.randint(1400, 1900)
        elif stage.startswith("tests"):
            inp, out = rng.randint(1100, 1900), rng.randint(450, 900)
        else:
            inp, out = rng.randint(900, 1600), rng.randint(280, 720)
        if stop_reason == "max_tokens":
            out = cfg.max_tokens
        price_in, price_out = next(
            (v for k, v in _PRICES_PER_MTOK.items() if k in cfg.model), _PRICES_PER_MTOK["haiku"]
        )
        cost = (inp + 0.1 * cache_read + 1.25 * cache_write) * price_in / 1e6
        cost += out * price_out / 1e6
        cost = round(cost, 8)
        total = self.ctx.add_llm_cost(cost)
        self.ctx.emit(
            "llm.usage",
            LlmUsageData(
                stage=stage,
                model=cfg.model,
                cassette="off",
                input_tokens=inp,
                output_tokens=out,
                cache_creation_input_tokens=cache_write,
                cache_read_input_tokens=cache_read,
                cost_usd=cost,
                run_cost_usd=round(total, 8),
                latency_ms=int(nominal_s * 1000),
                stop_reason=stop_reason,
            ),
            function_id=function_id,
            candidate_id=candidate_id,
            attempt=attempt,
        )

    # -- run ---------------------------------------------------------------------

    async def run(self) -> None:
        ctx = self.ctx
        if ctx.detail.power is None:
            ctx.emit("run.power.detected", RunPowerDetectedData(power=self.power))
        ctx.transition("cloning")
        await self.clone()
        ctx.transition("installing")
        await self.install()
        ctx.transition("discovering")
        await self.discover()
        ctx.transition("triaging")
        preselected = await self.triage()
        selected = await ctx.confirm_selection(preselected)
        ids = list(dict.fromkeys(selected))  # a duplicate id would start a function twice
        for index, fid in enumerate(ids):
            await self.function(fid, index, len(ids))
        ctx.transition("finalizing")
        await self.artifacts()

    async def clone(self) -> None:
        ctx = self.ctx
        rng = self.rng("clone")
        sha = _hex(self.pipe.seed, "head")
        async with ctx.step(
            "run.clone.started", RunCloneStartedData(url=ctx.source.url, ref=ctx.source.ref)
        ) as st:
            if ctx.source.url:
                ctx.log("clone", [f"Cloning into 'repo'... ({ctx.source.url})"])
            else:
                ctx.log("clone", ["Copying the bundled demo repository into 'repo'"])
            await self.sleep(1.4, rng)
            ctx.log(
                "clone",
                [
                    "remote: Enumerating objects: 64, done.",
                    "Receiving objects: 100% (64/64), 21.37 KiB | 1.9 MiB/s, done.",
                    "Resolving deltas: 100% (17/17), done.",
                ],
            )
            await self.sleep(1.2, rng)
            ctx.log("clone", [f"HEAD is now at {sha[:7]} datakit: add rank helpers"])
            st.ok(
                commit_sha=sha,
                n_files=_N_SOURCE_FILES + 7,
                n_py_files=_N_SOURCE_FILES + 2,
                size_bytes=48_213,
            )

    async def install(self) -> None:
        ctx = self.ctx
        rng = self.rng("env")
        err: EnvError | None = None
        async with ctx.step(
            "run.env.started",
            RunEnvStartedData(python_request="3.12", dependency_sources=["pyproject.toml"]),
        ) as st:
            ctx.log(
                "env",
                ["Using CPython 3.12.7", "Creating virtual environment at: .venv"],
            )
            await self.sleep(1.6, rng)
            if self.pipe.scenario == "fail_env":
                ctx.log(
                    "env",
                    [
                        "  × No solution found when resolving dependencies:",
                        "  ╰─▶ Because numpy==1.21.6 has no wheels for CPython 3.12 and demo-utils "
                        "depends on numpy==1.21.6, demo-utils cannot be installed.",
                    ],
                    level="error",
                    stream="stderr",
                )
                err = EnvError(
                    "dependency resolution failed",
                    detail="numpy==1.21.6 has no wheels for CPython 3.12",
                )
                st.fail(err)
            else:
                packages = [
                    PackageInfo(name="iniconfig", version="2.0.0"),
                    PackageInfo(name="packaging", version="24.2"),
                    PackageInfo(name="pluggy", version="1.5.0"),
                    PackageInfo(name="pytest", version="8.3.4"),
                    PackageInfo(name="demo-utils", version="0.1.0"),
                ]
                ctx.log(
                    "env",
                    [
                        f"Resolved {len(packages)} packages in 214ms",
                        f"Installed {len(packages)} packages in 31ms",
                        *(f" + {p.name}=={p.version}" for p in packages),
                    ],
                )
                await self.sleep(2.6, rng)
                ctx.log("env", [f"import probe: {', '.join(_PACKAGES)} ok"])
                st.ok(
                    python_version="3.12.7",
                    packages=packages,
                    import_probe=ImportProbe(ok_modules=list(_PACKAGES)),
                )
        if err is not None:
            raise err

    async def discover(self) -> None:
        ctx = self.ctx
        async with ctx.step("run.discovery.started") as st:
            await self.sleep(1.5, self.rng("discovery"))
            ranked = sorted(CATALOG.values(), key=lambda f: (-f.heuristic, f.function_id))
            ctx.log(
                "orchestrator",
                [
                    f"scanned {_N_SOURCE_FILES} files: {_N_FUNCTIONS} functions, 2 test files",
                    f"{len(ranked)} functions ranked by static heuristics",
                ],
            )
            st.ok(
                n_files=_N_SOURCE_FILES,
                n_functions=_N_FUNCTIONS,
                n_test_files=2,
                import_roots=["."],
                heuristic_ranked=[f.triage_item(llm=False) for f in ranked],
            )

    def preselection(self) -> list[str]:
        if self.pipe.scenario == "short":
            return list(SHORT_SELECTION)
        return list(DEMO_SELECTION[: max(0, self.s.preselect)])

    async def triage(self) -> list[str]:
        ctx = self.ctx
        fns = sorted(CATALOG.values(), key=lambda f: (-f.score, f.function_id))
        fns = fns[: self.s.max_functions]
        preselected = [fid for fid in self.preselection() if fid in CATALOG]
        async with ctx.step(
            "run.triage.started", RunTriageStartedData(n_candidates=len(fns), llm=True)
        ) as st:
            ctx.log("llm", [f"triage: rating {len(fns)} functions in 1 request"])
            await self.sleep(3.5, self.rng("triage"))
            items = [f.triage_item(llm=True, preselected=f.function_id in preselected) for f in fns]
            skipped = sum(1 for f in fns if f.skip_reason)
            ctx.log(
                "llm",
                [f"triage: {len(preselected)} preselected, {skipped} not optimizable"],
            )
            st.ok(items=items, preselected=preselected, llm_used=True)
        self.usage("triage", label="triage", nominal_s=3.5)
        return preselected

    # -- one function --------------------------------------------------------------

    async def function(self, fid: str, index: int, total: int) -> None:
        ctx = self.ctx
        fn = CATALOG.get(fid)
        info = fn.info() if fn else _unknown_info(fid)
        async with ctx.function(info, index=index, total=total) as fs:
            ctx.log("orchestrator", [f"[{index + 1}/{total}] {fid}"], function_id=fid)
            if fn is None:
                fs.complete("failed", reason=f"{fid!r} is not a function discovery found")
            elif fn.skip_reason:
                fs.complete("failed", reason=f"not optimizable: {fn.skip_reason}")
            else:
                await self.optimize(fn, fs)

    async def optimize(self, fn: FakeFunction, fs: FunctionScope) -> None:
        tests = await self.write_tests(fn)
        if tests is None:
            fs.complete(
                "skipped_untestable",
                reason=f"generated tests still fail on the original after "
                f"{self.s.test_repairs} repairs",
            )
            return
        test_file, attempt = tests
        if not await self.capture(fn):
            fs.complete("skipped_capture", reason=f"capture: {fn.capture_problem}")
            return

        bench = _Bench()
        tasks = [
            asyncio.create_task(self.candidate(fn, cid, bench, test_file), name=f"fake-{cid}")
            for cid in CANDIDATES
        ]
        try:
            await asyncio.sleep(0)  # let the writes start before the baseline
            await self.baseline(fn, bench)
            results = list(await asyncio.gather(*tasks))
        finally:
            await _reap(tasks)

        winner, ranking = self.decide(fn, results)
        if winner is None:
            return self.no_winner(fs, results, ranking)
        await self.merge(fn, fs, winner, test_file)

    async def write_tests(self, fn: FakeFunction) -> tuple[TestFile, int] | None:
        """tests.write + tests.run (+ repairs); None if the tests never pass."""
        ctx, fid = self.ctx, fn.function_id
        failures: list[str] = []
        for attempt in range(self.s.test_repairs + 1):
            kind = "write" if attempt == 0 else "repair"
            rng = self.rng(fid, "tests", attempt)
            test_file = fn.test_file(attempt)
            async with ctx.step(
                "function.tests.write.started",
                TestsWriteStartedData(kind=kind, failures_in=failures),
                function_id=fid,
                attempt=attempt,
            ) as st:
                await self.sleep(2.4, rng)
                st.ok(test_file=test_file)
            self.usage(
                "tests" if attempt == 0 else "tests_repair",
                label=f"tests:{attempt}",
                nominal_s=2.4,
                function_id=fid,
                attempt=attempt,
            )
            draft = fn.draft(attempt)
            ok = await self.run_tests(fn, test_file, attempt, repeat=0, failing=draft)
            if ok:
                await self.run_tests(fn, test_file, attempt, repeat=1, failing=draft)
                return test_file, attempt
            failures = [f"{test_file.path}::{draft.failing}"]
        return None

    async def run_tests(
        self, fn: FakeFunction, tf: TestFile, attempt: int, *, repeat: int, failing: Draft
    ) -> bool:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "tests.run", attempt, repeat)
        async with ctx.step(
            "function.tests.run.started",
            TestsRunStartedData(repeat=repeat),
            function_id=fid,
            attempt=attempt,
        ) as st:
            await self.sleep(1.2 if repeat == 0 else 0.9, rng)
            result = _pytest_result(tf, rng, failing=failing.failing, message=failing.message)
            ctx.log(
                "pytest",
                _pytest_lines(tf, result),
                level="info" if result.exit_code == 0 else "warn",
                stream="stdout",
                function_id=fid,
                attempt=attempt,
            )
            if result.exit_code == 0:
                st.ok(result=result, flaky=False)
            else:
                st.fail(None, result=result, flaky=False)
        return result.exit_code == 0

    async def capture(self, fn: FakeFunction) -> bool:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "capture")
        n_kept = min(fn.n_calls, 512)
        async with ctx.step("function.capture.started", function_id=fid) as st:
            await self.sleep(0.8, rng)
            fields = {
                "n_calls": fn.n_calls,
                "n_kept": n_kept,
                "n_unpicklable": 0,
                "mutates_args": fn.mutates_args,
                "raises": fn.raises,
                "total_bytes": n_kept * rng.randint(180, 2400),
                "previews": list(fn.previews),
            }
            if fn.capture_problem:
                ctx.log(
                    "sandbox",
                    [f"capture: {fn.capture_problem}"],
                    level="warn",
                    function_id=fid,
                )
                st.fail(None, deterministic=False, **fields)
                return False
            ctx.log(
                "sandbox",
                [f"captured {fn.n_calls} calls ({n_kept} kept) while running the tests"],
                function_id=fid,
            )
            st.ok(deterministic=True, **fields)
        return True

    async def baseline(self, fn: FakeFunction, bench: _Bench) -> None:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "baseline")
        n = self.s.n_trials
        calls = max(1, round(self.s.trial_target_s / fn.est_call_s))
        async with bench.lock:
            async with ctx.step("function.baseline.started", function_id=fid) as st:
                est = fn.est_call_s * rng.uniform(0.93, 1.07)
                ctx.log(
                    "bench",
                    [f"calibration: 5 samples, ~{est * 1e3:.3g} ms/call -> {calls} calls/trial"],
                    function_id=fid,
                )
                await self.sleep(3.0, rng)
                original, _ = _measure(
                    rng, cpu_s=fn.est_call_s, sigma=0.04, n=n, calls=calls, power=self.power
                )
                g = original.trials_g
                cv = _std(g) / _mean(g) * 100
                ctx.log(
                    "bench",
                    [f"baseline: {n} trials × {calls} calls, cv {cv:.1f}%"],
                    function_id=fid,
                )
                st.ok(
                    calibration=Calibration(calls_per_trial=calls, est_call_s=est, n_samples=5),
                    original=original,
                    cv_pct=round(cv, 2),
                    power=self.power,
                )
        bench.original = original
        bench.calls_per_trial = calls
        bench.ready.set()

    # -- candidates ------------------------------------------------------------------

    async def candidate(
        self, fn: FakeFunction, cid: CandidateId, bench: _Bench, tf: TestFile
    ) -> _Candidate:
        ctx, fid = self.ctx, fn.function_id
        plan = fn.plans[cid]
        res = _Candidate(cid)
        attempt = 0
        while True:
            res.attempt = attempt
            code = await self.write_candidate(fn, cid, plan, attempt)
            if code is None:
                return self.reject(fn, res, "llm_error", plan.detail or "no code returned")
            res.diff = code.diff
            await self.check_candidate(fn, cid, plan, attempt, tf, code)
            if plan.reject is None:
                break
            if plan.repair is not None and attempt < self.s.candidate_repairs:
                plan, attempt = plan.repair, attempt + 1
                continue
            return self.reject(fn, res, plan.reject, _reject_detail(plan))

        await bench.ready.wait()
        position = bench.waiting
        bench.waiting += 1
        ctx.emit(
            "candidate.bench.queued",
            CandidateBenchQueuedData(position=position),
            function_id=fid,
            candidate_id=cid,
            attempt=attempt,
        )
        try:
            await bench.lock.acquire()
        finally:
            bench.waiting -= 1
        try:
            res.stats = await self.bench_candidate(fn, cid, plan, attempt, bench)
        finally:
            bench.lock.release()
        return res

    def reject(
        self, fn: FakeFunction, res: _Candidate, reason: RejectReason, detail: str
    ) -> _Candidate:
        res.reject, res.detail = reason, detail
        self.ctx.emit(
            "candidate.rejected",
            CandidateRejectedData(reason=reason, detail=detail),
            function_id=fn.function_id,
            candidate_id=res.cid,
            attempt=res.attempt,
        )
        self.ctx.log(
            "orchestrator",
            [f"{res.cid} rejected ({reason}): {detail}"],
            level="warn",
            function_id=fn.function_id,
            candidate_id=res.cid,
            attempt=res.attempt,
        )
        return res

    async def write_candidate(
        self, fn: FakeFunction, cid: CandidateId, plan: FakePlan, attempt: int
    ) -> CandidateCode | None:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "write", cid, attempt)
        nominal = {"A": 3.2, "B": 2.6, "C": 2.9}[cid] * (0.8 if attempt else 1.0)
        nominal *= rng.uniform(0.85, 1.15)
        stage: LlmStage = "rewrite" if attempt == 0 else "rewrite_repair"
        code: CandidateCode | None = None
        async with ctx.step(
            "candidate.write.started",
            CandidateWriteStartedData(
                kind="write" if attempt == 0 else "repair", strategy_hint=STRATEGY_HINTS[cid]
            ),
            function_id=fid,
            candidate_id=cid,
            attempt=attempt,
        ) as st:
            await self.pipe.sleep(nominal)
            if plan.rewrite is None:
                st.fail(
                    LlmError(
                        "the model's response was cut off at max_tokens",
                        detail="no complete function in the output",
                    )
                )
            else:
                rw = fn.rewrites[plan.rewrite]
                code = CandidateCode(
                    code=rw.code,
                    new_imports=list(rw.new_imports),
                    strategy=rw.strategy,
                    rationale=rw.rationale,
                    diff=fn.diff(plan.rewrite),
                    diagnosis=plan.diagnosis if attempt else "",
                )
                st.ok(candidate=code)
        self.usage(
            stage,
            label=f"{stage}:{cid}:{attempt}",
            nominal_s=nominal,
            function_id=fid,
            candidate_id=cid,
            attempt=attempt,
            stop_reason="max_tokens" if plan.rewrite is None else "end_turn",
        )
        return code

    async def check_candidate(
        self,
        fn: FakeFunction,
        cid: CandidateId,
        plan: FakePlan,
        attempt: int,
        tf: TestFile,
        code: CandidateCode,
    ) -> None:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "check", cid, attempt)
        scope = {"function_id": fid, "candidate_id": cid, "attempt": attempt}
        n_samples = min(fn.n_calls, 64)
        async with ctx.step("candidate.check.started", **scope) as st:  # type: ignore[arg-type]
            if plan.reject in ("syntax", "static_rule", "identical"):
                await self.sleep(0.3, rng)
                ctx.log("sandbox", [f"static: {p}" for p in plan.problems], level="warn", **scope)  # type: ignore[arg-type]
                st.fail(None, static=StaticCheck(ok=False, problems=list(plan.problems)))
                return
            static = StaticCheck(ok=True)
            if plan.reject == "timeout":
                await self.sleep(1.8, rng)
                limit = self.s.test_timeout_s
                ctx.log(
                    "pytest",
                    [f"Timeout: tests did not finish within {limit:g} s; process group killed"],
                    level="warn",
                    **scope,  # type: ignore[arg-type]
                )
                st.fail(StepTimeout(f"candidate tests exceeded {limit:g} s"), static=static)
                return
            await self.sleep(1.2, rng)
            if plan.reject == "tests_failed":
                result = _pytest_result(tf, rng, failing=plan.failing, message=plan.message)
                ctx.log("pytest", _pytest_lines(tf, result), level="warn", stream="stdout", **scope)  # type: ignore[arg-type]
                st.fail(None, static=static, tests=result)
                return
            result = _pytest_result(tf, rng)
            ctx.log("pytest", _pytest_lines(tf, result), stream="stdout", **scope)  # type: ignore[arg-type]
            if plan.reject == "differential_mismatch":
                assert plan.mismatch is not None
                path, expected, actual = plan.mismatch
                diff = DiffCheckResult(
                    ok=False,
                    n_samples=n_samples,
                    mismatches=[
                        Mismatch(
                            sample_idx=0,
                            kind="mutation",
                            path=path,
                            expected=expected,
                            actual=actual,
                        )
                    ],
                )
                ctx.log(
                    "sandbox",
                    [f"differential: sample 0: {path} expected {expected}, got {actual}"],
                    level="warn",
                    **scope,  # type: ignore[arg-type]
                )
                st.fail(None, static=static, tests=result, differential=diff)
                return
            diff = DiffCheckResult(
                ok=True, n_samples=n_samples, slowdown_ratio=round(1 + plan.delta / 100, 3)
            )
            ctx.log("sandbox", [f"differential: {n_samples}/{n_samples} samples match"], **scope)  # type: ignore[arg-type]
            st.ok(static=static, tests=result, differential=diff)

    async def bench_candidate(
        self, fn: FakeFunction, cid: CandidateId, plan: FakePlan, attempt: int, bench: _Bench
    ) -> BenchStats | None:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "bench", cid, attempt)
        n, calls = self.s.n_trials, bench.calls_per_trial
        scope = {"function_id": fid, "candidate_id": cid, "attempt": attempt}
        original, orig_log = _measure(
            rng, cpu_s=fn.est_call_s, sigma=plan.sigma, n=n, calls=calls, power=self.power
        )
        cand, cand_log = _measure(
            rng,
            cpu_s=fn.est_call_s * (1 + plan.delta / 100),
            sigma=plan.sigma,
            n=n,
            calls=calls,
            power=self.power,
        )
        cmp = _compare(original, orig_log, cand, cand_log)
        stats = BenchStats(
            original=original,
            candidate=cand,
            delta_pct=cmp.delta_pct,
            delta_ci_pct=cmp.ci,
            p_value=cmp.p_value,
            significant=_significant(cmp.p_value, cmp.ci, cmp.delta_pct, self.s),
            n_trials=n,
            calls_per_trial=calls,
            cpu_time_delta_pct=round(
                (cand.cpu_s_per_call.mean / original.cpu_s_per_call.mean - 1) * 100, 2
            ),
            sanity_ok=True,
            power=self.power,
            g_saved_per_1m_calls=(original.g_per_call.mean - cand.g_per_call.mean) * 1e6,
            kwh_saved_per_1m_calls=(original.kwh_per_call.mean - cand.kwh_per_call.mean) * 1e6,
        )
        async with ctx.step(
            "candidate.bench.started",
            CandidateBenchStartedData(calls_per_trial=calls, n_trials=n),
            **scope,  # type: ignore[arg-type]
        ) as st:
            chunks = 4
            for k in range(1, chunks + 1):
                await self.sleep(0.6, rng)
                done = n * k // chunks
                o = _mean(original.trials_g[:done] or [0.0]) / original.trials_g[0]
                c = _mean(cand.trials_g[:done] or [0.0]) / original.trials_g[0]
                ctx.log(
                    "bench",
                    [
                        f"{cid}: trials {done}/{n} (interleaved) · original "
                        f"{fn.est_call_s * o * 1e3:.3g} ms/call · candidate "
                        f"{fn.est_call_s * c * 1e3:.3g} ms/call"
                    ],
                    **scope,  # type: ignore[arg-type]
                )
            ctx.log(
                "bench",
                [
                    f"{cid}: Δ {_pct(stats.delta_pct)}% CO₂/call (95% CI "
                    f"{_pct(stats.delta_ci_pct.lo)}…{_pct(stats.delta_ci_pct.hi)}), "
                    f"{_fmt_p(stats.p_value)}"
                ],
                **scope,  # type: ignore[arg-type]
            )
            st.ok(stats=stats)
        return stats

    # -- decision + merge ------------------------------------------------------------

    def decide(
        self, fn: FakeFunction, results: list[_Candidate]
    ) -> tuple[_Candidate | None, list[RankingEntry]]:
        """Holm-correct the benched candidates, pick the largest significant cut, emit."""
        s = self.s
        benched = [r for r in results if r.stats is not None]
        p_holm = _holm({r.cid: r.stats.p_value for r in benched})  # type: ignore[union-attr]
        entries: dict[str, RankingEntry] = {}
        for r in benched:
            st = r.stats
            assert st is not None
            ph = p_holm[r.cid]
            entries[r.cid] = RankingEntry(
                candidate_id=r.cid,
                status="eligible",
                delta_pct=st.delta_pct,
                delta_ci_pct=st.delta_ci_pct,
                p_value=st.p_value,
                p_holm=ph,
                significant=_significant(ph, st.delta_ci_pct, st.delta_pct, s),
                reason=_why_not(ph, st.delta_ci_pct, st.delta_pct, s),
            )
        winner = min(
            (r for r in benched if entries[r.cid].significant),
            key=lambda r: (r.stats.delta_pct, r.cid),  # type: ignore[union-attr]
            default=None,
        )
        for r in benched:
            e = entries[r.cid]
            if r is winner:
                entries[r.cid] = e.model_copy(update={"reason": "largest significant reduction"})
            elif winner is not None and e.significant:
                entries[r.cid] = e.model_copy(
                    update={"reason": f"significant; {winner.cid} saves more"}
                )
            if r.stats is not None:
                r.stats = r.stats.model_copy(update={"p_holm": p_holm[r.cid]})
        ranking = sorted(entries.values(), key=lambda e: (e.delta_pct, e.candidate_id))
        ranking += [
            RankingEntry(candidate_id=r.cid, status="rejected", reason=f"{r.reject}: {r.detail}")
            for r in results
            if r.stats is None
        ]
        outcome = "winner" if winner else ("all_rejected" if not benched else "no_significant_win")
        self.ctx.emit(
            "function.decision",
            FunctionDecisionData(
                outcome=outcome,
                winner=winner.cid if winner else None,
                ranking=ranking,
                rule=DecisionRule(alpha=s.alpha, min_effect_pct=s.min_effect_pct),
            ),
            function_id=fn.function_id,
        )
        return winner, ranking

    def no_winner(
        self, fs: FunctionScope, results: list[_Candidate], ranking: list[RankingEntry]
    ) -> None:
        eligible = [e for e in ranking if e.status == "eligible"]
        if not eligible:
            parts = ", ".join(f"{r.cid} {r.reject}" for r in results)
            fs.complete("all_rejected", reason=f"all {len(results)} candidates rejected: {parts}")
            return
        best = eligible[0]
        assert best.delta_pct is not None and best.delta_ci_pct is not None
        fs.complete(
            "no_significant_win",
            reason=(
                f"best: {best.candidate_id} {_pct(best.delta_pct)}% (95% CI "
                f"{_pct(best.delta_ci_pct.lo)}…{_pct(best.delta_ci_pct.hi)}), "
                f"{_fmt_p(best.p_holm or 1.0, 'p_holm')}: {best.reason}"
            ),
        )

    async def merge(
        self, fn: FakeFunction, fs: FunctionScope, winner: _Candidate, tf: TestFile
    ) -> None:
        ctx, fid = self.ctx, fn.function_id
        rng = self.rng(fid, "merge")
        stats = winner.stats
        assert stats is not None
        n_samples = min(fn.n_calls, 512)
        suite_n = self.suite_tests + len(tf.test_names)
        suite = PytestResult(
            exit_code=0,
            passed=suite_n,
            failed=0,
            errors=0,
            skipped=0,
            duration_s=round(rng.uniform(0.6, 1.6), 2),
            output_tail=f"{suite_n} passed in {rng.uniform(0.6, 1.6):.2f}s",
        )
        headline = (
            f"{_pct(stats.delta_pct)}% CO₂/call (95% CI {_pct(stats.delta_ci_pct.lo)}…"
            f"{_pct(stats.delta_ci_pct.hi)}), {_fmt_p(stats.p_holm or stats.p_value, 'p_holm')}"
        )
        async with ctx.step("function.merge.started", function_id=fid) as st:
            ctx.log(
                "orchestrator",
                [f"merging {winner.cid} into the trunk; running the full suite"],
                function_id=fid,
            )
            await self.sleep(1.2, rng)
            ctx.log("pytest", [suite.output_tail], stream="stdout", function_id=fid)
            if fn.merge_mismatches:
                differential = DiffCheckResult(
                    ok=False, n_samples=n_samples, mismatches=list(fn.merge_mismatches)
                )
                why = (
                    f"{len(fn.merge_mismatches)} of {n_samples} captured calls differ on the "
                    "merged trunk (running-sum float drift)"
                )
                ctx.log(
                    "orchestrator",
                    [f"reverting {winner.cid}: {why}"],
                    level="warn",
                    function_id=fid,
                )
                st.fail(
                    ValidationFailed(why), reverted=True, suite=suite, differential=differential
                )
            else:
                sha = _hex(self.pipe.seed, fid, "merge")
                self.trunk_sha = sha
                self.suite_tests = suite_n
                d = ctx.paths.fn(fid)
                d.mkdir(parents=True, exist_ok=True)
                (d / "merge.diff").write_text(winner.diff, encoding="utf-8")
                self.merged.append((fid, winner.diff))
                ctx.log("orchestrator", [f"merged {winner.cid} as {sha[:7]}"], function_id=fid)
                st.ok(
                    suite=suite,
                    differential=DiffCheckResult(ok=True, n_samples=n_samples),
                    commit_sha=sha,
                    diff=winner.diff,
                )
        if fn.merge_mismatches:
            fs.complete(
                "reverted",
                winner=winner.cid,
                delta_pct=stats.delta_pct,
                delta_ci_pct=stats.delta_ci_pct,
                reason=f"{winner.cid} reverted after merge: {why}",
            )
            return
        fs.complete(
            "accepted",
            winner=winner.cid,
            delta_pct=stats.delta_pct,
            delta_ci_pct=stats.delta_ci_pct,
            g_saved_per_1m_calls=stats.g_saved_per_1m_calls,
            kwh_saved_per_1m_calls=stats.kwh_saved_per_1m_calls,
            diff=winner.diff,
            reason=f"{winner.cid}: {headline}",
        )

    # -- artifacts -------------------------------------------------------------------

    async def artifacts(self) -> None:
        ctx = self.ctx
        run_id = ctx.run_id
        base = f"/api/runs/{run_id}/artifacts"
        async with ctx.step("run.artifacts.started") as st:
            await self.sleep(1.0, self.rng("artifacts"))
            out = ctx.paths.out
            out.mkdir(parents=True, exist_ok=True)
            patch_text = "".join(diff for _, diff in self.merged)
            patch_ref = None
            if self.merged:
                patch_path = out / f"{run_id}.patch"
                patch_path.write_text(patch_text, encoding="utf-8")
                data = patch_path.read_bytes()
                patch_ref = PatchRef(
                    url=f"{base}/patch",
                    bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    files_changed=len(self.merged),
                )
            totals = ctx.projection.totals(duration_ms=0).model_dump(
                mode="json", exclude={"duration_ms"}
            )
            report = {
                "run_id": run_id,
                "functions": [f.model_dump(mode="json") for f in ctx.detail.functions],
                "totals": totals,
            }
            zip_path = out / f"{run_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                entries = [("netzero-report.json", json.dumps(report, indent=2, sort_keys=True))]
                if self.merged:
                    entries.insert(0, ("netzero.patch", patch_text))
                for name, text in entries:
                    zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o644 << 16
                    zf.writestr(zi, text)
            zdata = zip_path.read_bytes()
            ctx.log(
                "orchestrator",
                [
                    f"patch: {len(self.merged)} files changed"
                    if self.merged
                    else "patch: nothing merged",
                    f"report: {zip_path.name} ({len(zdata)} bytes)",
                ],
            )
            st.ok(
                patch=patch_ref,
                zip=ArtifactRef(
                    url=f"{base}/zip", bytes=len(zdata), sha256=hashlib.sha256(zdata).hexdigest()
                ),
                function_diffs=[
                    FunctionDiffRef(
                        function_id=fid, url=f"{base}/functions/{quote(fid, safe='')}/diff"
                    )
                    for fid, _ in self.merged
                ],
            )


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _significant(p: float, ci: CI, delta: float, s) -> bool:
    return p < s.alpha and ci.hi < 0 and delta <= -s.min_effect_pct


def _why_not(p: float, ci: CI, delta: float, s) -> str:
    if delta > -s.min_effect_pct:
        return f"below the {s.min_effect_pct:g}% minimum effect"
    if ci.hi >= 0:
        return "95% CI includes 0"
    if p >= s.alpha:
        return f"{_fmt_p(p, 'p_holm')} ≥ α={s.alpha:g}"
    return ""


def _reject_detail(plan: FakePlan) -> str:
    if plan.detail:
        return plan.detail
    if plan.reject == "tests_failed":
        return f"{plan.failing} failed: {plan.message}"
    if plan.reject == "differential_mismatch" and plan.mismatch:
        path, expected, actual = plan.mismatch
        return f"{path} after the call: expected {expected}, got {actual} (sample 0)"
    return "; ".join(plan.problems)


def _pytest_result(
    tf: TestFile, rng: random.Random, *, failing: str | None = None, message: str = ""
) -> PytestResult:
    n = len(tf.test_names)
    duration = round(rng.uniform(0.15, 0.9), 2)
    if failing is None:
        return PytestResult(
            exit_code=0,
            passed=n,
            failed=0,
            errors=0,
            skipped=0,
            duration_s=duration,
            output_tail=f"{tf.path} {'.' * n}  [100%]\n\n{n} passed in {duration:.2f}s",
        )
    nodeid = f"{tf.path}::{failing}"
    dots = "".join("F" if name == failing else "." for name in tf.test_names)
    return PytestResult(
        exit_code=1,
        passed=n - 1,
        failed=1,
        errors=0,
        skipped=0,
        duration_s=duration,
        failures=[
            PytestFailure(
                nodeid=nodeid,
                message=message,
                tb_tail=_tb_tail(tf.code, tf.path, failing, message),
            )
        ],
        output_tail=(
            f"{tf.path} {dots}  [100%]\n\nFAILED {nodeid} - {message}\n"
            f"1 failed, {n - 1} passed in {duration:.2f}s"
        ),
    )


def _pytest_lines(tf: TestFile, result: PytestResult) -> list[str]:
    failed = {f.nodeid.rsplit("::", 1)[-1] for f in result.failures}
    lines = [f"collected {len(tf.test_names)} items", ""]
    lines += [
        f"{tf.path}::{name} {'FAILED' if name in failed else 'PASSED'}" for name in tf.test_names
    ]
    return [*lines, "", *result.output_tail.splitlines()[-1:]]


def _unknown_info(fid: str) -> FunctionInfo:
    module, _, qualname = fid.rpartition(":")
    qualname = qualname or fid
    return FunctionInfo(
        function_id=fid,
        module=module,
        qualname=qualname,
        kind="function",
        file=module.replace(".", "/") + ".py" if module else "",
        line=0,
        end_line=0,
        loc=0,
        import_line="",
        call_hint="",
        source="",
    )


def expected_outcome(function_id: str) -> FunctionOutcome:
    """The outcome the fake scripts for ``function_id`` under default settings."""
    fn = CATALOG.get(function_id)
    if fn is None or fn.fate == "not_selectable":
        return "failed"
    return fn.fate  # type: ignore[return-value]
