"""LlmClient against a fake SDK: parameters, retries, cassettes and cost reporting."""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from pydantic import SecretStr

from netzero.config import Settings, StageModelConfig
from netzero.errors import LlmError
from netzero.events import LlmUsageData
from netzero.llm import client as llm
from netzero.llm.cassette import CassetteKey, CassetteStore
from netzero.llm.client import LlmClient, request_params
from netzero.llm.models import TestFileOut, TriageOut
from netzero.llm.prompts import Request

KEY = "sk-ant-test-0123456789"
REQ = Request(
    system="sys",
    messages=[{"role": "user", "content": [{"type": "text", "text": "write tests"}]}],
    output=TestFileOut,
)
TESTS_KEY = CassetteKey("tests", "pkg.mod:f")
GOOD = {"code": "def test_a():\n    assert True\n", "notes": "one test"}


def message(
    payload: Any = GOOD,
    *,
    stop: str = "end_turn",
    i: int = 1000,
    o: int = 200,
    thinking: bool = False,
) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    content = [SimpleNamespace(type="text", text=text)]
    if thinking:
        content.insert(0, SimpleNamespace(type="thinking", thinking="hmm", signature="sig"))
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        model="claude-haiku-4-5-20251001",
        usage=SimpleNamespace(
            input_tokens=i,
            output_tokens=o,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=0,
        ),
    )


class FakeStream:
    def __init__(self, sdk: FakeSdk, reply: Any):
        self.sdk, self.reply = sdk, reply

    async def __aenter__(self) -> FakeStream:
        self.sdk.active += 1
        self.sdk.peak = max(self.sdk.peak, self.sdk.active)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.sdk.active -= 1

    async def get_final_message(self) -> Any:
        await asyncio.sleep(self.sdk.delay)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class FakeSdk:
    """``sdk.messages.stream(**kw)`` returning queued replies (or one reply forever)."""

    def __init__(self, *replies: Any, delay: float = 0.0):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.messages = self
        self.delay = delay
        self.active = self.peak = 0
        self.closed = False

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return FakeStream(self, reply)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def live(settings: Settings) -> Settings:
    return settings.model_copy(update={"anthropic_api_key": SecretStr(KEY)})


def collect() -> tuple[list[tuple[LlmUsageData, CassetteKey]], Any]:
    events: list[tuple[LlmUsageData, CassetteKey]] = []
    return events, lambda usage, key: events.append((usage, key))


# -- request parameters ---------------------------------------------------------


def test_haiku_gets_no_effort_and_no_thinking_by_default() -> None:
    params = request_params(StageModelConfig(model="claude-haiku-4-5", effort="high"), REQ)
    assert "thinking" not in params
    assert set(params["output_config"]) == {"format"}
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert params["max_tokens"] == 8000
    assert params["system"] == "sys" and params["messages"] == REQ.messages
    assert "temperature" not in params and "top_p" not in params


def test_haiku_thinking_uses_a_budget_below_max_tokens() -> None:
    cfg = StageModelConfig(model="claude-haiku-4-5", thinking=True, max_tokens=8000)
    params = request_params(cfg, REQ)
    assert params["thinking"] == {"type": "enabled", "budget_tokens": 4000}
    assert params["max_tokens"] == 8000
    small = request_params(cfg.model_copy(update={"max_tokens": 600}), REQ)
    assert small["thinking"]["budget_tokens"] == 1024
    assert small["max_tokens"] > 1024


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"])
def test_five_family_gets_effort_and_adaptive_thinking(model: str) -> None:
    params = request_params(StageModelConfig(model=model, effort="high", thinking=True), REQ)
    assert params["output_config"]["effort"] == "high"
    assert params["thinking"] == {"type": "adaptive"}


def test_thinking_off_omits_the_field_rather_than_disabling() -> None:
    params = request_params(StageModelConfig(model="claude-sonnet-5-5", effort="low"), REQ)
    assert "thinking" not in params
    assert params["output_config"]["effort"] == "low"


def test_unknown_models_get_neither_effort_nor_thinking() -> None:
    cfg = StageModelConfig(model="claude-future-9", effort="max", thinking=True)
    params = request_params(cfg, REQ)
    assert "thinking" not in params and "effort" not in params["output_config"]


