"""``RunContext``: what a pipeline sees of its run.

A pipeline is ``async def pipeline(ctx: RunContext) -> None``. It moves the
run through its states with ``ctx.transition``, wraps every unit of work in
``ctx.step`` (or ``ctx.function`` per selected function) and never writes
terminal events: the orchestrator does that once the pipeline returns,
raises or is cancelled.

Steps close themselves. A ``ctx.step`` block that raises (including
``CancelledError``) still emits its ``*.completed`` with ``ok=false`` on the
way out, innermost first, so the grammar holds no matter how the pipeline
ends. The orchestrator closes anything left open as a last resort.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel

from netzero import __version__
from netzero.config import Settings
from netzero.errors import error_info
from netzero.events import (
    DATA_CLASSES,
    STARTED_TO_COMPLETED,
    TERMINAL_STATES,
    CandidateId,
    ErrorInfo,
    EventBase,
    FunctionCompletedData,
    FunctionInfo,
    FunctionOutcome,
    FunctionStartedData,
    LogData,
    OpenStep,
    RunCreatedData,
    RunDetail,
    RunMode,
    RunSelectionConfirmedData,
    RunSettings,
    RunSource,
    RunState,
    RunStateChangedData,
    StepDone,
)
from netzero.pipeline.bus import EventBus, LogSink, now_ms
from netzero.pipeline.projection import Projection
from netzero.pipeline.store import RunPaths, RunStore
from netzero.sandbox.procs import ProcRegistry

CancelReason = Literal["user", "shutdown"]

TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"cloning"}),
    "cloning": frozenset({"installing"}),
    "installing": frozenset({"discovering"}),
    "discovering": frozenset({"triaging"}),
    "triaging": frozenset({"awaiting_selection"}),
    "awaiting_selection": frozenset({"optimizing"}),
    "optimizing": frozenset({"finalizing"}),
    "finalizing": frozenset({"completed"}),
}
"""Forward transitions. Any non-terminal state may also end in failed, cancelled or interrupted."""

# Events after which run.json is rewritten immediately; others are debounced.
PERSIST_NOW = frozenset(
    {
        "run.created",
        "run.state_changed",
        "run.power.detected",
        "run.triage.completed",
        "run.discovery.completed",
        "run.selection.confirmed",
        "function.completed",
        "run.artifacts.completed",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.interrupted",
    }
)
PERSIST_DEBOUNCE_S = 0.5


def can_transition(frm: str, to: str) -> bool:
    if frm in TERMINAL_STATES:
        return False
    if to in ("failed", "cancelled", "interrupted"):
        return True
    return to in TRANSITIONS.get(frm, frozenset())


def run_settings(settings: Settings) -> RunSettings:
    return RunSettings(
        max_functions=settings.max_functions,
        preselect=settings.preselect,
        n_trials=settings.n_trials,
        trial_target_s=settings.trial_target_s,
        alpha=settings.alpha,
        min_effect_pct=settings.min_effect_pct,
        candidates=["A", "B", "C"],
        country_iso=settings.country,
    )


class Step:
    """Handle yielded by ``ctx.step``: set the outcome of the step before the block ends."""

    def __init__(self) -> None:
        self.ok_flag = True
        self.error: ErrorInfo | None = None
        self.fields: dict[str, Any] = {}

    def ok(self, **fields: Any) -> None:
        self.ok_flag = True
        self.fields.update(fields)

    def fail(self, error: ErrorInfo | BaseException | str | None = None, **fields: Any) -> None:
        """Mark the step failed. ``error`` is for infrastructure errors; a domain
        failure (e.g. tests failed) passes ``error=None`` and carries its result in fields."""
        self.ok_flag = False
        if isinstance(error, BaseException):
            error = error_info(error)
        elif isinstance(error, str):
            error = ErrorInfo(kind="internal", message=error)
        self.error = error
        self.fields.update(fields)


class FunctionScope:
    """Handle yielded by ``ctx.function``: record the outcome before the block ends."""

    def __init__(self, function_id: str):
        self.function_id = function_id
        self.outcome: FunctionOutcome | None = None
        self.fields: dict[str, Any] = {}

    def complete(self, outcome: FunctionOutcome, **fields: Any) -> None:
        self.outcome = outcome
        self.fields = fields


class RunContext:
    def __init__(
        self,
        *,
        run_id: str,
        paths: RunPaths,
        settings: Settings,
        store: RunStore,
        mode: RunMode,
        source: RunSource,
        auto_select: bool = False,
        created_ts: int | None = None,
        start_seq: int = 0,
        projection: Projection | None = None,
        clock: Callable[[], int] | None = None,
    ):
        self.run_id = run_id
        self.paths = paths
        self.settings = settings
        self.store = store
        self.mode: RunMode = mode
        self.source = source
        self.auto_select = auto_select
        # one clock for event timestamps and step durations (tests and the
        # synthetic replay pass a scaled clock)
        self.clock: Callable[[], int] = clock or now_ms
        self.created_ts = created_ts if created_ts is not None else self.clock()
        self.projection = projection or Projection.empty(
            run_id, created_ts=self.created_ts, source=source, mode=mode
        )
        self.state: RunState = self.projection.detail.state
        self.bus = EventBus(
            run_id, paths.events, start_seq=start_seq, on_event=self._on_event, clock=self.clock
        )
        self.procs = ProcRegistry(paths.procs)
        self.cancel_reason: CancelReason | None = None
        self.open_at_cancel: list[OpenStep] = []
        self.t0 = self.clock()
        self.llm_cost_usd = 0.0
        self._selection: asyncio.Future[list[str]] | None = None
        self._sinks: set[LogSink] = set()
        self._persist_handle: asyncio.TimerHandle | None = None
        self._closed = False

    # -- events ------------------------------------------------------------------

    @property
    def detail(self) -> RunDetail:
        return self.projection.detail

    @property
    def last_seq(self) -> int:
        return self.bus.last_seq

    def emit(
        self,
        type: str,
        data: BaseModel | dict | None = None,
        *,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
    ) -> EventBase:
        return self.bus.emit(
            type, data, function_id=function_id, candidate_id=candidate_id, attempt=attempt
        )

    def emit_created(self) -> EventBase:
        return self.emit(
            "run.created",
            RunCreatedData(
                netzero_version=__version__,
                mode=self.mode,
                source=self.source,
                models=self.settings.stages.models(),  # type: ignore[arg-type]
                settings=run_settings(self.settings),
                assumptions=self.settings.assumptions(),
            ),
        )

    def _on_event(self, ev: EventBase) -> None:
        self.projection.apply(ev)
        if ev.type in PERSIST_NOW:  # type: ignore[attr-defined]
            self.persist()
        elif self._persist_handle is None and not self._closed:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.persist()
                return
            self._persist_handle = loop.call_later(PERSIST_DEBOUNCE_S, self.persist)

    def persist(self) -> None:
        if self._persist_handle is not None:
            self._persist_handle.cancel()
            self._persist_handle = None
        self.store.write_detail(self.projection.detail)

    # -- state -------------------------------------------------------------------

    def transition(self, to: RunState, reason: str | None = None) -> None:
        frm = self.state
        if not can_transition(frm, to):
            raise RuntimeError(f"invalid run transition {frm} -> {to}")
        self.state = to
        self.emit(
            "run.state_changed", RunStateChangedData(from_state=frm, to_state=to, reason=reason)
        )

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    # -- steps -------------------------------------------------------------------

    def _abort_error(self, exc: BaseException) -> ErrorInfo:
        if isinstance(exc, asyncio.CancelledError):
            if self.cancel_reason == "shutdown":
                return ErrorInfo(kind="interrupted", message="server shut down")
            if self.cancel_reason == "user":
                return ErrorInfo(kind="cancelled", message="cancelled by user")
            return ErrorInfo(kind="cancelled", message="cancelled")
        return error_info(exc)

    def _elapsed_ms(self, t0: int) -> int:
        return max(0, self.clock() - t0)

    def elapsed_ms(self) -> int:
        """Milliseconds since the run (or this process's share of it) started."""
        return self._elapsed_ms(self.t0)

    @asynccontextmanager
    async def step(
        self,
        started_type: str,
        started_data: BaseModel | dict | None = None,
        *,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
    ) -> AsyncIterator[Step]:
        """Emit ``X.started``, run the block, emit exactly one ``X.completed``.

        Exceptions propagate after the ``ok=false`` completion is written.
        """
        completed = STARTED_TO_COMPLETED[started_type]
        data_cls = DATA_CLASSES[completed]
        if not issubclass(data_cls, StepDone):
            raise TypeError(f"{started_type} is not a StepDone step; use ctx.function")
        scope = {"function_id": function_id, "candidate_id": candidate_id, "attempt": attempt}
        self.emit(started_type, started_data, **scope)
        t0 = self.clock()
        st = Step()
        try:
            yield st
        except BaseException as exc:
            if self._still_open(started_type, scope):
                self.emit(
                    completed,
                    _completed_data(
                        data_cls,
                        ok=False,
                        duration_ms=self._elapsed_ms(t0),
                        error=self._abort_error(exc),
                        fields=st.fields,
                    ),
                    **scope,
                )
            raise
        else:
            # close_steps may already have closed it (e.g. a candidate task that
            # outlived its function scope)
            if not self._still_open(started_type, scope):
                return
            self.emit(
                completed,
                _completed_data(
                    data_cls,
                    ok=st.ok_flag,
                    duration_ms=self._elapsed_ms(t0),
                    error=st.error,
                    fields=st.fields,
                ),
                **scope,
            )

    def _still_open(self, started_type: str, scope: dict) -> bool:
        return (
            not self.bus.terminal
            and not self.bus.closed
            and self.bus.is_open(
                started_type, scope["function_id"], scope["candidate_id"], scope["attempt"]
            )
        )

    @asynccontextmanager
    async def function(
        self, info: FunctionInfo, *, index: int, total: int
    ) -> AsyncIterator[FunctionScope]:
        """One selected function: ``function.started`` ... ``function.completed``.

        A non-cancel exception inside the block is logged and becomes outcome
        ``failed``; the run moves on to the next function. Steps of this function
        still open when the block ends are closed first.
        """
        fid = info.function_id
        self.emit(
            "function.started",
            FunctionStartedData(index=index, total=total, info=info),
            function_id=fid,
        )
        t0 = self.clock()
        fs = FunctionScope(fid)
        try:
            yield fs
        except asyncio.CancelledError as exc:
            err = self._abort_error(exc)
            self.close_steps(err, function_id=fid)
            if self._still_open(
                "function.started", {"function_id": fid, "candidate_id": None, "attempt": None}
            ):
                self.emit(
                    "function.completed",
                    FunctionCompletedData(
                        outcome="cancelled", reason=err.message, duration_ms=self._elapsed_ms(t0)
                    ),
                    function_id=fid,
                )
            raise
        except Exception as exc:
            err = error_info(exc)
            self.log_exception("orchestrator", exc, function_id=fid)
            self.close_steps(
                ErrorInfo(kind="cancelled", message=f"function failed: {err.message}"),
                function_id=fid,
            )
            self.emit(
                "function.completed",
                FunctionCompletedData(
                    outcome="failed", reason=err.message, duration_ms=self._elapsed_ms(t0)
                ),
                function_id=fid,
            )
        else:
            self.close_steps(ErrorInfo(kind="cancelled", message="step abandoned"), function_id=fid)
            if fs.outcome is None:
                data = FunctionCompletedData(
                    outcome="failed", reason="no outcome recorded", duration_ms=self._elapsed_ms(t0)
                )
            else:
                try:
                    data = FunctionCompletedData(
                        outcome=fs.outcome, duration_ms=self._elapsed_ms(t0), **fs.fields
                    )
                except Exception as exc:  # an invalid summary fails this function, not the run
                    self.log_exception("orchestrator", exc, function_id=fid)
                    data = FunctionCompletedData(
                        outcome="failed",
                        reason="invalid function.completed fields",
                        duration_ms=self._elapsed_ms(t0),
                    )
            self.emit("function.completed", data, function_id=fid)

    def close_steps(self, error: ErrorInfo, *, function_id: str | None = None) -> list[OpenStep]:
        """Close still-open steps of one function (excluding ``function.started``)."""
        if self.bus.terminal or self.bus.closed:
            return []
        return self.bus.close_open_steps(
            error,
            where=lambda st: st.function_id == function_id and st.type != "function.started",
        )

    # -- selection ---------------------------------------------------------------

    async def wait_selection(self) -> list[str]:
        """Block in ``awaiting_selection`` until ``select`` is called."""
        loop = asyncio.get_running_loop()
        self._selection = loop.create_future()
        try:
            return await self._selection
        finally:
            self._selection = None

    @property
    def awaiting_selection(self) -> bool:
        return self._selection is not None and not self._selection.done()

    def select(self, function_ids: list[str]) -> None:
        if not self.awaiting_selection:
            raise RuntimeError("run is not awaiting a selection")
        self._selection.set_result(function_ids)  # type: ignore[union-attr]

    async def confirm_selection(self, preselected: list[str]) -> list[str]:
        """``awaiting_selection`` -> ``run.selection.confirmed`` -> ``optimizing``.

        With ``auto_select`` the preselected functions are confirmed at once;
        the run still passes through ``awaiting_selection`` so every log has
        the same shape.
        """
        self.transition("awaiting_selection")
        if self.auto_select:
            ids, auto = list(preselected), True
        else:
            ids, auto = await self.wait_selection(), False
        ids = list(dict.fromkeys(ids))  # a function is optimized once, whoever selected it
        self.emit("run.selection.confirmed", RunSelectionConfirmedData(function_ids=ids, auto=auto))
        self.transition("optimizing")
        return ids

    # -- logs + costs ------------------------------------------------------------

    def log(
        self,
        source: str,
        lines: list[str] | str,
        *,
        level: str = "info",
        stream: str | None = None,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
    ) -> None:
        if isinstance(lines, str):
            lines = lines.splitlines() or [""]
        if self.mode == "record" and level == "debug":
            return
        self.emit(
            "log",
            LogData(level=level, source=source, stream=stream, lines=lines),  # type: ignore[arg-type]
            function_id=function_id,
            candidate_id=candidate_id,
            attempt=attempt,
        )

    def log_exception(self, source: str, exc: BaseException, **scope: Any) -> None:
        tb = traceback.format_exception(exc)
        lines = "".join(tb).rstrip().splitlines()[-40:]
        self.log(source, lines, level="error", **scope)

    def log_sink(
        self,
        source: str,
        *,
        level: str = "info",
        stream: str | None = None,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
        log_name: str | None = None,
    ) -> LogSink:
        sink = LogSink(
            self.bus,
            source,
            level=level,
            stream=stream,
            function_id=function_id,
            candidate_id=candidate_id,
            attempt=attempt,
            log_file=self.paths.logs / log_name if log_name else None,
            enabled=not (self.mode == "record" and level == "debug"),
        )
        self._sinks.add(sink)
        return sink

    def add_llm_cost(self, cost_usd: float) -> float:
        self.llm_cost_usd += cost_usd
        return self.llm_cost_usd

    # -- teardown ----------------------------------------------------------------

    def flush_logs(self) -> None:
        for sink in list(self._sinks):
            sink.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sink in list(self._sinks):
            try:
                sink.close()
            except Exception:
                pass
        self._sinks.clear()
        self.persist()
        self.bus.close()


def _completed_data(
    data_cls: type[BaseModel], *, ok: bool, duration_ms: int, error: ErrorInfo | None, fields: dict
) -> BaseModel:
    try:
        return data_cls(ok=ok, duration_ms=duration_ms, error=error, **fields)
    except Exception as exc:
        if not fields:
            raise
        # an invalid artifact must not break the grammar: close without it, but say why
        why = f"invalid {data_cls.__name__} fields: {exc}"[:2000]
        if error is None:
            error = ErrorInfo(kind="internal", message=f"invalid {data_cls.__name__} fields")
        detail = "\n\n".join(filter(None, [error.detail, why]))
        return data_cls(
            ok=False,
            duration_ms=duration_ms,
            error=error.model_copy(update={"detail": detail}),
        )
