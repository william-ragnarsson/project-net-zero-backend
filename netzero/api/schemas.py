"""REST request/response bodies, plus the single-root ``Contract`` for export.

Response models extend the event module's ``_Model`` (every field present in
JSON). Request models use ``_Request`` so defaulted fields stay optional in
the generated TypeScript.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from netzero.events import (
    Heartbeat,
    LlmStage,
    PowerInfo,
    RunDetail,
    RunEvent,
    RunMode,
    RunSummary,
    _Model,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRunRequest(_Request):
    """Exactly one of ``github_url`` / ``demo``."""

    github_url: str | None = None
    demo: bool = False
    ref: str | None = None
    auto_select: bool = False


class SelectRequest(_Request):
    function_ids: list[str] = Field(min_length=1, max_length=20)


class Health(_Model):
    ok: bool
    version: str


class Tools(_Model):
    git: bool
    uv: bool


class ReplayRef(_Model):
    id: str
    title: str
    description: str = ""
    src: str  # URL of the events.jsonl, relative to the site root
    functions: int = 0
    duration_ms: int = 0


class Capabilities(_Model):
    version: str
    has_api_key: bool
    modes: list[RunMode]  # "live" only with a key
    active_run: RunSummary | None
    platform: str
    python: str
    tools: Tools
    power: PowerInfo | None  # cached probe; null until probed
    models: dict[LlmStage, str]
    demo_available: bool


class ApiError(_Model):
    detail: str
    code: Literal[
        "bad_request",
        "invalid_url",
        "no_api_key",
        "run_active",
        "not_found",
        "bad_state",
        "not_ready",
    ]
    active_run: RunSummary | None = None


class Contract(_Model):
    """Single root exported to ``web/src/gen/schema.json``; never sent on the wire."""

    event: RunEvent
    heartbeat: Heartbeat
    run_summary: RunSummary
    run_detail: RunDetail
    capabilities: Capabilities
    health: Health
    replay_ref: ReplayRef
    api_error: ApiError
    create_run_request: CreateRunRequest
    select_request: SelectRequest


def contract_schema() -> dict:
    return Contract.model_json_schema(mode="serialization")