def test_dated_ids_share_capabilities() -> None:
    assert llm.capabilities("claude-haiku-4-5-20251001") == llm.CAPABILITIES["claude-haiku-4-5"]


def test_params_are_json_serialisable() -> None:
    cfg = StageModelConfig(model="claude-opus-5-5", effort="high", thinking=True)
    json.dumps(request_params(cfg, Request("s", REQ.messages, TriageOut)))


# -- live calls -------------------------------------------------------------------


async def test_live_call_parses_and_reports_usage(live: Settings) -> None:
    sdk = FakeSdk(message(thinking=True))
    events, on_usage = collect()
    client = LlmClient(live, sdk=sdk, on_usage=on_usage)
    result = await client.call("tests", REQ, TESTS_KEY)
    assert result.parsed == TestFileOut(**GOOD)
    assert len(sdk.calls) == 1
    usage, key = events[0]
    assert key == TESTS_KEY
    assert usage.cassette == "off" and usage.stage == "tests"
    assert usage.model == "claude-haiku-4-5"
    assert (usage.input_tokens, usage.output_tokens, usage.cache_creation_input_tokens) == (
        1000,
        200,
        0,
    )
    assert usage.cost_usd == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1e6)
    assert usage.run_cost_usd == usage.cost_usd
    assert usage.stop_reason == "end_turn"
    assert result.usage == usage
    # the repair conversation echoes the answer as plain text, no thinking blocks
    assert result.conversation.system == "sys"
    assert result.conversation.messages[:-1] == REQ.messages
    assert result.conversation.messages[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": TestFileOut(**GOOD).model_dump_json()}],
    }


async def test_on_usage_gets_the_running_total(live: Settings) -> None:
    events, on_usage = collect()
    client = LlmClient(live, sdk=FakeSdk(message(i=3000, o=700)), on_usage=on_usage)
    for _ in range(3):
        await client.call("tests", REQ, TESTS_KEY)
    costs = [u.cost_usd for u, _ in events]
    totals = [u.run_cost_usd for u, _ in events]
    assert totals == pytest.approx([costs[0], costs[0] + costs[1], sum(costs)])
    assert client.run_cost_usd == pytest.approx(sum(costs))


async def test_max_tokens_retries_once_with_double_budget(live: Settings) -> None:
    sdk = FakeSdk(message('{"code": "def te', stop="max_tokens", o=8000), message())
    events, on_usage = collect()
    warnings: list[str] = []
    client = LlmClient(live, sdk=sdk, on_usage=on_usage, on_warn=warnings.append)
    result = await client.call("tests", REQ, TESTS_KEY)
    assert result.parsed.notes == "one test"
    assert [c["max_tokens"] for c in sdk.calls] == [8000, 16000]
    assert [u.stop_reason for u, _ in events] == ["max_tokens", "end_turn"]
    assert events[1][0].run_cost_usd == pytest.approx(events[0][0].cost_usd + events[1][0].cost_usd)
    assert any("max_tokens=16000" in w for w in warnings)


async def test_max_tokens_twice_fails(live: Settings) -> None:
    sdk = FakeSdk(message("{", stop="max_tokens"))
    client = LlmClient(live, sdk=sdk, on_warn=lambda _: None)
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error" and "max_tokens" in exc.value.message
    assert len(sdk.calls) == 2


async def test_invalid_answer_is_retried_once(live: Settings) -> None:
    sdk = FakeSdk(message({"code": "x"}), message())
    events, on_usage = collect()
    client = LlmClient(live, sdk=sdk, on_usage=on_usage, on_warn=lambda _: None)
    result = await client.call("tests", REQ, TESTS_KEY)
    assert result.parsed.code.startswith("def test_a")
    assert len(sdk.calls) == 2 and len(events) == 2
    assert sdk.calls[0]["max_tokens"] == sdk.calls[1]["max_tokens"]


