"""The pipeline's LLM steps: build the prompt, pick the cassette key, call.

Each step returns the answer and the conversation a repair extends. Cassette
keys name the step, not the request bytes, so a recorded demo replays in the
same order whatever the prompts say::

    triage          (triage, None, None, 0)
    tests           (tests, fid, None, 0)
    tests_repair    (tests_repair, fid, None, n >= 1)
    rewrite         (rewrite, fid, candidate, 0)
    rewrite_repair  (rewrite_repair, fid, candidate, n >= 1)
"""

from __future__ import annotations

from collections.abc import Sequence

from netzero.events import CandidateId, PytestFailure
from netzero.llm.cassette import CassetteKey
from netzero.llm.client import LlmClient
from netzero.llm.models import RewriteOut, RewriteRepairOut, TestFileOut, TestRepairOut
from netzero.llm.prompts import (
    Conversation,
    rewrite_repair_messages,
    rewrite_request,
    tests_repair_messages,
    tests_request,
    triage_request,
)
from netzero.pipeline.discovery import DiscoveredFunction
from netzero.pipeline.triage import LlmRanker, LlmRating


def make_ranker(client: LlmClient) -> LlmRanker:
    """One triage call for the shortlist; ratings for ids it was not given are dropped."""

    async def ranker(fns: Sequence[DiscoveredFunction]) -> dict[str, LlmRating]:
        if not fns:
            return {}
        result = await client.call("triage", triage_request(fns), CassetteKey("triage"))
        wanted = {fn.function_id for fn in fns}
        return {
            r.function_id: LlmRating(r.potential, r.testability, r.reason)
            for r in result.parsed.ratings
            if r.function_id in wanted
        }

    return ranker


async def write_tests(
    client: LlmClient, fn: DiscoveredFunction, *, module_source: str
) -> tuple[TestFileOut, Conversation]:
    request = tests_request(fn, module_source=module_source)
    result = await client.call("tests", request, CassetteKey("tests", fn.function_id))
    return result.parsed, result.conversation


async def repair_tests(
    client: LlmClient,
    conv: Conversation,
    fn: DiscoveredFunction,
    failures: Sequence[PytestFailure | str],
    attempt: int,
) -> tuple[TestRepairOut, Conversation]:
    """``attempt`` counts repairs from 1; ``conv`` is the previous write or repair."""
    if attempt < 1:
        raise ValueError(f"repair attempts count from 1, got {attempt}")
    key = CassetteKey("tests_repair", fn.function_id, None, attempt)
    result = await client.call("tests_repair", tests_repair_messages(conv, failures), key)
    return result.parsed, result.conversation


async def write_candidate(
    client: LlmClient,
    fn: DiscoveredFunction,
    candidate: CandidateId,
    *,
    module_source: str,
    test_code: str,
    samples_preview: Sequence[str],
) -> tuple[RewriteOut, Conversation]:
    request = rewrite_request(
        fn,
        module_source=module_source,
        test_code=test_code,
        samples_preview=samples_preview,
        candidate=candidate,
    )
    key = CassetteKey("rewrite", fn.function_id, candidate, 0)
    result = await client.call("rewrite", request, key)
    return result.parsed, result.conversation


async def repair_candidate(
    client: LlmClient,
    conv: Conversation,
    fn: DiscoveredFunction,
    candidate: CandidateId,
    problems: str,
    *,
    attempt: int = 1,
) -> tuple[RewriteRepairOut, Conversation]:
    if attempt < 1:
        raise ValueError(f"repair attempts count from 1, got {attempt}")
    key = CassetteKey("rewrite_repair", fn.function_id, candidate, attempt)
    result = await client.call("rewrite_repair", rewrite_repair_messages(conv, problems), key)
    return result.parsed, result.conversation
