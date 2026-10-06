"""Prompts for every LLM stage, built byte-for-byte the same on every run.

Byte stability is what lets cassettes match (``request_hash``) and the prompt
cache hit: system prompts are constants, nothing time- or path-dependent goes
in, and the three rewrite candidates share every block but the last, which
carries their strategy hint. ``cache_control`` sits on the last shared block,
so B and C (and every repair) can read what A wrote.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from netzero.events import CandidateId, PytestFailure
from netzero.llm.models import RewriteOut, RewriteRepairOut, TestFileOut, TestRepairOut, TriageOut
from netzero.pipeline.discovery import DiscoveredFunction
from netzero.pipeline.splice import find_function_node

CACHE_CONTROL = {"type": "ephemeral"}
EXCERPT_CHARS = 24_000
TRIAGE_SOURCE_CHARS = 1_500
MAX_SAMPLES = 12
SAMPLE_CHARS = 400
MAX_FAILURES = 10
FAILURE_CHARS = 1_500
PROBLEM_CHARS = 8_000

STRATEGY_HINTS: dict[CandidateId, str] = {
    "A": "algorithmic: lower the time complexity",
    "B": "builtins and stdlib: set/dict lookups, comprehensions, itertools, collections, "
    "str methods",
    "C": "vectorize with numpy if the module already imports it, otherwise a conservative "
    "micro-optimization",
}


@dataclass(frozen=True)
class Request[T: BaseModel]:
    """One API request: the conversation so far and the model its answer must fit."""

    system: str
    messages: list[dict[str, Any]]
    output: type[T]


@dataclass(frozen=True)
class Conversation:
    """A finished exchange, ending with the assistant's answer, for a repair to extend."""

    system: str
    messages: list[dict[str, Any]]


TRIAGE_SYSTEM = """\
You rate Python functions for an agent that rewrites functions so they use less CPU, \
and so less energy, while behaving exactly the same.

For every function give:
- potential: how much CPU time a faithful rewrite could save on realistic inputs.
  high: clear algorithmic waste (membership tests on lists inside loops, repeated work, \
quadratic string building, recomputation). medium: real gains from builtins, comprehensions \
or better data structures. low: already tight, or cheap anyway. none: nothing to gain \
(I/O bound, trivial, or delegates all work elsewhere).
- testability: how easily deterministic pytest tests can call it with plain data.
  high: a pure function of plain arguments. medium: needs a small object or a little setup. \
low: needs complex fixtures, global state or external resources. none: cannot be called in \
isolation.
- reason: at most 15 words.

Rate every function exactly once and copy each id exactly as given."""

TESTS_SYSTEM = """\
You write characterization tests for one Python function: its current behaviour is the \
specification, bugs included. Later an optimized rewrite must pass these tests, and the \
calls the tests make are captured and replayed against both versions, so the tests decide \
what "same behaviour" means.

Rules:
- One pytest file. Plain functions with plain assert; no unittest classes, no boilerplate, \
no if __name__ block.
- Import the function with exactly the import line you are given. Import only the standard \
library, pytest, and modules the function's own module already imports.
- Deterministic: no network, files, clock, environment, subprocesses or unseeded randomness. \
Build data with fixed literals, ranges or random.Random(42).
- Never mock, patch or monkeypatch the function under test or anything it calls.
- At least 3 tests that pin down behaviour: typical inputs, edge cases (empty, single item, \
duplicates, boundaries), and errors via pytest.raises where the function raises.
- Exactly one test decorated with @pytest.mark.nz_workload. It is the benchmark: it calls \
the function a few times on realistic, moderately large inputs built deterministically, \
each call taking roughly 1 to 50 ms, and asserts on the results.
- Pass the function only plain picklable data (numbers, strings, lists, dicts, tuples, \
sets, instances of the module's own classes). No lambdas, generators, open files, or \
classes and functions defined in the test file as arguments.
- Assert exact results, including types and order where the function defines them.

Return the complete file in `code`, without markdown fences."""