@pytest.mark.parametrize(
    ("replies", "max_tokens"),
    [
        ((message("{", stop="max_tokens"), message({"code": "x"})), [8000, 16000, 16000]),
        ((message({"code": "x"}), message("{", stop="max_tokens")), [8000, 8000, 16000]),
    ],
)
async def test_each_retry_is_spent_once(
    live: Settings, replies: tuple[Any, ...], max_tokens: list[int]
) -> None:
    sdk = FakeSdk(*replies)
    events, on_usage = collect()
    client = LlmClient(live, sdk=sdk, on_usage=on_usage, on_warn=lambda _: None)
    with pytest.raises(LlmError):
        await client.call("tests", REQ, TESTS_KEY)
    assert [c["max_tokens"] for c in sdk.calls] == max_tokens
    assert len(events) == 3  # every attempt is billed


async def test_cut_off_then_invalid_then_valid(live: Settings) -> None:
    sdk = FakeSdk(message("{", stop="max_tokens"), message({"code": "x"}), message())
    client = LlmClient(live, sdk=sdk, on_warn=lambda _: None)
    result = await client.call("tests", REQ, TESTS_KEY)
    assert result.parsed == TestFileOut(**GOOD)
    assert len(sdk.calls) == 3


async def test_invalid_answer_twice_fails(live: Settings) -> None:
    sdk = FakeSdk(message("not json at all"))
    client = LlmClient(live, sdk=sdk, on_warn=lambda _: None)
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error" and "TestFileOut" in exc.value.message
    assert len(sdk.calls) == 2


async def test_refusal_fails_at_once(live: Settings) -> None:
    sdk = FakeSdk(message("", stop="refusal"))
    events, on_usage = collect()
    client = LlmClient(live, sdk=sdk, on_usage=on_usage)
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_refusal"
    assert len(sdk.calls) == 1
    assert events[0][0].stop_reason == "refusal"


async def test_context_window_exceeded_is_not_retried(live: Settings) -> None:
    sdk = FakeSdk(message("", stop="model_context_window_exceeded"))
    client = LlmClient(live, sdk=sdk)
    with pytest.raises(LlmError, match="context window"):
        await client.call("tests", REQ, TESTS_KEY)
    assert len(sdk.calls) == 1


async def test_sdk_errors_become_llm_errors_without_secrets(live: Settings) -> None:
    response = SimpleNamespace(request=None, status_code=400, headers={})
    body = {"error": {"type": "invalid_request_error", "message": f"bad key {KEY} sk-ant-other"}}
    error = anthropic.BadRequestError("Error code: 400", response=response, body=body)  # type: ignore[arg-type]
    client = LlmClient(live, sdk=FakeSdk(error))
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error"
    assert "400" in exc.value.message and "bad key" in exc.value.message
    assert KEY not in exc.value.message and "sk-ant-other" not in exc.value.message
    assert exc.value.__cause__ is None


class RemoteProtocolError(Exception):
    """Like the transport error a stream dropped mid-answer raises; the SDK does not wrap it."""


@pytest.mark.parametrize(
    "error",
    [
        RemoteProtocolError(f"peer closed connection without sending complete message ({KEY})"),
        RuntimeError("Unexpected event order, got content_block_delta before message_start"),
        ConnectionResetError(54, "Connection reset by peer"),
    ],
)
async def test_mid_stream_failures_become_llm_errors(live: Settings, error: Exception) -> None:
    """Anything else would fail the whole function as an internal error."""
    events, on_usage = collect()
    sdk = FakeSdk(error)
    client = LlmClient(live, sdk=sdk, on_usage=on_usage)
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error"
    assert type(error).__name__ in exc.value.message
    # the function scope logs the whole traceback, chained causes included
    assert KEY not in "".join(traceback.format_exception(exc.value))
    assert len(sdk.calls) == 1 and events == []
    assert sdk.active == 0 and not client._sem.locked()


async def test_connection_errors_become_llm_errors(live: Settings) -> None:
    client = LlmClient(live, sdk=FakeSdk(anthropic.APIConnectionError(request=None)))  # type: ignore[arg-type]
    with pytest.raises(LlmError, match="APIConnectionError"):
        await client.call("tests", REQ, TESTS_KEY)


async def test_no_api_key_is_an_llm_error(settings: Settings) -> None:
    client = LlmClient(settings)
    with pytest.raises(LlmError, match="API key"):
        await client.call("tests", REQ, TESTS_KEY)


