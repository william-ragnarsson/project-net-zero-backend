"""REST request/response bodies, plus the single-root ``Contract`` for export.

Response models extend the event module's ``_Model`` (every field present in
JSON). Request models use ``_Request`` so defaulted fields stay optional in
the generated TypeScript.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from netzero.events import (
    CandidateId,
    FunctionOutcome,
    Heartbeat,
    LlmStage,
    PowerInfo,
    RejectReason,
    RunDetail,
    RunEvent,
    RunMode,
    RunState,
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
        "no_database",
    ]
    active_run: RunSummary | None = None


# -- /api/history (Postgres; see netzero/history) -------------------------------------


class HistoryTable(_Model):
    name: str
    rows: int
    bytes: int


class StoredEvent(_Model):
    """One row of the ``events`` table, without its payload."""

    position: int  # global, in commit order
    stream_id: str  # "run:<run id>"
    stream_seq: int  # the event's seq
    type: str
    ts: int
    recorded_at: int  # epoch ms the database stored it
    bytes: int  # length of the stored line


class HistoryStatus(_Model):
    enabled: bool  # NETZERO_DATABASE_URL is set
    ok: bool  # connected, migrated, last sync succeeded
    detail: str
    database: str | None = None  # "netzero on 127.0.0.1:54320"; never the password
    server_version: str | None = None
    migrations: list[str] = []
    tables: list[HistoryTable] = []
    head_position: int = 0  # last event stored
    projected_position: int = 0  # last event folded into the read models
    last_sync_ts: int | None = None
    recent: list[StoredEvent] = []  # newest first


class TrendPoint(_Model):
    run_id: str
    created_ts: int
    state: RunState
    g_saved_per_1m_calls: float
    accepted: int


class ProjectSummary(_Model):
    id: str
    name: str  # "owner/repo", or "demo"
    kind: Literal["github", "demo"]
    url: str | None
    runs: int
    runs_completed: int
    last_run_ts: int | None
    last_state: RunState | None
    functions_tracked: int  # distinct functions with a result
    functions_improved: int  # distinct functions accepted at least once
    latest_g_saved_per_1m_calls: float | None  # the newest completed run
    best_g_saved_per_1m_calls: float | None
    trend: list[TrendPoint]  # the last 24 runs, oldest first


class HistoryRun(_Model):
    run_id: str
    created_ts: int
    ended_ts: int | None
    state: RunState
    mode: RunMode
    ref: str | None
    base_sha: str | None  # the commit the run measured
    functions_total: int
    functions_done: int
    counts_by_outcome: dict[FunctionOutcome, int]
    mean_reduction_pct: float | None
    g_saved_per_1m_calls: float
    kwh_saved_per_1m_calls: float
    llm_cost_usd: float
    error: str | None
    events: int


class FunctionPoint(_Model):
    """One function in one run."""

    run_id: str
    created_ts: int
    base_sha: str | None
    outcome: FunctionOutcome | None  # null while in progress
    winner: CandidateId | None
    delta_pct: float | None
    ci_lo: float | None
    ci_hi: float | None
    g_saved_per_1m_calls: float | None
    reason: str


class FunctionHistory(_Model):
    function_id: str
    qualname: str
    module: str | None
    file: str | None
    runs: int
    accepted: int
    best_delta_pct: float | None  # most negative accepted delta
    points: list[FunctionPoint]  # oldest first


class CandidateStat(_Model):
    candidate_id: CandidateId
    hint: str
    proposed: int  # ranked in a decision
    eligible: int  # passed tests and the differential check
    significant: int
    accepted: int  # won and stayed merged


class RejectStat(_Model):
    reason: RejectReason
    count: int


class ProjectDetail(_Model):
    project: ProjectSummary
    runs: list[HistoryRun]  # newest first
    functions: list[FunctionHistory]
    candidates: list[CandidateStat]
    rejections: list[RejectStat]


ActivityType = Literal[
    "run.created",
    "run.selection.confirmed",
    "function.completed",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.interrupted",
]


class ActivityItem(_Model):
    position: int
    seq: int  # within its run, for /replay?at=
    ts: int
    type: ActivityType
    run_id: str
    project_id: str
    project_name: str
    function_id: str | None = None
    outcome: FunctionOutcome | None = None
    winner: CandidateId | None = None
    delta_pct: float | None = None
    g_saved_per_1m_calls: float | None = None
    functions: int | None = None  # selected, or done when the run ends
    accepted: int | None = None
    message: str | None = None  # why it failed, or the function's reason


class ActivityPage(_Model):
    items: list[ActivityItem]  # newest first
    next: str | None  # pass as ?before= for older items


class FunctionDiff(_Model):
    run_id: str
    function_id: str
    outcome: FunctionOutcome
    diff: str | None


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
    history_status: HistoryStatus
    project_summary: ProjectSummary
    project_detail: ProjectDetail
    activity_page: ActivityPage
    function_diff: FunctionDiff


def contract_schema() -> dict:
    return Contract.model_json_schema(mode="serialization")