REWRITE_SYSTEM = """\
You rewrite one Python function so it uses less CPU, and so less energy, without changing \
anything a caller could observe. The rewrite replaces the original in its module, must pass \
the existing tests, and is checked against the original on real captured calls; it is kept \
only if it is measurably faster.

Keep exactly:
- the name, the parameters with their defaults and annotations, the decorators, and \
whether it is async;
- the observable behaviour for every possible input, not only the tested ones: return \
values and their types (a dict stays a dict, not a Counter or defaultdict; a list stays a \
list), the order of results, the exceptions raised and when, and argument mutation \
(mutate an argument exactly when and how the original does, never otherwise);
- determinism.

Do not:
- add global or module state, or use global or nonlocal;
- cache or memoize results across calls;
- do any I/O, printing or logging;
- import anything but the standard library and modules the file already imports. List \
every import the module does not already have in `new_imports`, as a full statement \
such as "from collections import Counter"; leave it empty otherwise.

`code` is the complete function definition with its decorators, starting at column 0, and \
nothing else at top level (helpers nested inside the function are fine). Never trade \
correctness for speed: if no safe speedup exists, make the smallest safe improvement."""

_KIND_NOTES = {
    "function": "",
    "method": "It is a method: build an instance with the class constructor and plain "
    "arguments, then call the method on it.",
    "staticmethod": "It is a staticmethod: call it through the class.",
    "classmethod": "It is a classmethod: call it through the class.",
}