async def test_sdk_is_created_lazily_with_retries(live: Settings) -> None:
    client = LlmClient(live)
    assert client._sdk is None
    sdk = client.sdk
    assert isinstance(sdk, anthropic.AsyncAnthropic)
    assert sdk.max_retries == llm.MAX_RETRIES
    await client.aclose()
    assert client._sdk is None


async def test_aclose_leaves_a_given_sdk_open(live: Settings) -> None:
    sdk = FakeSdk(message())
    client = LlmClient(live, sdk=sdk)
    await client.aclose()
    assert not sdk.closed
    # and keeps using it rather than building a real client from the key
    assert client.sdk is sdk
    await client.call("tests", REQ, TESTS_KEY)
    assert len(sdk.calls) == 1


async def test_stage_must_match_the_key(live: Settings) -> None:
    client = LlmClient(live, sdk=FakeSdk(message()))
    with pytest.raises(ValueError, match="stage"):
        await client.call("rewrite", REQ, TESTS_KEY)


async def test_calls_respect_the_concurrency_limit(live: Settings) -> None:
    limited = live.model_copy(update={"llm_concurrency": 2})
    sdk = FakeSdk(message(), delay=0.01)
    client = LlmClient(limited, sdk=sdk)
    await asyncio.gather(*(client.call("tests", REQ, TESTS_KEY) for _ in range(6)))
    assert len(sdk.calls) == 6 and sdk.peak == 2


async def test_cancelling_a_call_frees_its_slot(live: Settings) -> None:
    sdk = FakeSdk(message(), delay=30)
    client = LlmClient(live.model_copy(update={"llm_concurrency": 1}), sdk=sdk)
    task = asyncio.create_task(client.call("tests", REQ, TESTS_KEY))
    for _ in range(50):
        if sdk.active:
            break
        await asyncio.sleep(0)
    assert sdk.active == 1 and client._sem.locked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sdk.active == 0 and not client._sem.locked()
    sdk.delay = 0
    assert (await client.call("tests", REQ, TESTS_KEY)).parsed == TestFileOut(**GOOD)


async def test_stage_config_decides_the_model(live: Settings) -> None:
    stages = live.stages.model_copy(
        update={"tests": StageModelConfig(model="claude-sonnet-5-5", effort="medium")}
    )
    sdk = FakeSdk(message())
    events, on_usage = collect()
    client = LlmClient(live.model_copy(update={"stages": stages}), sdk=sdk, on_usage=on_usage)
    await client.call("tests", REQ, TESTS_KEY)
    assert sdk.calls[0]["model"] == "claude-sonnet-5-5"
    assert sdk.calls[0]["output_config"]["effort"] == "medium"
    assert events[0][0].cost_usd == pytest.approx((1000 * 2.0 + 200 * 10.0) / 1e6)


async def test_unknown_model_warns_once(live: Settings) -> None:
    stages = live.stages.model_copy(update={"tests": StageModelConfig(model="claude-new-1")})
    warnings: list[str] = []
    client = LlmClient(
        live.model_copy(update={"stages": stages}),
        sdk=FakeSdk(message()),
        on_warn=warnings.append,
    )
    for _ in range(3):
        result = await client.call("tests", REQ, TESTS_KEY)
    assert result.usage.cost_usd == 0.0
    assert len(warnings) == 1


# -- cassettes --------------------------------------------------------------------


