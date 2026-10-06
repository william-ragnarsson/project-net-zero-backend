"""The pipeline's LLM steps: which prompt, which cassette key, what comes back."""

from __future__ import annotations

from typing import Any

import pytest

from netzero.events import LlmUsageData, PytestFailure
from netzero.llm import tasks
from netzero.llm.cassette import CassetteKey
from netzero.llm.client import LlmResult, conversation
from netzero.llm.models import RewriteOut, RewriteRepairOut, TestFileOut, TestRepairOut
from netzero.llm.prompts import Request
from netzero.pipeline.discovery import DiscoveredFunction, StaticFeatures
from netzero.pipeline.triage import LlmRating

MODULE = "def dedupe(items):\n    return list(dict.fromkeys(items))\n"
REWRITE = {"strategy": "dict", "rationale": "O(n)", "code": MODULE, "new_imports": []}
CANNED: dict[str, dict[str, Any]] = {
    "tests": {"code": "def test_a():\n    pass\n", "notes": "n"},
    "tests_repair": {"diagnosis": "d", "code": "def test_b():\n    pass\n", "notes": "n"},
    "rewrite": REWRITE,
    "rewrite_repair": {"diagnosis": "d", **REWRITE},
}


def make_fn(fid: str = "pkg.mod:dedupe") -> DiscoveredFunction:
    return DiscoveredFunction(
        function_id=fid,
        module="pkg.mod",
        qualname=fid.split(":")[1],
        name=fid.split(":")[1],
        kind="function",
        file="pkg/mod.py",
        line=1,
        end_line=2,
        def_line=1,
        source=MODULE,
        import_line="from pkg.mod import dedupe",
        call_hint="dedupe(items)",
        import_root=".",
        class_path=[],
        nested_in_function=False,
        features=StaticFeatures(loc=2, n_args=1),
    )


class FakeClient:
    """Records each call and answers with a canned output for the stage."""

    def __init__(self, triage: dict[str, Any] | None = None):
        self.calls: list[tuple[str, Request[Any], CassetteKey]] = []
        self.canned = {**CANNED, "triage": triage or {"ratings": []}}

    async def call(self, stage: str, request: Request[Any], key: CassetteKey) -> LlmResult[Any]:
        self.calls.append((stage, request, key))
        parsed = request.output.model_validate(self.canned[stage])
        usage = LlmUsageData(stage=stage, model="claude-haiku-4-5", cassette="off")  # type: ignore[arg-type]
        return LlmResult(parsed=parsed, usage=usage, conversation=conversation(request, parsed))


def rating(fid: str, potential: str = "high") -> dict[str, str]:
    return {"function_id": fid, "potential": potential, "testability": "medium", "reason": "loop"}


async def test_ranker_maps_ratings_and_drops_unknown_ids() -> None:
    client = FakeClient(
        triage={
            "ratings": [rating("pkg.mod:a"), rating("pkg.mod:ghost"), rating("pkg.mod:b", "low")]
        }
    )
    ranker = tasks.make_ranker(client)  # type: ignore[arg-type]
    ratings = await ranker([make_fn("pkg.mod:a"), make_fn("pkg.mod:b")])
    assert ratings == {
        "pkg.mod:a": LlmRating("high", "medium", "loop"),
        "pkg.mod:b": LlmRating("low", "medium", "loop"),
    }
    [(stage, request, key)] = client.calls
    assert stage == "triage" and key == CassetteKey("triage")
    assert "2 functions" in request.messages[0]["content"][-1]["text"]


async def test_ranker_skips_the_call_for_no_functions() -> None:
    client = FakeClient()
    assert await tasks.make_ranker(client)([]) == {}  # type: ignore[arg-type]
    assert client.calls == []


async def test_each_step_uses_its_own_cassette_key() -> None:
    client: Any = FakeClient()
    fn = make_fn()
    tests, conv = await tasks.write_tests(client, fn, module_source=MODULE)
    assert isinstance(tests, TestFileOut)
    fixed, _ = await tasks.repair_tests(client, conv, fn, ["boom"], 2)
    assert isinstance(fixed, TestRepairOut)
    cand, cconv = await tasks.write_candidate(
        client, fn, "B", module_source=MODULE, test_code=tests.code, samples_preview=["(1,) {}"]
    )
    assert isinstance(cand, RewriteOut)
    redo, _ = await tasks.repair_candidate(client, cconv, fn, "B", "slower")
    assert isinstance(redo, RewriteRepairOut)
    assert [(stage, key) for stage, _, key in client.calls] == [
        ("tests", CassetteKey("tests", "pkg.mod:dedupe")),
        ("tests_repair", CassetteKey("tests_repair", "pkg.mod:dedupe", None, 2)),
        ("rewrite", CassetteKey("rewrite", "pkg.mod:dedupe", "B", 0)),
        ("rewrite_repair", CassetteKey("rewrite_repair", "pkg.mod:dedupe", "B", 1)),
    ]


async def test_repairs_extend_the_previous_conversation() -> None:
    client: Any = FakeClient()
    fn = make_fn()
    _, conv = await tasks.write_tests(client, fn, module_source=MODULE)
    failure = PytestFailure(nodeid="t.py::test_a", message="assert 1 == 2")
    _, repaired = await tasks.repair_tests(client, conv, fn, [failure], 1)
    _, request, _ = client.calls[-1]
    assert request.system == conv.system
    assert request.messages[: len(conv.messages)] == conv.messages
    assert "assert 1 == 2" in request.messages[-1]["content"][0]["text"]
    # the next repair builds on this one
    assert repaired.messages[: len(request.messages)] == request.messages
    assert repaired.messages[-1]["role"] == "assistant"


@pytest.mark.parametrize("attempt", [0, -1])
async def test_repair_attempts_count_from_one(attempt: int) -> None:
    client: Any = FakeClient()
    fn = make_fn()
    _, conv = await tasks.write_tests(client, fn, module_source=MODULE)
    with pytest.raises(ValueError):
        await tasks.repair_tests(client, conv, fn, ["x"], attempt)
    with pytest.raises(ValueError):
        await tasks.repair_candidate(client, conv, fn, "A", "x", attempt=attempt)
    assert len(client.calls) == 1