def _text(text: str, *, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = dict(CACHE_CONTROL)
    return block


def _user(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": list(blocks)}


def _clip(text: str, limit: int, *, tail: bool = False) -> str:
    if len(text) <= limit:
        return text
    return "..." + text[-limit:] if tail else text[:limit] + "..."


def _function_lines(source: str, fn: DiscoveredFunction) -> tuple[int, int, list[int]]:
    """1-based first and last line of ``fn`` in ``source``, and 0-based top-level import lines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return fn.line, fn.end_line, []
    imports = [
        i
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for i in range(node.lineno - 1, node.end_lineno or node.lineno)
    ]
    node = find_function_node(tree, fn.qualname)
    if node is None:
        return fn.line, fn.end_line, imports
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return start, node.end_lineno or node.lineno, imports


def module_excerpt(source: str, fn: DiscoveredFunction, *, limit: int = EXCERPT_CHARS) -> str:
    """The whole module when it fits; else the function, the imports and the lines nearest it."""
    if len(source) <= limit:
        return source
    lines = source.splitlines()
    start, end, imports = _function_lines(source, fn)
    keep = set(range(max(start - 1, 0), min(end, len(lines))))
    used = sum(len(lines[i]) + 1 for i in keep)
    outward = [i for d in range(1, len(lines)) for i in (start - 1 - d, end - 1 + d)]
    for i in [*imports, *outward]:
        if i in keep or not 0 <= i < len(lines):
            continue
        if used + len(lines[i]) + 1 > limit:
            break
        keep.add(i)
        used += len(lines[i]) + 1
    out: list[str] = []
    gap = 0
    for i, line in enumerate(lines):
        if i not in keep:
            gap += 1
            continue
        if gap:
            out.append(f"# ... {gap} lines omitted ...")
            gap = 0
        out.append(line)
    if gap:
        out.append(f"# ... {gap} lines omitted ...")
    return "\n".join(out) + "\n"


def _function_block(fn: DiscoveredFunction) -> str:
    return (
        f'<function id="{fn.function_id}" kind="{fn.kind}" file="{fn.file}">\n'
        f"{fn.source.rstrip()}\n</function>"
    )


def _module_block(fn: DiscoveredFunction, module_source: str) -> str:
    return f'<module path="{fn.file}">\n{module_excerpt(module_source, fn).rstrip()}\n</module>'


def triage_request(fns: Sequence[DiscoveredFunction]) -> Request[TriageOut]:
    parts = [
        f'<function id="{fn.function_id}" kind="{fn.kind}" loc="{fn.features.loc}">\n'
        f"{_clip(fn.source.rstrip(), TRIAGE_SOURCE_CHARS)}\n</function>"
        for fn in fns
    ]
    body = "<functions>\n" + "\n".join(parts) + "\n</functions>"
    msg = _user(_text(body), _text(f"Rate these {len(parts)} functions."))
    return Request(system=TRIAGE_SYSTEM, messages=[msg], output=TriageOut)


def tests_request(fn: DiscoveredFunction, *, module_source: str) -> Request[TestFileOut]:
    how = f"Import it with exactly this line: {fn.import_line}\nCall it like: {fn.call_hint}"
    if note := _KIND_NOTES.get(fn.kind, ""):
        how += f"\n{note}"
    msg = _user(
        _text(_function_block(fn)),
        _text(_module_block(fn, module_source)),
        _text(f"{how}\n\nWrite the test file."),
    )
    return Request(system=TESTS_SYSTEM, messages=[msg], output=TestFileOut)


def _failure_text(f: PytestFailure | str) -> str:
    if isinstance(f, str):
        return f"<failure>\n{_clip(f.strip(), FAILURE_CHARS)}\n</failure>"
    body = _clip(f.message.strip(), FAILURE_CHARS // 2)
    if f.tb_tail.strip():
        body += "\n" + _clip(f.tb_tail.strip(), FAILURE_CHARS, tail=True)
    return f'<failure nodeid="{f.nodeid}">\n{body}\n</failure>'


def tests_repair_messages(
    prev: Conversation, failures: Sequence[PytestFailure | str]
) -> Request[TestRepairOut]:
    """``prev`` plus the failures, as a request for a corrected file."""
    shown = [_failure_text(f) for f in failures[:MAX_FAILURES]]
    text = "<pytest_failures>\n" + "\n".join(shown) + "\n</pytest_failures>\n"
    if len(failures) > MAX_FAILURES:
        text += f"({len(failures) - MAX_FAILURES} more failures not shown)\n"
    text += (
        "\nYour test file failed against the unchanged function, which is correct by "
        "definition. Fix the tests, not the function: make every expectation match what the "
        "function actually does. Keep at least 3 tests and exactly one "
        "@pytest.mark.nz_workload test, and return the complete corrected file."
    )
    return Request(
        system=prev.system, messages=[*prev.messages, _user(_text(text))], output=TestRepairOut
    )


def _samples_block(samples_preview: Sequence[str]) -> str:
    if not samples_preview:
        return "<samples>\n(no samples captured)\n</samples>"
    shown = [
        f"{i}. {_clip(s, SAMPLE_CHARS)}" for i, s in enumerate(samples_preview[:MAX_SAMPLES], 1)
    ]
    more = len(samples_preview) - MAX_SAMPLES
    if more > 0:
        shown.append(f"({more} more not shown)")
    return (
        "<samples>\nArguments of calls captured while the tests ran:\n"
        + "\n".join(shown)
        + "\n</samples>"
    )


def rewrite_request(
    fn: DiscoveredFunction,
    *,
    module_source: str,
    test_code: str,
    samples_preview: Sequence[str],
    candidate: CandidateId,
) -> Request[RewriteOut]:
    msg = _user(
        _text(_function_block(fn)),
        _text(_module_block(fn, module_source)),
        _text(f"<tests>\n{test_code.rstrip()}\n</tests>"),
        _text(_samples_block(samples_preview), cache=True),
        _text(
            f"Candidate {candidate} strategy: {STRATEGY_HINTS[candidate]}. Other candidates "
            "try other strategies, so commit to this one; if it cannot help this function, "
            "make the best safe improvement you can."
        ),
    )
    return Request(system=REWRITE_SYSTEM, messages=[msg], output=RewriteOut)


def rewrite_repair_messages(prev: Conversation, problems: str) -> Request[RewriteRepairOut]:
    """``prev`` plus why the rewrite was rejected, as a request for a fixed one."""
    text = (
        f"<problems>\n{_clip(problems.strip(), PROBLEM_CHARS)}\n</problems>\n\n"
        "Your rewrite was rejected for the problems above. Diagnose them and return the "
        "complete corrected function, keeping every rule from the system prompt. Keep the "
        "speedup if you can do so safely; otherwise make a more conservative rewrite."
    )
    return Request(
        system=prev.system,
        messages=[*prev.messages, _user(_text(text))],
        output=RewriteRepairOut,
    )
