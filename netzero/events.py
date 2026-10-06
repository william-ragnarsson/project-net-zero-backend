"""The run event contract: every byte the frontend and the CLI ever see.

Each event is one line in ``runs/<id>/events.jsonl`` and one SSE message.
Envelope fields come first (``seq`` is always the first key) so resume can
read a line's seq cheaply. ``web/src/gen/events.ts`` is generated from this
module via ``netzero export-schema`` + ``make types``.

Grammar:
- ``seq`` starts at 1 and is gapless per run.
- every ``X.started`` is followed by exactly one ``X.completed`` with the same
  (function_id, candidate_id, attempt); completed payloads extend ``StepDone``
  and carry the full artifact (null fields when ``ok`` is false).
- the terminal run event is the last line of the file.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

CandidateId = Literal["A", "B", "C"]
CANDIDATE_IDS: tuple[CandidateId, ...] = ("A", "B", "C")

RunState = Literal[
    "created",
    "cloning",
    "installing",
    "discovering",
    "triaging",
    "awaiting_selection",
    "optimizing",
    "finalizing",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "interrupted"})

RunMode = Literal["live", "demo", "record"]
LlmStage = Literal["triage", "tests", "tests_repair", "rewrite", "rewrite_repair"]

FunctionOutcome = Literal[
    "accepted",
    "no_significant_win",
    "all_rejected",
    "reverted",
    "skipped_untestable",
    "skipped_capture",
    "failed",
    "cancelled",
]
FUNCTION_OUTCOMES: tuple[str, ...] = FunctionOutcome.__args__  # type: ignore[attr-defined]

RejectReason = Literal[
    "syntax",
    "static_rule",
    "tests_failed",
    "differential_mismatch",
    "timeout",
    "llm_error",
    "identical",
    "bench_failed",
    "measurement_inconsistent",
]

ErrorKind = Literal[
    "timeout",
    "cancelled",
    "interrupted",
    "llm_error",
    "llm_refusal",
    "cassette_miss",
    "sandbox_error",
    "validation",
    "clone_error",
    "env_error",
    "bench_error",
    "internal",
]

PowerSource = Literal["rapl", "powermetrics", "tdp_estimate"]
PowerBadge = Literal["measured", "calibrated", "estimated"]
PowerMethod = Literal["codecarbon_task", "codecarbon_model"]

FunctionKind = Literal["function", "method", "staticmethod", "classmethod"]
Rating = Literal["high", "medium", "low", "none"]


class _Model(BaseModel):
    """Base for every contract model.

    ``json_schema_serialization_defaults_required`` makes fields with defaults
    required in the serialization schema, so generated TS types have no
    spurious ``?`` on fields that are always present in the JSON.
    """

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class NoData(_Model):
    pass


class ErrorInfo(_Model):
    kind: ErrorKind
    message: str
    detail: str | None = None


class StepDone(_Model):
    """Base of every ``*.completed`` payload."""

    ok: bool
    duration_ms: int
    error: ErrorInfo | None = None


# ---------------------------------------------------------------------------
# Shared payload models
# ---------------------------------------------------------------------------


class GridInfo(_Model):
    country_iso: str  # ISO3, or "WORLD"
    kg_per_kwh: float
    source: str  # e.g. "codecarbon world average", "codecarbon energy mix (SWE)"


class PowerInfo(_Model):
    power_source: PowerSource
    badge: PowerBadge
    method: PowerMethod
    cpu_model: str
    tdp_w: float
    cpu_count: int
    p_core_w: float  # power attributed to one busy core
    p_ram_w: float  # CodeCarbon RAM model for this process
    grid: GridInfo
    notes: list[str] = []


class Assumptions(_Model):
    """Prices used client-side to turn per-call savings into yearly figures."""

    eur_per_kwh: float
    eu_ets_eur_per_t: float
    vcm_eur_per_t: float
    default_calls_per_year: int
    notes: list[str] = []


class RunSource(_Model):
    kind: Literal["github", "demo"]
    url: str | None = None
    ref: str | None = None


class RunSettings(_Model):
    max_functions: int
    preselect: int
    n_trials: int
    trial_target_s: float
    alpha: float
    min_effect_pct: float
    candidates: list[CandidateId]
    country_iso: str | None = None


class TriageItem(_Model):
    function_id: str  # "pkg.mod:Qual.name"
    module: str
    qualname: str
    kind: FunctionKind
    file: str  # repo-relative POSIX path
    line: int
    end_line: int
    loc: int
    import_line: str
    call_hint: str
    heuristic_score: float
    llm_potential: Rating | None = None
    llm_testability: Rating | None = None
    score: float
    reasons: list[str] = []
    skip_reason: str | None = None
    preselected: bool = False


class FunctionInfo(_Model):
    function_id: str
    module: str
    qualname: str
    kind: FunctionKind
    file: str
    line: int
    end_line: int
    loc: int
    import_line: str
    call_hint: str
    source: str


class PytestFailure(_Model):
    nodeid: str
    message: str
    tb_tail: str = ""


class PytestResult(_Model):
    exit_code: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_s: float
    failures: list[PytestFailure] = []
    output_tail: str = ""


class Mismatch(_Model):
    sample_idx: int
    kind: Literal["return", "exception", "mutation", "slowdown"]
    path: str = ""
    expected: str = ""
    actual: str = ""


class DiffCheckResult(_Model):
    ok: bool
    n_samples: int
    mismatches: list[Mismatch] = []
    slowdown_ratio: float | None = None


class Interval(_Model):
    mean: float
    ci_low: float
    ci_high: float
    std: float


class CI(_Model):
    lo: float
    hi: float


class MeasureStats(_Model):
    """Per-call measurements for one arm (original or candidate) of a bench."""

    g_per_call: Interval  # grams CO2e per call
    kwh_per_call: Interval
    kwh_cpu_per_call: float  # mean CPU share of kwh_per_call
    kwh_ram_per_call: float  # mean RAM share of kwh_per_call
    cpu_s_per_call: Interval
    wall_s_per_call: Interval
    n_trials: int
    calls_per_trial: int
    trials_g: list[float]  # per-call grams, one value per trial


class BenchStats(_Model):
    original: MeasureStats
    candidate: MeasureStats
    delta_pct: float  # -37.2 == 37.2% less CO2 per call
    delta_ci_pct: CI  # 95% CI of delta_pct
    p_value: float  # one-sided Welch t on log per-call g
    p_holm: float | None = None  # filled in by function.decision
    significant: bool  # this candidate passes the gate on its raw p
    n_trials: int
    calls_per_trial: int
    cpu_time_delta_pct: float
    sanity_ok: bool
    power: PowerInfo
    g_saved_per_1m_calls: float
    kwh_saved_per_1m_calls: float


class TestFile(_Model):
    __test__ = False  # not a pytest class

    path: str  # repo-relative path the file is written to
    code: str
    test_names: list[str]
    workload_test: str
    notes: str = ""
    diagnosis: str = ""


class CandidateCode(_Model):
    code: str
    new_imports: list[str] = []
    strategy: str
    rationale: str
    diff: str
    diagnosis: str = ""


class StaticCheck(_Model):
    ok: bool
    problems: list[str] = []


class Calibration(_Model):
    calls_per_trial: int
    est_call_s: float
    n_samples: int


class RankingEntry(_Model):
    candidate_id: CandidateId
    status: Literal["eligible", "rejected"]
    delta_pct: float | None = None
    delta_ci_pct: CI | None = None
    p_value: float | None = None
    p_holm: float | None = None
    significant: bool = False
    reason: str = ""


class DecisionRule(_Model):
    alpha: float
    min_effect_pct: float
    correction: Literal["holm"] = "holm"


class PackageInfo(_Model):
    name: str
    version: str


class ImportProbe(_Model):
    ok_modules: list[str] = []
    failed: dict[str, str] = {}


class ArtifactRef(_Model):
    url: str
    bytes: int
    sha256: str


class PatchRef(ArtifactRef):
    files_changed: int


class FunctionDiffRef(_Model):
    function_id: str
    url: str


class OpenStep(_Model):
    type: str  # the *.started event type left open
    function_id: str | None = None
    candidate_id: CandidateId | None = None
    attempt: int | None = None


class RunTotals(_Model):
    functions_total: int
    functions_done: int
    counts_by_outcome: dict[FunctionOutcome, int]
    mean_reduction_pct: float | None = None  # over accepted functions
    g_saved_per_1m_calls: float  # sum over accepted functions
    kwh_saved_per_1m_calls: float
    llm_cost_usd: float
    duration_ms: int


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------


class RunCreatedData(_Model):
    schema_version: int = SCHEMA_VERSION
    netzero_version: str
    mode: RunMode
    source: RunSource
    models: dict[LlmStage, str]
    settings: RunSettings
    assumptions: Assumptions


class RunStateChangedData(_Model):
    from_state: RunState | None
    to_state: RunState
    reason: str | None = None


class RunPowerDetectedData(_Model):
    power: PowerInfo


class RunPowerCalibrationCompletedData(StepDone):
    p_idle_w: float | None = None
    p_busy_w: float | None = None
    p_core_w: float | None = None
    cached: bool = False


class RunCloneStartedData(_Model):
    url: str | None
    ref: str | None = None


class RunCloneCompletedData(StepDone):
    commit_sha: str | None = None
    n_files: int | None = None
    n_py_files: int | None = None
    size_bytes: int | None = None


class RunEnvStartedData(_Model):
    python_request: str
    dependency_sources: list[str] = []


class RunEnvCompletedData(StepDone):
    python_version: str | None = None
    packages: list[PackageInfo] = []
    import_probe: ImportProbe | None = None


class RunDiscoveryCompletedData(StepDone):
    n_files: int = 0
    n_functions: int = 0
    n_test_files: int = 0
    import_roots: list[str] = []
    heuristic_ranked: list[TriageItem] = []


class RunTriageStartedData(_Model):
    n_candidates: int
    llm: bool


class RunTriageCompletedData(StepDone):
    items: list[TriageItem] = []
    preselected: list[str] = []
    llm_used: bool = False


class RunSelectionConfirmedData(_Model):
    function_ids: list[str]  # execution order
    auto: bool = False


class RunArtifactsCompletedData(StepDone):
    patch: PatchRef | None = None
    zip: ArtifactRef | None = None
    function_diffs: list[FunctionDiffRef] = []


class RunCompletedData(_Model):
    summary: RunTotals


class RunFailedData(_Model):
    stage: RunState
    error: ErrorInfo


class RunCancelledData(_Model):
    at_state: RunState
    reason: Literal["user"] = "user"


class RunInterruptedData(_Model):
    previous_state: RunState
    open_steps: list[OpenStep] = []


class FunctionStartedData(_Model):
    index: int  # 0-based position in the selection
    total: int
    info: FunctionInfo


class TestsWriteStartedData(_Model):
    kind: Literal["write", "repair"]
    failures_in: list[str] = []


class TestsWriteCompletedData(StepDone):
    test_file: TestFile | None = None


class TestsRunStartedData(_Model):
    target: Literal["original"] = "original"
    repeat: int = 0  # 0 first run, 1 flakiness re-run


class TestsRunCompletedData(StepDone):
    result: PytestResult | None = None
    flaky: bool = False


class CaptureCompletedData(StepDone):
    n_calls: int = 0
    n_kept: int = 0
    n_unpicklable: int = 0
    mutates_args: bool = False
    raises: bool = False
    deterministic: bool = True
    total_bytes: int = 0
    previews: list[str] = []


class BaselineCompletedData(StepDone):
    calibration: Calibration | None = None
    original: MeasureStats | None = None
    cv_pct: float | None = None
    power: PowerInfo | None = None


class FunctionDecisionData(_Model):
    outcome: Literal["winner", "no_significant_win", "all_rejected"]
    winner: CandidateId | None = None
    ranking: list[RankingEntry] = []
    rule: DecisionRule


class MergeCompletedData(StepDone):
    reverted: bool = False
    suite: PytestResult | None = None
    differential: DiffCheckResult | None = None
    commit_sha: str | None = None
    diff: str | None = None


class FunctionCompletedData(_Model):
    outcome: FunctionOutcome
    winner: CandidateId | None = None
    delta_pct: float | None = None
    delta_ci_pct: CI | None = None
    g_saved_per_1m_calls: float | None = None
    kwh_saved_per_1m_calls: float | None = None
    diff: str | None = None
    reason: str = ""
    duration_ms: int


class CandidateWriteStartedData(_Model):
    kind: Literal["write", "repair"]
    strategy_hint: str


class CandidateWriteCompletedData(StepDone):
    candidate: CandidateCode | None = None


class CandidateCheckCompletedData(StepDone):
    static: StaticCheck | None = None
    tests: PytestResult | None = None
    differential: DiffCheckResult | None = None


class CandidateRejectedData(_Model):
    reason: RejectReason
    detail: str = ""


class CandidateBenchQueuedData(_Model):
    position: int  # 0 == next on the bench


class CandidateBenchStartedData(_Model):
    calls_per_trial: int
    n_trials: int


class CandidateBenchCompletedData(StepDone):
    stats: BenchStats | None = None


class LlmUsageData(_Model):
    stage: LlmStage
    model: str
    cassette: Literal["off", "hit", "recorded", "miss"]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    run_cost_usd: float = 0.0
    latency_ms: int = 0
    stop_reason: str | None = None


class LogData(_Model):
    level: Literal["debug", "info", "warn", "error"]
    source: Literal["clone", "env", "pytest", "bench", "llm", "orchestrator", "sandbox"]
    stream: Literal["stdout", "stderr"] | None = None
    lines: list[str]
    truncated: bool = False


# ---------------------------------------------------------------------------
# Envelope + events
# ---------------------------------------------------------------------------


class EventBase(_Model):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    seq: int  # 1-based, gapless per run
    ts: int  # unix epoch ms
    run_id: str
    function_id: str | None = None
    candidate_id: CandidateId | None = None
    attempt: int | None = None  # 0 first try; 1..2 test repairs; 1 candidate repair


# -- run ----------------------------------------------------------------------


class RunCreated(EventBase):
    type: Literal["run.created"] = "run.created"
    data: RunCreatedData


class RunStateChanged(EventBase):
    type: Literal["run.state_changed"] = "run.state_changed"
    data: RunStateChangedData


class RunPowerDetected(EventBase):
    type: Literal["run.power.detected"] = "run.power.detected"
    data: RunPowerDetectedData


class RunPowerCalibrationStarted(EventBase):
    type: Literal["run.power.calibration.started"] = "run.power.calibration.started"
    data: NoData = NoData()


class RunPowerCalibrationCompleted(EventBase):
    type: Literal["run.power.calibration.completed"] = "run.power.calibration.completed"
    data: RunPowerCalibrationCompletedData


class RunCloneStarted(EventBase):
    type: Literal["run.clone.started"] = "run.clone.started"
    data: RunCloneStartedData


class RunCloneCompleted(EventBase):
    type: Literal["run.clone.completed"] = "run.clone.completed"
    data: RunCloneCompletedData


class RunEnvStarted(EventBase):
    type: Literal["run.env.started"] = "run.env.started"
    data: RunEnvStartedData


class RunEnvCompleted(EventBase):
    type: Literal["run.env.completed"] = "run.env.completed"
    data: RunEnvCompletedData


class RunDiscoveryStarted(EventBase):
    type: Literal["run.discovery.started"] = "run.discovery.started"
    data: NoData = NoData()


class RunDiscoveryCompleted(EventBase):
    type: Literal["run.discovery.completed"] = "run.discovery.completed"
    data: RunDiscoveryCompletedData


class RunTriageStarted(EventBase):
    type: Literal["run.triage.started"] = "run.triage.started"
    data: RunTriageStartedData


class RunTriageCompleted(EventBase):
    type: Literal["run.triage.completed"] = "run.triage.completed"
    data: RunTriageCompletedData


class RunSelectionConfirmed(EventBase):
    type: Literal["run.selection.confirmed"] = "run.selection.confirmed"
    data: RunSelectionConfirmedData


class RunArtifactsStarted(EventBase):
    type: Literal["run.artifacts.started"] = "run.artifacts.started"
    data: NoData = NoData()


class RunArtifactsCompleted(EventBase):
    type: Literal["run.artifacts.completed"] = "run.artifacts.completed"
    data: RunArtifactsCompletedData


class RunCompleted(EventBase):
    type: Literal["run.completed"] = "run.completed"
    data: RunCompletedData


class RunFailed(EventBase):
    type: Literal["run.failed"] = "run.failed"
    data: RunFailedData


class RunCancelled(EventBase):
    type: Literal["run.cancelled"] = "run.cancelled"
    data: RunCancelledData


class RunInterrupted(EventBase):
    type: Literal["run.interrupted"] = "run.interrupted"
    data: RunInterruptedData


# -- function -------------------------------------------------------------------


class FunctionStarted(EventBase):
    type: Literal["function.started"] = "function.started"
    data: FunctionStartedData


class FunctionTestsWriteStarted(EventBase):
    type: Literal["function.tests.write.started"] = "function.tests.write.started"
    data: TestsWriteStartedData


class FunctionTestsWriteCompleted(EventBase):
    type: Literal["function.tests.write.completed"] = "function.tests.write.completed"
    data: TestsWriteCompletedData


class FunctionTestsRunStarted(EventBase):
    type: Literal["function.tests.run.started"] = "function.tests.run.started"
    data: TestsRunStartedData


class FunctionTestsRunCompleted(EventBase):
    type: Literal["function.tests.run.completed"] = "function.tests.run.completed"
    data: TestsRunCompletedData


class FunctionCaptureStarted(EventBase):
    type: Literal["function.capture.started"] = "function.capture.started"
    data: NoData = NoData()


class FunctionCaptureCompleted(EventBase):
    type: Literal["function.capture.completed"] = "function.capture.completed"
    data: CaptureCompletedData


class FunctionBaselineStarted(EventBase):
    type: Literal["function.baseline.started"] = "function.baseline.started"
    data: NoData = NoData()


class FunctionBaselineCompleted(EventBase):
    type: Literal["function.baseline.completed"] = "function.baseline.completed"
    data: BaselineCompletedData


class FunctionDecision(EventBase):
    type: Literal["function.decision"] = "function.decision"
    data: FunctionDecisionData


class FunctionMergeStarted(EventBase):
    type: Literal["function.merge.started"] = "function.merge.started"
    data: NoData = NoData()


class FunctionMergeCompleted(EventBase):
    type: Literal["function.merge.completed"] = "function.merge.completed"
    data: MergeCompletedData


class FunctionCompleted(EventBase):
    type: Literal["function.completed"] = "function.completed"
    data: FunctionCompletedData


# -- candidate ------------------------------------------------------------------


class CandidateWriteStarted(EventBase):
    type: Literal["candidate.write.started"] = "candidate.write.started"
    data: CandidateWriteStartedData


class CandidateWriteCompleted(EventBase):
    type: Literal["candidate.write.completed"] = "candidate.write.completed"
    data: CandidateWriteCompletedData


class CandidateCheckStarted(EventBase):
    type: Literal["candidate.check.started"] = "candidate.check.started"
    data: NoData = NoData()


class CandidateCheckCompleted(EventBase):
    type: Literal["candidate.check.completed"] = "candidate.check.completed"
    data: CandidateCheckCompletedData


class CandidateRejected(EventBase):
    type: Literal["candidate.rejected"] = "candidate.rejected"
    data: CandidateRejectedData


class CandidateBenchQueued(EventBase):
    type: Literal["candidate.bench.queued"] = "candidate.bench.queued"
    data: CandidateBenchQueuedData


class CandidateBenchStarted(EventBase):
    type: Literal["candidate.bench.started"] = "candidate.bench.started"
    data: CandidateBenchStartedData


class CandidateBenchCompleted(EventBase):
    type: Literal["candidate.bench.completed"] = "candidate.bench.completed"
    data: CandidateBenchCompletedData


# -- misc -------------------------------------------------------------------------


class LlmUsage(EventBase):
    type: Literal["llm.usage"] = "llm.usage"
    data: LlmUsageData


class Log(EventBase):
    type: Literal["log"] = "log"
    data: LogData


RunEvent = Annotated[
    RunCreated
    | RunStateChanged
    | RunPowerDetected
    | RunPowerCalibrationStarted
    | RunPowerCalibrationCompleted
    | RunCloneStarted
    | RunCloneCompleted
    | RunEnvStarted
    | RunEnvCompleted
    | RunDiscoveryStarted
    | RunDiscoveryCompleted
    | RunTriageStarted
    | RunTriageCompleted
    | RunSelectionConfirmed
    | RunArtifactsStarted
    | RunArtifactsCompleted
    | RunCompleted
    | RunFailed
    | RunCancelled
    | RunInterrupted
    | FunctionStarted
    | FunctionTestsWriteStarted
    | FunctionTestsWriteCompleted
    | FunctionTestsRunStarted
    | FunctionTestsRunCompleted
    | FunctionCaptureStarted
    | FunctionCaptureCompleted
    | FunctionBaselineStarted
    | FunctionBaselineCompleted
    | FunctionDecision
    | FunctionMergeStarted
    | FunctionMergeCompleted
    | FunctionCompleted
    | CandidateWriteStarted
    | CandidateWriteCompleted
    | CandidateCheckStarted
    | CandidateCheckCompleted
    | CandidateRejected
    | CandidateBenchQueued
    | CandidateBenchStarted
    | CandidateBenchCompleted
    | LlmUsage
    | Log,
    Field(discriminator="type"),
]
RunEventAdapter: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)

EVENT_CLASSES: dict[str, type[EventBase]] = {
    cls.model_fields["type"].default: cls
    for cls in RunEvent.__origin__.__args__  # type: ignore[attr-defined]
}
"""Event ``type`` string -> event class."""

DATA_CLASSES: dict[str, type[BaseModel]] = {
    t: cls.model_fields["data"].annotation
    for t, cls in EVENT_CLASSES.items()  # type: ignore[misc]
}
"""Event ``type`` string -> payload class."""

EventType = str

TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
)

STARTED_TO_COMPLETED: dict[str, str] = {
    t: t[: -len(".started")] + ".completed" for t in EVENT_CLASSES if t.endswith(".started")
}
"""Every step pair: ``X.started`` -> ``X.completed``."""


class Heartbeat(_Model):
    """SSE-only keepalive, sent as ``event: heartbeat`` with no id. Never persisted."""

    type: Literal["heartbeat"] = "heartbeat"
    run_id: str
    last_seq: int
    state: RunState
    server_ts: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_event(
    type: str,
    *,
    seq: int,
    ts: int,
    run_id: str,
    data: BaseModel | dict | None = None,
    function_id: str | None = None,
    candidate_id: CandidateId | None = None,
    attempt: int | None = None,
) -> EventBase:
    """Build a validated event of ``type``; ``data`` may be a model or a dict."""
    cls = EVENT_CLASSES[type]
    data_cls = DATA_CLASSES[type]
    if data is None:
        data = data_cls()
    elif isinstance(data, dict):
        data = data_cls.model_validate(data)
    elif not isinstance(data, data_cls):
        raise TypeError(f"{type} expects {data_cls.__name__}, got {type_name(data)}")
    return cls(
        seq=seq,
        ts=ts,
        run_id=run_id,
        function_id=function_id,
        candidate_id=candidate_id,
        attempt=attempt,
        data=data,
    )


def type_name(obj: object) -> str:
    return type(obj).__name__


def dump_event(event: EventBase) -> str:
    """Canonical one-line JSON for ``events.jsonl`` and SSE ``data:``."""
    return event.model_dump_json()


def parse_event(line: str | bytes) -> RunEvent:
    return RunEventAdapter.validate_json(line)


def is_terminal_type(event_type: str) -> bool:
    return event_type in TERMINAL_EVENT_TYPES


def line_seq(line: str | bytes) -> int:
    """Read ``seq`` from a persisted line without a full parse (seq is the first key)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    if not line.startswith('{"seq":'):
        raise ValueError("not an event line")
    end = line.index(",", 7)
    return int(line[7:end])


