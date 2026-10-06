"""The one place that talks to the Anthropic API, or replays what it once said.

Requests stream (``messages.stream``) with the output schema in
``output_config.format`` and are validated here rather than by
``messages.parse``: ``parse`` raises on a refusal or a truncated answer before
the ``stop_reason`` and the billed usage can be read, and both drive the
retries below and the cost total.

Per call: a refusal fails at once (``llm_refusal``), an answer cut off at
``max_tokens`` is retried once with twice the budget, and an answer that does
not fit the schema is retried once. Each API request reports its usage, so the
run's cost includes the attempts that failed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ValidationError

from netzero.config import Settings, StageModelConfig
from netzero.errors import LlmError
from netzero.events import LlmStage, LlmUsageData
from netzero.llm import pricing
from netzero.llm.cassette import CassetteEntry, CassetteKey, CassetteStore, request_hash
from netzero.llm.prompts import Conversation, Request

log = logging.getLogger(__name__)

MAX_RETRIES = 6  # the SDK's own retries of 408/409/429/5xx and dropped connections
MIN_THINKING_BUDGET = 1024
ERROR_CHARS = 300
_KEY_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

UsageCallback = Callable[[LlmUsageData, CassetteKey], None]
WarnCallback = Callable[[str], None]


@dataclass(frozen=True)
class ModelCaps:
    """Which optional parameters a model accepts; anything else is a 400."""

    effort: bool
    thinking: Literal["budget", "adaptive", "none"]


CAPABILITIES: dict[str, ModelCaps] = {
    "claude-haiku-4-5": ModelCaps(effort=False, thinking="budget"),
    "claude-sonnet-5-5": ModelCaps(effort=True, thinking="adaptive"),
    "claude-opus-5-5": ModelCaps(effort=True, thinking="adaptive"),
    "claude-fable-5-1": ModelCaps(effort=True, thinking="adaptive"),
}
UNKNOWN_CAPS = ModelCaps(effort=False, thinking="none")


def capabilities(model: str) -> ModelCaps:
    return CAPABILITIES.get(model) or CAPABILITIES.get(pricing.base_model(model), UNKNOWN_CAPS)


def output_schema(output: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema structured outputs accept: closed objects, every field required."""
    return anthropic.transform_schema(output)


