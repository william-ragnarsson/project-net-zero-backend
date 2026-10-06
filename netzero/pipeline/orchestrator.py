"""``RunManager``: create, drive, cancel and recover runs.

One run is active at a time (``runs/.active.lock``, shared with CLI
processes). The manager owns the pipeline task and a driver task; the
driver writes the terminal event exactly once, however the pipeline ends:

* returned in ``finalizing``      -> ``completed``
* raised                          -> ``failed``
* cancelled by the user           -> ``cancelled``
* cancelled by shutdown           -> ``interrupted``

Runs left non-terminal by a crash (no process holds their ``.lock``) are
recovered on boot, or lazily when read: leftover process groups are killed,
open steps get synthetic closes and ``run.interrupted`` becomes the last line.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from netzero import paths
from netzero.api.schemas import CreateRunRequest
from netzero.bench.probe import load_cached
from netzero.config import Settings
from netzero.errors import NetzeroError, error_info
from netzero.events import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_STATES,
    ErrorInfo,
    EventBase,
    OpenStep,
    PowerInfo,
    RunCancelledData,
    RunCompletedData,
    RunDetail,
    RunFailedData,
    RunInterruptedData,
    RunMode,
    RunPowerDetectedData,
    RunSource,
    RunState,
    RunSummary,
    line_type,
    parse_event,
)
from netzero.pipeline.bus import now_ms
from netzero.pipeline.clone import InvalidUrl, parse_github_url, validate_ref
from netzero.pipeline.projection import Projection
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import (
    FileLock,
    RunStore,
    read_complete_lines,
    repair_tail,
    to_summary,
)
from netzero.sandbox.procs import kill_leftovers

logger = logging.getLogger("netzero")

PipelineFn = Callable[[RunContext], Awaitable[None]]


class RunRejected(Exception):
    """A command the manager refuses; the API maps ``code`` to an HTTP status."""

    def __init__(self, code: str, detail: str, *, active_run: RunSummary | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.active_run = active_run


def select_pipeline(settings: Settings) -> PipelineFn:
    if settings.fake_pipeline:
        from netzero.pipeline.fake import FakePipeline

        return FakePipeline(
            speed=settings.fake_speed, scenario=settings.fake_scenario, seed=settings.fake_seed
        )
    from netzero.pipeline.flow import run_pipeline

    return run_pipeline


@dataclass
class ActiveRun:
    ctx: RunContext
    active_lock: FileLock
    run_lock: FileLock
    task: asyncio.Task[None] | None = None
    driver: asyncio.Task[None] | None = None
    finished: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class RunManager:
    def __init__(
        self,
        settings: Settings,
        store: RunStore | None = None,
        *,
        pipeline: PipelineFn | None = None,
        power: Callable[[], PowerInfo | None] = load_cached,
        clock: Callable[[], int] | None = None,
    ):
        self.settings = settings
        self.store = store or RunStore(settings.runs_dir)
        self.pipeline = pipeline or select_pipeline(settings)
        self.power = power
        self.clock = clock or now_ms
        self.active: ActiveRun | None = None

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Recover runs a previous process left behind."""
        self.store.runs_dir.mkdir(parents=True, exist_ok=True)
        self.recover_all()

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel the active run (it ends ``interrupted``) and wait for its terminal event."""
        a = self.active
        if a is None:
            return
        self._request_cancel(a, "shutdown")
        try:
            await asyncio.wait_for(asyncio.shield(a.done.wait()), timeout)
        except TimeoutError:
            logger.warning(
                "run %s did not stop within %.0fs; forcing it closed", a.ctx.run_id, timeout
            )
            self._finish(a)

    # -- commands ----------------------------------------------------------------

    async def create(self, req: CreateRunRequest, *, mode: RunMode | None = None) -> RunSummary:
        s = self.settings
        if bool(req.github_url) == bool(req.demo):
            raise RunRejected("bad_request", "pass exactly one of github_url or demo")
        if req.github_url:
            try:
                repo = parse_github_url(req.github_url)
                ref = validate_ref(req.ref)
            except InvalidUrl as exc:
                raise RunRejected("invalid_url", exc.message) from None
            source = RunSource(kind="github", url=repo.url, ref=ref)
            label = repo.label
        else:
            if req.ref:
                raise RunRejected("bad_request", "ref only applies to github_url")
            source = RunSource(kind="demo")
            label = "demo"

        mode = mode or ("demo" if req.demo else "live")
        if mode in ("live", "record") and not (s.has_api_key or s.fake_pipeline):
            raise RunRejected(
                "no_api_key",
                "set ANTHROPIC_API_KEY to optimize a GitHub repository; "
                "the demo and replays work without one",
            )
        if req.demo and not (paths.DEMO_REPO.is_dir() or s.fake_pipeline):
            raise RunRejected("not_ready", f"the bundled demo repo is missing ({paths.DEMO_REPO})")

        if self.active is not None:
            raise RunRejected(
                "run_active", "a run is already in progress", active_run=self.active_summary()
            )
        active_lock = FileLock(self.store.active_lock_path)
        if not active_lock.try_acquire():
            raise RunRejected(
                "run_active",
                "another netzero process is running a run",
                active_run=self._foreign_active(),
            )

        run_lock: FileLock | None = None
        ctx: RunContext | None = None
        try:
            run_id = self.store.new_run_id(label)
            rp = self.store.paths(run_id)
            rp.mkdirs()
            run_lock = FileLock(rp.lock)
            if not run_lock.try_acquire():
                raise RuntimeError(f"run {run_id} is locked by another process")
            ctx = RunContext(
                run_id=run_id,
                paths=rp,
                settings=s,
                store=self.store,
                mode=mode,
                source=source,
                auto_select=req.auto_select,
                clock=self.clock,
            )
            ctx.emit_created()
            power = self._cached_power()
            if power is not None:
                ctx.emit("run.power.detected", RunPowerDetectedData(power=power))
        except BaseException:
            if ctx is not None:
                ctx.close()
            if run_lock is not None:
                run_lock.release()
            active_lock.release()
            raise

        active = ActiveRun(ctx=ctx, active_lock=active_lock, run_lock=run_lock)
        self.active = active
        active.task = asyncio.create_task(self.pipeline(ctx), name=f"netzero-pipeline-{run_id}")
        active.task.add_done_callback(_retrieve)
        active.driver = asyncio.create_task(self._drive(active), name=f"netzero-driver-{run_id}")
        logger.info("run %s started (%s, %s)", run_id, mode, source.url or "demo")
        return to_summary(ctx.detail)

    def select(self, run_id: str, function_ids: list[str]) -> RunSummary:
        ctx = self.live(run_id)
        if ctx is None:
            if self.store.exists(run_id):
                raise RunRejected("bad_state", "this run is not awaiting a selection")
            raise RunRejected("not_found", f"no run {run_id}")
        if not ctx.awaiting_selection:
            raise RunRejected("bad_state", f"this run is {ctx.state}, not awaiting a selection")
        ids = list(dict.fromkeys(function_ids))
        triage = {item.function_id: item for item in ctx.detail.triage}
        unknown = [fid for fid in ids if fid not in triage]
        if unknown:
            raise RunRejected("bad_request", f"unknown function ids: {', '.join(unknown[:5])}")
        skipped = [fid for fid in ids if triage[fid].skip_reason]
        if skipped:
            raise RunRejected(
                "bad_request", f"skipped functions cannot be selected: {', '.join(skipped[:5])}"
            )
        if len(ids) > self.settings.max_functions:
            raise RunRejected(
                "bad_request", f"select at most {self.settings.max_functions} functions"
            )
        ctx.select(ids)
        return to_summary(ctx.detail)

    async def cancel(self, run_id: str, *, wait_s: float = 5.0) -> RunSummary:
        """Cancel a run of this process and wait (briefly) for its terminal event.

        Idempotent: a finished run returns its summary unchanged.
        """
        a = self.active
        if a is not None and a.ctx.run_id == run_id:
            self._request_cancel(a, "user")
            try:
                await asyncio.wait_for(asyncio.shield(a.done.wait()), wait_s)
            except TimeoutError:
                pass
            return to_summary(a.ctx.detail)
        detail = self.detail(run_id)
        if detail is None:
            raise RunRejected("not_found", f"no run {run_id}")
        if detail.state in TERMINAL_STATES:
            return to_summary(detail)
        raise RunRejected(
            "bad_state", "this run is driven by another netzero process; cancel it there"
        )

    async def wait(self, run_id: str) -> None:
        a = self.active
        if a is not None and a.ctx.run_id == run_id:
            await a.done.wait()

    def _request_cancel(self, a: ActiveRun, reason: str) -> None:
        ctx = a.ctx
        if a.finished or a.task is None or a.task.done() or ctx.cancel_reason is not None:
            return
        ctx.cancel_reason = reason  # type: ignore[assignment]
        ctx.open_at_cancel = ctx.bus.open_steps()
        a.task.cancel()

    # -- queries -----------------------------------------------------------------

    def live(self, run_id: str) -> RunContext | None:
        a = self.active
        if a is not None and a.ctx.run_id == run_id and not a.finished:
            return a.ctx
        return None

    def active_summary(self) -> RunSummary | None:
        a = self.active
        if a is not None and not a.finished:
            return to_summary(a.ctx.detail)
        return self._foreign_active()

    def detail(self, run_id: str) -> RunDetail | None:
        if not self.store.valid_id(run_id):
            return None
        ctx = self.live(run_id)
        if ctx is not None:
            return ctx.detail
        d = self.store.read_detail(run_id)
        if d is None:
            return None
        if not self._ended(run_id, d.state) and not self.store.is_owned(run_id):
            return self.recover(run_id) or self.store.read_detail(run_id) or d
        return d

    def summaries(self) -> list[RunSummary]:
        out: list[RunSummary] = []
        for s in self.store.list_summaries():
            ctx = self.live(s.id)
            if ctx is not None:
                s = to_summary(ctx.detail)
            elif s.state not in TERMINAL_STATES and not self.store.is_owned(s.id):
                d = self.recover(s.id)
                if d is not None:
                    s = to_summary(d)
            out.append(s)
        return out

    def _ended(self, run_id: str, state: RunState) -> bool:
        """The run is over and its log is complete. ``run.json`` is persisted on
        ``run.state_changed``, so a terminal state alone can precede a crash
        that lost the terminal event; the log has the final say."""
        return state in TERMINAL_STATES and self.store.has_terminal_event(run_id)

    def _foreign_active(self) -> RunSummary | None:
        """The run another process is driving, if any (most recent first)."""
        for run_id in self.store.list_ids()[:50]:
            if self.live(run_id) is not None:
                continue
            s = self.store.read_summary(run_id)
            if s is not None and s.state not in TERMINAL_STATES and self.store.is_owned(run_id):
                return s
        return None

    def _cached_power(self) -> PowerInfo | None:
        try:
            return self.power()
        except Exception:  # a broken cache must not block a run
            logger.exception("reading the cached power profile failed")
            return None

    # -- driver ------------------------------------------------------------------

    async def _drive(self, a: ActiveRun) -> None:
        assert a.task is not None
        try:
            await asyncio.wait({a.task})
        finally:
            self._finish(a)

    def _finish(self, a: ActiveRun) -> None:
        """Write the terminal event (once), release the locks, wake waiters."""
        if a.finished:
            return
        a.finished = True
        ctx = a.ctx
        try:
            try:
                ctx.procs.kill_all()
            except Exception:
                logger.exception("killing the processes of run %s failed", ctx.run_id)
            if not (ctx.bus.terminal or ctx.bus.closed):
                ctx.flush_logs()
                self._write_terminal(ctx, a.task)
        except Exception:
            logger.exception("finishing run %s failed", ctx.run_id)
        finally:
            try:
                ctx.close()
            finally:
                a.run_lock.release()
                a.active_lock.release()
                if self.active is a:
                    self.active = None
                a.done.set()
                logger.info("run %s ended %s", ctx.run_id, ctx.state)

    def _write_terminal(self, ctx: RunContext, task: asyncio.Task[None] | None) -> None:
        reason = ctx.cancel_reason
        if ctx.terminal:
            # the pipeline transitioned to a terminal state itself
            _emit_missing_terminal(ctx)
            return
        if task is None or not task.done():
            # the driver was cancelled (event loop teardown): stop the pipeline
            if task is not None:
                ctx.cancel_reason = ctx.cancel_reason or "shutdown"
                task.cancel()
            _end_interrupted(ctx)
            return
        if task.cancelled():
            if reason == "user":
                _end_cancelled(ctx)
            elif reason == "shutdown":
                _end_interrupted(ctx)
            else:
                _end_failed(
                    ctx, ErrorInfo(kind="internal", message="the pipeline task was cancelled")
                )
            return
        exc = task.exception()
        if reason == "user":
            _end_cancelled(ctx)
        elif reason == "shutdown":
            _end_interrupted(ctx)
        elif exc is not None:
            if not isinstance(exc, NetzeroError):
                ctx.log_exception("orchestrator", exc)
            _end_failed(ctx, error_info(exc))
        elif ctx.state != "finalizing":
            _end_failed(
                ctx,
                ErrorInfo(kind="internal", message=f"the pipeline stopped in state {ctx.state}"),
            )
        else:
            _end_completed(ctx)

    # -- recovery ----------------------------------------------------------------

    def recover_all(self) -> int:
        n = 0
        for run_id in self.store.list_ids():
            if self.live(run_id) is not None or self.store.is_owned(run_id):
                continue
            s = self.store.read_summary(run_id)
            if s is not None and self._ended(run_id, s.state):
                continue
            if self.recover(run_id) is not None:
                n += 1
        if n:
            logger.info("recovered %d interrupted run(s)", n)
        return n

    def recover(self, run_id: str) -> RunDetail | None:
        """Bring an orphaned run to a terminal state; ``None`` if it is owned or unreadable."""
        if self.live(run_id) is not None or not self.store.valid_id(run_id):
            return None
        rp = self.store.paths(run_id)
        if not rp.events.exists():
            return None
        lock = FileLock(rp.lock)
        if not lock.try_acquire():
            return None
        try:
            repair_tail(rp.events)
            lines, _ = read_complete_lines(rp.events)
            try:
                events = [parse_event(line) for line in lines]
                proj = _rebuild(events, run_id)
            except (ValidationError, ValueError) as exc:
                logger.warning("cannot recover run %s: %s", run_id, exc)
                return None
            d = proj.detail
            if events and events[-1].type in TERMINAL_EVENT_TYPES:  # type: ignore[attr-defined]
                self.store.write_detail(d)
                return d
            try:
                killed = kill_leftovers(rp.procs)
            except Exception:
                logger.exception("killing leftover processes of run %s failed", run_id)
                killed = 0
            ctx = RunContext(
                run_id=run_id,
                paths=rp,
                settings=self.settings,
                store=self.store,
                mode=d.mode,
                source=d.source,
                created_ts=d.created_ts,
                start_seq=d.last_seq,
                projection=proj,
                clock=self.clock,
            )
            try:
                ctx.bus.restore(events)  # type: ignore[arg-type]
                if killed:
                    ctx.log(
                        "orchestrator", f"killed {killed} leftover process group(s)", level="warn"
                    )
                if ctx.terminal:
                    _emit_missing_terminal(ctx)
                else:
                    _end_interrupted(ctx, message="netzero stopped while this run was in progress")
            finally:
                ctx.close()
            logger.info("run %s recovered as %s", run_id, ctx.state)
            return ctx.detail
        finally:
            lock.release()


# -- terminal writers -------------------------------------------------------------


def _end_completed(ctx: RunContext) -> None:
    ctx.bus.close_open_steps(
        ErrorInfo(kind="internal", message="step left open at the end of the run"),
        function_outcome="failed",
    )
    ctx.transition("completed")
    ctx.emit(
        "run.completed",
        RunCompletedData(summary=ctx.projection.totals(duration_ms=ctx.elapsed_ms())),
    )


def _end_failed(ctx: RunContext, error: ErrorInfo) -> None:
    stage = ctx.state
    ctx.bus.close_open_steps(
        ErrorInfo(kind="cancelled", message=f"run failed: {error.message}"),
        function_outcome="failed",
    )
    ctx.transition("failed", reason=error.message)
    ctx.emit("run.failed", RunFailedData(stage=stage, error=error))


def _end_cancelled(ctx: RunContext) -> None:
    at = ctx.state
    ctx.bus.close_open_steps(ErrorInfo(kind="cancelled", message="cancelled by user"))
    ctx.transition("cancelled", reason="cancelled by user")
    ctx.emit("run.cancelled", RunCancelledData(at_state=at))


def _end_interrupted(ctx: RunContext, *, message: str = "server shut down") -> None:
    prev = ctx.state
    closed = ctx.bus.close_open_steps(ErrorInfo(kind="interrupted", message=message))
    open_steps: list[OpenStep] = ctx.open_at_cancel or closed
    ctx.transition("interrupted", reason=message)
    ctx.emit("run.interrupted", RunInterruptedData(previous_state=prev, open_steps=open_steps))


def _emit_missing_terminal(ctx: RunContext) -> None:
    """The state is terminal but its terminal event was never written (crash in between)."""
    state: RunState = ctx.state
    prev = _state_before_last_transition(ctx) or state
    d = ctx.detail
    if state == "completed":
        duration = max(0, d.updated_ts - d.created_ts)
        ctx.emit(
            "run.completed", RunCompletedData(summary=ctx.projection.totals(duration_ms=duration))
        )
    elif state == "cancelled":
        ctx.emit("run.cancelled", RunCancelledData(at_state=prev))
    elif state == "interrupted":
        ctx.emit("run.interrupted", RunInterruptedData(previous_state=prev))
    else:
        ctx.emit(
            "run.failed",
            RunFailedData(
                stage=prev,
                error=d.error or ErrorInfo(kind="interrupted", message="netzero stopped"),
            ),
        )


def _state_before_last_transition(ctx: RunContext) -> RunState | None:
    lines, _ = read_complete_lines(ctx.paths.events)
    for line in reversed(lines):
        if line_type(line) == "run.state_changed":
            return parse_event(line).data.from_state  # type: ignore[union-attr]
    return None


def _retrieve(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


def _rebuild(events: list, run_id: str) -> Projection:
    if not events:
        raise ValueError("no events")
    first: EventBase = events[0]
    if first.type != "run.created":  # type: ignore[attr-defined]
        raise ValueError("events.jsonl must start with run.created")
    proj = Projection.empty(
        run_id,
        created_ts=first.ts,
        source=first.data.source,  # type: ignore[attr-defined]
        mode=first.data.mode,  # type: ignore[attr-defined]
    )
    for ev in events:
        proj.apply(ev)
    return proj