async def test_record_then_replay(live: Settings, tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    events, on_usage = collect()
    recorder = LlmClient(
        live.model_copy(update={"cassette_mode": "record"}),
        cassettes=store,
        sdk=FakeSdk(message({"code": "x"}), message()),
        on_usage=on_usage,
        on_warn=lambda _: None,
    )
    recorded = await recorder.call("tests", REQ, TESTS_KEY)
    assert [u.cassette for u, _ in events] == ["off", "recorded"]
    doc = json.loads(store.path(TESTS_KEY).read_text())
    assert doc["output"] == GOOD
    assert doc["model"] == "claude-haiku-4-5"
    assert doc["usage"]["input_tokens"] == 1000
    assert doc["stop_reason"] == "end_turn"
    assert len(doc["request_hash"]) == 64

    # replay needs neither a key nor an SDK
    replay_settings = live.model_copy(update={"cassette_mode": "replay", "anthropic_api_key": None})
    events.clear()
    warnings: list[str] = []
    player = LlmClient(replay_settings, cassettes=store, on_usage=on_usage, on_warn=warnings.append)
    replayed = await player.call("tests", REQ, TESTS_KEY)
    assert replayed.parsed == recorded.parsed
    assert replayed.conversation == recorded.conversation
    assert warnings == []
    usage, key = events[0]
    assert key == TESTS_KEY
    assert usage.cassette == "hit" and usage.cost_usd == 0.0 and usage.run_cost_usd == 0.0
    assert usage.input_tokens == 1000 and usage.output_tokens == 200
    assert player._sdk is None


async def test_replay_warns_when_the_prompt_changed(settings: Settings, tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    store.path(TESTS_KEY).parent.mkdir(parents=True)
    store.path(TESTS_KEY).write_text(json.dumps({"output": GOOD, "request_hash": "0" * 64}))
    warnings: list[str] = []
    client = LlmClient(
        settings.model_copy(update={"cassette_mode": "replay"}),
        cassettes=store,
        on_warn=warnings.append,
    )
    result = await client.call("tests", REQ, TESTS_KEY)
    assert result.parsed == TestFileOut(**GOOD)
    assert len(warnings) == 1 and "different prompt" in warnings[0]


async def test_replay_miss(settings: Settings, tmp_path: Path) -> None:
    events, on_usage = collect()
    client = LlmClient(
        settings.model_copy(update={"cassette_mode": "replay"}),
        cassettes=CassetteStore(tmp_path),
        on_usage=on_usage,
    )
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "cassette_miss"
    assert "tests/pkg.mod__f/_/0.json" in exc.value.message
    assert [(u.cassette, k) for u, k in events] == [("miss", TESTS_KEY)]


async def test_replay_of_a_cassette_that_does_not_fit(settings: Settings, tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    store.path(TESTS_KEY).parent.mkdir(parents=True)
    store.path(TESTS_KEY).write_text(json.dumps({"output": {"code": 1}}))
    client = LlmClient(settings.model_copy(update={"cassette_mode": "replay"}), cassettes=store)
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error" and "TestFileOut" in exc.value.message


async def test_off_mode_writes_no_cassettes(live: Settings, tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    client = LlmClient(live, cassettes=store, sdk=FakeSdk(message()))
    await client.call("tests", REQ, TESTS_KEY)
    assert not any(tmp_path.iterdir())


async def test_a_cassette_that_cannot_be_saved_fails_the_step_but_is_billed(
    live: Settings, tmp_path: Path
) -> None:
    (tmp_path / "tests").write_text("a file where the stage directory should be")
    events, on_usage = collect()
    client = LlmClient(
        live.model_copy(update={"cassette_mode": "record"}),
        cassettes=CassetteStore(tmp_path),
        sdk=FakeSdk(message()),
        on_usage=on_usage,
    )
    with pytest.raises(LlmError) as exc:
        await client.call("tests", REQ, TESTS_KEY)
    assert exc.value.kind == "llm_error"
    assert "could not write cassette tests/pkg.mod__f/_/0.json" in exc.value.message
    assert str(tmp_path) not in exc.value.message
    [(usage, key)] = events
    assert key == TESTS_KEY and usage.cassette == "off" and usage.cost_usd > 0
    assert client.run_cost_usd == pytest.approx(usage.cost_usd)


async def test_replay_uses_the_recorded_model_and_tolerates_extra_usage_fields(
    settings: Settings, tmp_path: Path
) -> None:
    store = CassetteStore(tmp_path)
    store.path(TESTS_KEY).parent.mkdir(parents=True)
    doc = {
        "output": GOOD,
        "model": "claude-sonnet-5-5",
        "usage": {"input_tokens": 7, "service_tier": "standard"},
    }
    store.path(TESTS_KEY).write_text(json.dumps(doc))
    events, on_usage = collect()
    client = LlmClient(
        settings.model_copy(update={"cassette_mode": "replay"}),
        cassettes=store,
        on_usage=on_usage,
    )
    await client.call("tests", REQ, TESTS_KEY)
    [(usage, _)] = events
    assert usage.model == "claude-sonnet-5-5"
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (7, 0, 0.0)
