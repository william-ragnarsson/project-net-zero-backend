"""Prompt builders: byte-stable, cache-friendly, and saying what each stage needs."""

from __future__ import annotations

import json
import textwrap

import pytest

from netzero.events import PytestFailure
from netzero.llm import prompts
from netzero.llm.models import RewriteOut, RewriteRepairOut, TestFileOut, TestRepairOut
from netzero.llm.prompts import Conversation
from netzero.pipeline.discovery import DiscoveredFunction, StaticFeatures

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


    def dedupe(items):
        out = []
        for x in items:
            if x not in out:
                out.append(x)
        return out
    '''
)


def make_fn(qualname: str = "dedupe", kind: str = "function", source: str | None = None):
    lines = MODULE.splitlines()
    name = qualname.rsplit(".", 1)[-1]
    start = next(i for i, line in enumerate(lines, 1) if line.strip().startswith(f"def {name}("))
    src = source or textwrap.dedent("\n".join(lines[start - 1 : start + 5])) + "\n"
    return DiscoveredFunction(
        function_id=f"pkg.mod:{qualname}",
        module="pkg.mod",
        qualname=qualname,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        file="pkg/mod.py",
        line=start,
        end_line=start + 5,
        def_line=start,
        source=src,
        import_line=f"from pkg.mod import {qualname.split('.')[0]}",
        call_hint=f"{qualname}(items)" if kind == "function" else "Bag(items).unique()",
        import_root=".",
        class_path=qualname.split(".")[:-1],
        nested_in_function=False,
        features=StaticFeatures(loc=6, n_args=1),
    )


def rewrite(candidate: str, samples: list[str] | None = None):
    return prompts.rewrite_request(
        make_fn(),
        module_source=MODULE,
        test_code="def test_x():\n    assert dedupe([1, 1]) == [1]\n",
        samples_preview=["([1, 2, 2],) {}"] if samples is None else samples,
        candidate=candidate,  # type: ignore[arg-type]
    )


def dump(request: prompts.Request) -> str:
    return json.dumps(
        {"system": request.system, "messages": request.messages}, sort_keys=True, ensure_ascii=False
    )


def test_candidates_share_everything_but_the_hint_block() -> None:
    a, b, c = rewrite("A"), rewrite("B"), rewrite("C")
    assert a.system == b.system == c.system == prompts.REWRITE_SYSTEM
    assert [len(r.messages) for r in (a, b, c)] == [1, 1, 1]
    blocks = [r.messages[0]["content"] for r in (a, b, c)]
    assert blocks[0][:-1] == blocks[1][:-1] == blocks[2][:-1]
    assert len({json.dumps(bl[-1]) for bl in blocks}) == 3
    # the last shared block carries the cache breakpoint, the hint block does not
    assert blocks[0][-2]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[0][-1]
    assert sum("cache_control" in block for block in blocks[0]) == 1
    assert prompts.STRATEGY_HINTS["A"] in blocks[0][-1]["text"]
    assert "numpy" in blocks[2][-1]["text"]
    assert a.output is RewriteOut


def test_requests_are_json_and_byte_stable() -> None:
    for build in (
        lambda: rewrite("B"),
        lambda: prompts.tests_request(make_fn(), module_source=MODULE),
        lambda: prompts.triage_request([make_fn(), make_fn("Bag.unique", "method")]),
    ):
        first, second = dump(build()), dump(build())
        assert first == second
        json.loads(first)


def test_cache_control_survives_into_the_repair() -> None:
    req = rewrite("A")
    conv = Conversation(
        system=req.system,
        messages=[
            *req.messages,
            {"role": "assistant", "content": [{"type": "text", "text": "{}"}]},
        ],
    )
    repair = prompts.rewrite_repair_messages(conv, "AssertionError: [1] != [1, 2]")
    assert repair.output is RewriteRepairOut
    assert repair.system == prompts.REWRITE_SYSTEM
    assert repair.messages[:2] == conv.messages
    assert repair.messages[0]["content"][3]["cache_control"] == {"type": "ephemeral"}
    assert repair.messages[-1]["role"] == "user"
    assert "AssertionError" in repair.messages[-1]["content"][0]["text"]


def test_repair_problems_are_clipped() -> None:
    conv = Conversation(system="s", messages=[])
    repair = prompts.rewrite_repair_messages(conv, "x" * 50_000)
    assert len(repair.messages[-1]["content"][0]["text"]) < prompts.PROBLEM_CHARS + 1000


def test_samples_block() -> None:
    empty = rewrite("A", samples=[])
    assert "(no samples captured)" in empty.messages[0]["content"][3]["text"]
    many = rewrite("A", samples=[f"({i},) {{}}" + "z" * 1000 for i in range(30)])
    text = many.messages[0]["content"][3]["text"]
    assert "12. (11,)" in text and "13." not in text
    assert "18 more not shown" in text
    assert len(text) < 12 * (prompts.SAMPLE_CHARS + 20) + 200


def test_rewrite_request_shows_function_module_and_tests() -> None:
    texts = [block["text"] for block in rewrite("A").messages[0]["content"]]
    assert texts[0].startswith('<function id="pkg.mod:dedupe" kind="function" file="pkg/mod.py">')
    assert "from collections import OrderedDict" in texts[1]
    assert "assert dedupe([1, 1]) == [1]" in texts[2]


def test_tests_request() -> None:
    req = prompts.tests_request(make_fn("Bag.unique", "method"), module_source=MODULE)
    assert req.system == prompts.TESTS_SYSTEM
    assert req.output is TestFileOut
    text = "\n".join(block["text"] for block in req.messages[0]["content"])
    assert "from pkg.mod import Bag" in text
    assert "Bag(items).unique()" in text
    assert "It is a method" in text
    assert '<module path="pkg/mod.py">' in text
    assert "nz_workload" in prompts.TESTS_SYSTEM
    assert "random.Random(42)" in prompts.TESTS_SYSTEM


def test_triage_request_lists_every_function_and_clips_long_sources() -> None:
    long_fn = make_fn(source="def dedupe(items):\n" + "    x = 1\n" * 1000)
    req = prompts.triage_request([long_fn, make_fn("Bag.unique", "method")])
    text = req.messages[0]["content"][0]["text"]
    assert 'id="pkg.mod:dedupe"' in text and 'id="pkg.mod:Bag.unique"' in text
    assert 'kind="method"' in text and 'loc="6"' in text
    assert len(text) < prompts.TRIAGE_SOURCE_CHARS + 1000
    assert "2 functions" in req.messages[0]["content"][1]["text"]


def test_tests_repair_appends_failures() -> None:
    conv = Conversation(
        system=prompts.TESTS_SYSTEM,
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "write tests"}]},
            {"role": "assistant", "content": [{"type": "text", "text": '{"code": ""}'}]},
        ],
    )
    failures = [
        PytestFailure(nodeid=f"t.py::test_{i}", message=f"assert {i} == 0", tb_tail="E   boom")
        for i in range(14)
    ]
    req = prompts.tests_repair_messages(conv, [*failures, "collection error: ImportError"])
    assert req.output is TestRepairOut
    assert req.system == conv.system
    assert req.messages[:2] == conv.messages
    text = req.messages[-1]["content"][0]["text"]
    assert 'nodeid="t.py::test_9"' in text and "test_10" not in text
    assert "5 more failures not shown" in text
    assert "not the function" in text


def test_tests_repair_accepts_plain_strings() -> None:
    req = prompts.tests_repair_messages(Conversation("s", []), ["ImportError: no module x"])
    assert "ImportError: no module x" in req.messages[-1]["content"][0]["text"]


class TestModuleExcerpt:
    def test_small_module_is_whole(self) -> None:
        assert prompts.module_excerpt(MODULE, make_fn()) == MODULE

    def test_large_module_keeps_function_and_imports(self) -> None:
        filler = "".join(f"CONST_{i} = {i}\n" for i in range(3000))
        source = "import math\nimport re\n\n" + filler + "\n\n" + MODULE.split("\n\n\n", 1)[1]
        fn = make_fn()
        excerpt = prompts.module_excerpt(source, fn, limit=2000)
        assert len(excerpt) < 2400
        assert excerpt.startswith("import math\nimport re\n")
        assert "def dedupe(items):" in excerpt and "return out" in excerpt
        assert "lines omitted ..." in excerpt
        assert "CONST_2999 = 2999" in excerpt  # nearest lines first
        assert "CONST_5 = 5" not in excerpt

    def test_unparsable_module_falls_back_to_line_numbers(self) -> None:
        source = "def broken(:\n" + "x = 1\n" * 2000 + MODULE
        fn = make_fn()
        fn.line, fn.end_line = 2001 + fn.line, 2001 + fn.end_line
        excerpt = prompts.module_excerpt(source, fn, limit=500)
        assert "def dedupe(items):" in excerpt


@pytest.mark.parametrize(
    "system", [prompts.TRIAGE_SYSTEM, prompts.TESTS_SYSTEM, prompts.REWRITE_SYSTEM]
)
def test_system_prompts_are_static(system: str) -> None:
    assert "{" not in system  # no unformatted placeholders
    assert "/Users/" not in system and "/tmp" not in system