def request_params(
    config: StageModelConfig, request: Request[Any], *, max_tokens: int | None = None
) -> dict[str, Any]:
    """Keyword arguments for ``messages.stream``, with only what ``config.model`` accepts.

    Thinking is omitted unless asked for: ``{"type": "disabled"}`` is rejected by
    some models, and leaving the field out works on all of them.
    """
    caps = capabilities(config.model)
    tokens = max_tokens or config.max_tokens
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": output_schema(request.output)}
    }
    if config.effort and caps.effort:
        output_config["effort"] = config.effort
    params: dict[str, Any] = {
        "model": config.model,
        "max_tokens": tokens,
        "system": request.system,
        "messages": request.messages,
        "output_config": output_config,
    }
    if config.thinking and caps.thinking == "adaptive":
        params["thinking"] = {"type": "adaptive"}
    elif config.thinking and caps.thinking == "budget":
        budget = max(MIN_THINKING_BUDGET, tokens // 2)
        params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        params["max_tokens"] = max(tokens, budget * 2)  # the budget must stay below max_tokens
    return params


def conversation(request: Request[Any], parsed: BaseModel) -> Conversation:
    """The request plus the answer as plain text, for a repair to extend.

    The answer is re-serialized rather than echoed with its thinking blocks:
    dropping leading thinking is allowed, and the same text in live and replay
    runs keeps repair requests (and their hashes) identical.
    """
    answer = {"role": "assistant", "content": [{"type": "text", "text": parsed.model_dump_json()}]}
    return Conversation(system=request.system, messages=[*request.messages, answer])


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    parsed: T
    usage: LlmUsageData  # of the request that produced ``parsed``
    conversation: Conversation


def _clip(text: str, limit: int = ERROR_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _answer_text(msg: Any) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _parse[T: BaseModel](output: type[T], msg: Any) -> tuple[T | None, str]:
    """The validated answer, or why there is none."""
    stop = msg.stop_reason
    if stop == "refusal":
        return None, "the model declined to answer"
    if stop == "max_tokens":
        return None, "the answer was cut off at max_tokens"
    if stop == "model_context_window_exceeded":
        return None, "the prompt does not fit the model's context window"
    text = _answer_text(msg)
    if not text.strip():
        return None, f"the answer had no text (stop_reason {stop})"
    try:
        return output.model_validate_json(text), ""
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "answer"
        return None, f"the answer does not fit {output.__name__}: {where}: {first.get('msg', e)}"


class LlmClient:
    """Rate-limited, cassette-aware structured calls; one per run, for its cost total."""

    def __init__(
        self,
        settings: Settings,
        *,
        cassettes: CassetteStore | None = None,
        on_usage: UsageCallback | None = None,
        on_warn: WarnCallback | None = None,
        sdk: Any = None,
    ):
        self.settings = settings
        self.mode = settings.cassette_mode
        self.cassettes = cassettes or CassetteStore()
        self.run_cost_usd = 0.0
        self._on_usage = on_usage
        self._on_warn = on_warn or log.warning
        self._sdk = sdk
        self._owns_sdk = False
        self._sem = asyncio.Semaphore(max(1, settings.llm_concurrency))
        self._warned: set[str] = set()

    @property
    def sdk(self) -> Any:
        """Created on first use, so replay runs and tests never need a key."""
        if self._sdk is None:
            if self.mode == "replay":
                raise LlmError("cassette replay never calls the API")
            key = self.settings.anthropic_api_key
            if key is None or not key.get_secret_value().strip():
                raise LlmError("no Anthropic API key: set ANTHROPIC_API_KEY or replay cassettes")
            self._sdk = anthropic.AsyncAnthropic(
                api_key=key.get_secret_value().strip(), max_retries=MAX_RETRIES
            )
            self._owns_sdk = True
        return self._sdk

    async def aclose(self) -> None:
        """Close the SDK this client created; one passed in belongs to the caller and stays."""
        if self._owns_sdk and self._sdk is not None:
            sdk, self._sdk, self._owns_sdk = self._sdk, None, False
            await sdk.close()

    async def call[T: BaseModel](
        self, stage: LlmStage, request: Request[T], key: CassetteKey
    ) -> LlmResult[T]:
        if key.stage != stage:
            raise ValueError(f"cassette key is for stage {key.stage!r}, not {stage!r}")
        config = self.settings.stages.get(stage)
        if self.mode == "replay":
            return self._replay(stage, config, request, key)
        return await self._live(stage, config, request, key)

    # -- live -----------------------------------------------------------------

    async def _live[T: BaseModel](
        self, stage: LlmStage, config: StageModelConfig, request: Request[T], key: CassetteKey
    ) -> LlmResult[T]:
        max_tokens = config.max_tokens
        retried_tokens = retried_schema = False
        while True:
            msg, latency_ms = await self._send(
                request_params(config, request, max_tokens=max_tokens)
            )
            parsed, problem = _parse(request.output, msg)
            save_error: OSError | None = None
            if parsed is not None and self.mode == "record":
                try:
                    self._record(key, config.model, request, parsed, msg)
                except OSError as e:  # still bill the call below, then fail the step
                    save_error = e
            recorded = parsed is not None and self.mode == "record" and save_error is None
            usage = self._usage(stage, config.model, msg, latency_ms, recorded=recorded)
            self._report(usage, key)
            if save_error is not None:
                reason = save_error.strerror or type(save_error).__name__
                where = key.relpath().as_posix()
                raise LlmError(f"{stage}: could not write cassette {where}: {reason}") from None
            if parsed is not None:
                return LlmResult(
                    parsed=parsed, usage=usage, conversation=conversation(request, parsed)
                )
            stop = msg.stop_reason
            if stop == "refusal":
                raise LlmError(f"{stage}: {problem}", kind="llm_refusal")
            if stop == "max_tokens" and not retried_tokens:
                retried_tokens = True
                max_tokens *= 2
                self._on_warn(f"{stage}: answer cut off, retrying with max_tokens={max_tokens}")
                continue
            if stop not in ("max_tokens", "model_context_window_exceeded") and not retried_schema:
                retried_schema = True
                self._on_warn(f"{stage}: {problem}; retrying once")
                continue
            raise LlmError(f"{stage}: {problem}")

    async def _send(self, params: dict[str, Any]) -> tuple[Any, int]:
        sdk = self.sdk
        async with self._sem:
            started = time.monotonic()
            try:
                async with sdk.messages.stream(**params) as stream:
                    msg = await stream.get_final_message()
            except anthropic.APIError as e:
                raise LlmError(self._describe(e)) from None
            except Exception as e:
                # The SDK retries only the opening request: a stream dropped mid-answer
                # raises its transport's own errors. Fail the step, not the function.
                # No chained cause: failed functions log their traceback unredacted.
                text = self._redact(f"{type(e).__name__}: {e}")
                raise LlmError(_clip(f"Anthropic API stream failed: {text}")) from None
        return msg, round((time.monotonic() - started) * 1000)

    def _describe(self, e: anthropic.APIError) -> str:
        """Status and the API's own message, never request headers or the key."""
        status = getattr(e, "status_code", None)
        body = e.body if isinstance(e.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        text = self._redact(str(error.get("message") or e.message or type(e).__name__))
        prefix = (
            f"Anthropic API error ({status})"
            if status
            else f"Anthropic API error ({type(e).__name__})"
        )
        return _clip(f"{prefix}: {text}")

    def _redact(self, text: str) -> str:
        key = self.settings.anthropic_api_key
        if key is not None and key.get_secret_value().strip():
            text = text.replace(key.get_secret_value().strip(), "[redacted]")
        return _KEY_LIKE.sub("[redacted]", text)

    def _record(
        self, key: CassetteKey, model: str, request: Request[Any], parsed: BaseModel, msg: Any
    ) -> None:
        schema = output_schema(request.output)
        self.cassettes.save(
            key,
            CassetteEntry(
                output=parsed.model_dump(mode="json"),
                model=model,
                request_hash=request_hash(model, request.system, request.messages, schema),
                usage=pricing.token_counts(msg.usage),
                stop_reason=msg.stop_reason,
            ),
        )

    def _usage(
        self, stage: LlmStage, model: str, msg: Any, latency_ms: int, *, recorded: bool
    ) -> LlmUsageData:
        tokens = pricing.token_counts(msg.usage)
        cost = pricing.cost_usd(model, tokens, warn=self._warn_once)
        self.run_cost_usd += cost
        return LlmUsageData(
            stage=stage,
            model=model,
            cassette="recorded" if recorded else "off",
            **tokens,
            cost_usd=round(cost, 8),
            run_cost_usd=round(self.run_cost_usd, 8),
            latency_ms=latency_ms,
            stop_reason=msg.stop_reason,
        )

    # -- replay ---------------------------------------------------------------

    def _replay[T: BaseModel](
        self, stage: LlmStage, config: StageModelConfig, request: Request[T], key: CassetteKey
    ) -> LlmResult[T]:
        try:
            entry = self.cassettes.load(key)
        except LlmError as e:
            if e.kind == "cassette_miss":
                miss = LlmUsageData(
                    stage=stage,
                    model=config.model,
                    cassette="miss",
                    run_cost_usd=round(self.run_cost_usd, 8),
                )
                self._report(miss, key)
            raise
        where = key.relpath().as_posix()
        try:
            parsed = request.output.model_validate(entry.output)
        except ValidationError as e:
            raise LlmError(
                f"cassette {where} does not fit {request.output.__name__}",
                detail=_clip(str(e), 2000),
            ) from None
        schema = output_schema(request.output)
        expected = request_hash(config.model, request.system, request.messages, schema)
        if entry.request_hash and entry.request_hash != expected:
            self._on_warn(
                f"cassette {where} was recorded for a different prompt; replaying it anyway"
            )
        usage = LlmUsageData(
            stage=stage,
            model=entry.model or config.model,
            cassette="hit",
            **pricing.token_counts(entry.usage or {}),
            run_cost_usd=round(self.run_cost_usd, 8),
            stop_reason=entry.stop_reason,
        )
        self._report(usage, key)
        return LlmResult(parsed=parsed, usage=usage, conversation=conversation(request, parsed))

    # -- callbacks ------------------------------------------------------------

    def _report(self, usage: LlmUsageData, key: CassetteKey) -> None:
        if self._on_usage is not None:
            self._on_usage(usage, key)

    def _warn_once(self, text: str) -> None:
        if text not in self._warned:
            self._warned.add(text)
            self._on_warn(text)
