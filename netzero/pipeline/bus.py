"""Per-run event bus: assign seq, append to ``events.jsonl``, fan out to subscribers.

Everything here runs on the event loop and never awaits, so an ``emit`` is
atomic: seq assignment, the file append and the fan-out cannot interleave
with another emit. Never call ``emit`` from a worker thread.

Order per event: build + validate -> append line (O_APPEND, unbuffered) ->
apply to the projection -> publish. A subscriber therefore never sees an
event that is not already on disk.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from netzero.events import (
    DATA_CLASSES,
    STARTED_TO_COMPLETED,
    TERMINAL_EVENT_TYPES,
    CandidateId,
    ErrorInfo,
    EventBase,
    FunctionCompletedData,
    FunctionOutcome,
    LogData,
    OpenStep,
    dump_event,
    make_event,
)

StepKey = tuple[str, str | None, str | None, int | None]

COMPLETED_TO_STARTED = {v: k for k, v in STARTED_TO_COMPLETED.items()}


def now_ms() -> int:
    return int(time.time() * 1000)


class Subscription:
    """A bounded queue of ``(seq, line)``. ``None`` means the bus closed."""

    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue(maxsize)
        self.overflowed = False

    def push(self, item: tuple[int, str] | None) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # The consumer is too slow: it must drop the stream and resume from
            # its last id (the file has everything).
            self.overflowed = True

    async def get(self) -> tuple[int, str] | None:
        return await self.queue.get()


class EventBus:
    def __init__(
        self,
        run_id: str,
        path: Path,
        *,
        start_seq: int = 0,
        on_event: Callable[[EventBase], None] | None = None,
        clock: Callable[[], int] = now_ms,
        sub_maxsize: int = 10_000,
    ):
        self.run_id = run_id
        self.path = path
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        self._seq = start_seq
        self._on_event = on_event
        self._clock = clock
        self._sub_maxsize = sub_maxsize
        self._subs: set[Subscription] = set()
        self._open: dict[StepKey, tuple[str, int]] = {}  # key -> (started type, started ts)
        self.terminal = False
        self.closed = False

    # -- emit ------------------------------------------------------------------

    @property
    def last_seq(self) -> int:
        return self._seq

    def emit(
        self,
        type: str,
        data: BaseModel | dict | None = None,
        *,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
    ) -> EventBase:
        if self.closed or self.terminal:
            raise RuntimeError(f"run {self.run_id}: emit {type!r} after the terminal event")
        ts = self._clock()
        ev = make_event(
            type,
            seq=self._seq + 1,
            ts=ts,
            run_id=self.run_id,
            data=data,
            function_id=function_id,
            candidate_id=candidate_id,
            attempt=attempt,
        )
        line = dump_event(ev)
        os.write(self._fd, (line + "\n").encode("utf-8"))
        self._seq = ev.seq
        self._track(type, (function_id, candidate_id, attempt), ts)
        if type in TERMINAL_EVENT_TYPES:
            self.terminal = True
        if self._on_event is not None:
            self._on_event(ev)
        for sub in list(self._subs):
            sub.push((ev.seq, line))
        return ev

    def _track(self, type: str, scope: tuple, ts: int) -> None:
        if type in STARTED_TO_COMPLETED:
            self._open[(type, *scope)] = (type, ts)
        elif type in COMPLETED_TO_STARTED:
            self._open.pop((COMPLETED_TO_STARTED[type], *scope), None)

    def restore(self, events: list[EventBase]) -> None:
        """Rebuild open-step tracking from already-persisted events (recovery)."""
        for ev in events:
            self._track(ev.type, (ev.function_id, ev.candidate_id, ev.attempt), ev.ts)  # type: ignore[attr-defined]
            if ev.type in TERMINAL_EVENT_TYPES:  # type: ignore[attr-defined]
                self.terminal = True

    # -- open steps ------------------------------------------------------------

    def open_steps(self) -> list[OpenStep]:
        return [
            OpenStep(type=k[0], function_id=k[1], candidate_id=k[2], attempt=k[3])  # type: ignore[arg-type]
            for k in self._open
        ]

    def is_open(self, started_type: str, function_id=None, candidate_id=None, attempt=None) -> bool:
        return (started_type, function_id, candidate_id, attempt) in self._open

    def close_open_steps(
        self,
        error: ErrorInfo,
        *,
        function_outcome: FunctionOutcome = "cancelled",
        where: Callable[[OpenStep], bool] | None = None,
    ) -> list[OpenStep]:
        """Emit a synthetic ``ok=false`` completion for open steps, innermost first.

        ``where`` limits which steps are closed (default: all of them).
        """
        closed: list[OpenStep] = []
        for key in reversed(list(self._open)):
            if key not in self._open:
                continue
            started_type, ts = self._open[key]
            _, function_id, candidate_id, attempt = key
            step = OpenStep(
                type=started_type,
                function_id=function_id,
                candidate_id=candidate_id,  # type: ignore[arg-type]
                attempt=attempt,
            )
            if where is not None and not where(step):
                continue
            closed.append(step)
            completed = STARTED_TO_COMPLETED[started_type]
            duration = max(0, self._clock() - ts)
            if completed == "function.completed":
                data: BaseModel = FunctionCompletedData(
                    outcome=function_outcome, reason=error.message, duration_ms=duration
                )
            else:
                data = DATA_CLASSES[completed](ok=False, duration_ms=duration, error=error)
            self.emit(
                completed,
                data,
                function_id=function_id,
                candidate_id=candidate_id,  # type: ignore[arg-type]
                attempt=attempt,
            )
        return closed

    # -- subscribers -----------------------------------------------------------

    def subscribe(self) -> Subscription:
        sub = Subscription(self._sub_maxsize)
        if self.closed:
            sub.push(None)
        else:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    @property
    def n_subscribers(self) -> int:
        return len(self._subs)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for sub in list(self._subs):
            sub.push(None)
        self._subs.clear()
        try:
            os.close(self._fd)
        except OSError:
            pass


LogLevel = str  # "debug" | "info" | "warn" | "error"
LogSource = str  # see LogData.source


# pytest's session header says nothing about the run; it stays in the log file
PYTEST_HEADER = re.compile(
    r"=+ test session starts =+$|platform \S+ -- Python |cachedir: |rootdir: |configfile: "
    r"|plugins: |collecting \.\.\. |collected \d+ items?$"
)


class LogSink:
    """Coalesce log lines into ``log`` events (every 250 ms or 100 lines).

    At most ``cap`` lines per sink reach the event stream; the rest only go to
    ``log_file`` and the final event says ``truncated``.
    """

    FLUSH_S = 0.25
    BATCH = 100
    MAX_LINE = 2000

    def __init__(
        self,
        bus: EventBus,
        source: LogSource,
        *,
        level: LogLevel = "info",
        stream: str | None = None,
        function_id: str | None = None,
        candidate_id: CandidateId | None = None,
        attempt: int | None = None,
        log_file: Path | None = None,
        enabled: bool = True,
        cap: int = 2000,
    ):
        self.bus = bus
        self.source = source
        self.level = level
        self.stream = stream
        self.scope = {"function_id": function_id, "candidate_id": candidate_id, "attempt": attempt}
        self.enabled = enabled
        self.cap = cap
        self._buf: list[str] = []
        self._sent = 0
        self._truncated = False
        self._timer: asyncio.TimerHandle | None = None
        self._file = log_file.open("a", encoding="utf-8") if log_file else None

    def write(self, line: str) -> None:
        line = line.rstrip("\n")
        if self._file:
            self._file.write(line + "\n")
        if not self.enabled or (self.source == "pytest" and PYTEST_HEADER.match(line)):
            return
        if self._sent + len(self._buf) >= self.cap:
            self._truncated = True
            return
        if len(line) > self.MAX_LINE:
            line = line[: self.MAX_LINE] + " …"
        self._buf.append(line)
        if len(self._buf) >= self.BATCH:
            self.flush()
        elif self._timer is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timer = loop.call_later(self.FLUSH_S, self.flush)

    def write_lines(self, lines: list[str] | str) -> None:
        if isinstance(lines, str):
            lines = lines.splitlines()
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._file:
            self._file.flush()
        if not self._buf or self.bus.terminal or self.bus.closed:
            self._buf.clear()
            return
        lines, self._buf = self._buf, []
        self._sent += len(lines)
        self.bus.emit(
            "log",
            LogData(
                level=self.level,  # type: ignore[arg-type]
                source=self.source,  # type: ignore[arg-type]
                stream=self.stream,  # type: ignore[arg-type]
                lines=lines,
                truncated=self._truncated,
            ),
            **self.scope,
        )

    def close(self) -> None:
        if self._truncated and self.enabled and not self._buf and not self.bus.terminal:
            self._buf.append(f"[log truncated after {self.cap} lines; full log on disk]")
        self.flush()
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self) -> LogSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