def line_type(line: str) -> str:
    """Read ``type`` from a persisted line without a full parse."""
    i = line.index('"type":"') + 8
    return line[i : line.index('"', i)]


# ---------------------------------------------------------------------------
# Run records (run.json, REST)
# ---------------------------------------------------------------------------


class RunSummary(_Model):
    id: str
    created_ts: int
    updated_ts: int
    state: RunState
    source: RunSource
    mode: RunMode
    functions_total: int = 0
    functions_done: int = 0
    counts_by_outcome: dict[FunctionOutcome, int] = {}
    mean_reduction_pct: float | None = None
    g_saved_per_1m_calls: float = 0.0
    last_seq: int = 0


class FunctionRecord(_Model):
    function_id: str
    qualname: str
    outcome: FunctionOutcome | None = None  # null while in progress
    winner: CandidateId | None = None
    delta_pct: float | None = None
    delta_ci_pct: CI | None = None
    g_saved_per_1m_calls: float | None = None
    reason: str = ""


class RunDetail(RunSummary):
    models: dict[LlmStage, str] = {}
    settings: RunSettings | None = None
    assumptions: Assumptions | None = None
    power: PowerInfo | None = None
    triage: list[TriageItem] = []
    selection: list[str] | None = None
    functions: list[FunctionRecord] = []
    artifacts: RunArtifactsCompletedData | None = None
    error: ErrorInfo | None = None
    llm_cost_usd: float = 0.0
