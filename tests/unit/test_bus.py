"""``EventBus``, ``Subscription`` and ``LogSink``."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from netzero.events import (
    ErrorInfo,
    EventBase,
    LogData,
    OpenStep,
    dump_event,
    line_seq,
    make_event,
    parse_event,
)
from netzero.pipeline.bus import EventBus, LogSink, Subscription
from netzero.pipeline.store import read_complete_lines

RUN_ID = "20261005-120000-bus"


def log_data(text: str = "hi") -> LogData:
    return LogData(level="info", source="orchestrator", lines=[text])


def disk_lines(path: Path) -> list[str]:
    return read_complete_lines(path)[0]


@pytest.fixture
def events_path(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


@pytest.fixture
def bus(events_path: Path) -> Iterator[EventBus]:
    b = EventBus(RUN_ID, events_path)
    yield b
    b.close()


def drain(sub: Subscription) -> list[tuple[int, str] | None]:
    out: list[tuple[int, str] | None] = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


# -- seq + persistence ------------------------------------------------------------


async def test_seq_is_gapless_under_concurrent_emits(bus: EventBus, events_path: Path) -> None:
    sub = bus.subscribe()

    async def one(i: int) -> int:
        await asyncio.sleep(0)
        return bus.emit("log", log_data(f"line {i}")).seq

    seqs = await asyncio.gather(*(one(i) for i in range(1000)))
    assert sorted(seqs) == list(range(1, 1001))
    assert bus.last_seq == 1000
    lines = disk_lines(events_path)
    assert [line_seq(ln) for ln in lines] == list(range(1, 1001))
    assert [s for s, _ in drain(sub)] == list(range(1, 1001))  # type: ignore[misc]


def test_start_seq_continues_numbering(events_path: Path) -> None:
    b = EventBus(RUN_ID, events_path, start_seq=41)
    try:
        assert b.emit("log", log_data()).seq == 42
    finally:
        b.close()


def test_invalid_event_consumes_no_seq(bus: EventBus, events_path: Path) -> None:
    with pytest.raises(ValueError):
        bus.emit("log", {"level": "loud", "source": "orchestrator", "lines": []})
    assert bus.last_seq == 0
    assert disk_lines(events_path) == []
    assert bus.emit("log", log_data()).seq == 1


def test_line_is_on_disk_before_on_event_and_subscribers(events_path: Path) -> None:
    seen: list[str] = []

    def on_event(ev: EventBase) -> None:
        lines = disk_lines(events_path)
        assert lines and line_seq(lines[-1]) == ev.seq
        seen.append("on_event")

    b = EventBus(RUN_ID, events_path, on_event=on_event)
    sub = b.subscribe()
    real_push = sub.push

    def checking_push(item: tuple[int, str] | None) -> None:
        if item is not None:
            assert disk_lines(events_path)[-1] == item[1]
            seen.append("push")
        real_push(item)

    sub.push = checking_push  # type: ignore[method-assign]
    try:
        for i in range(3):
            b.emit("log", log_data(str(i)))
    finally:
        b.close()
    assert seen == ["on_event", "push"] * 3


def test_published_line_is_the_persisted_line(bus: EventBus, events_path: Path) -> None:
    sub = bus.subscribe()
    ev = bus.emit("log", log_data(), function_id="m:f", candidate_id="A", attempt=0)
    (item,) = drain(sub)
    assert item == (1, dump_event(ev))
    assert disk_lines(events_path) == [dump_event(ev)]
    assert parse_event(item[1]) == ev  # type: ignore[index]


def test_late_subscriber_gets_only_later_events(bus: EventBus) -> None:
    early = bus.subscribe()
    bus.emit("log", log_data("1"))
    bus.emit("log", log_data("2"))
    late = bus.subscribe()
    bus.emit("log", log_data("3"))
    assert [s for s, _ in drain(early)] == [1, 2, 3]  # type: ignore[misc]
    assert [s for s, _ in drain(late)] == [3]  # type: ignore[misc]


def test_unsubscribe_stops_delivery(bus: EventBus) -> None:
    sub = bus.subscribe()
    assert bus.n_subscribers == 1
    bus.unsubscribe(sub)
    assert bus.n_subscribers == 0
    bus.emit("log", log_data())
    assert drain(sub) == []


# -- overflow + close ---------------------------------------------------------------


def test_overflow_drops_everything_after_including_close(events_path: Path) -> None:
    b = EventBus(RUN_ID, events_path, sub_maxsize=2)
    slow = b.subscribe()
    for i in range(3):
        b.emit("log", log_data(str(i)))
    assert slow.overflowed
    assert [s for s, _ in drain(slow)] == [1, 2]  # type: ignore[misc]
    b.emit("log", log_data("after drain"))  # room again, but still dropped
    b.close()
    assert drain(slow) == []  # no event 4 and no close sentinel
    assert [line_seq(ln) for ln in disk_lines(events_path)] == [1, 2, 3, 4]


def test_subscription_push_without_bus() -> None:
    sub = Subscription(maxsize=1)
    sub.push((1, "a"))
    assert not sub.overflowed
    sub.push((2, "b"))
    assert sub.overflowed
    sub.push(None)
    assert drain(sub) == [(1, "a")]


async def test_close_delivers_sentinel_to_healthy_subscribers(bus: EventBus) -> None:
    a, b = bus.subscribe(), bus.subscribe()
    bus.emit("log", log_data())
    bus.close()
    for sub in (a, b):
        assert (await sub.get())[0] == 1  # type: ignore[index]
        assert await sub.get() is None
    assert bus.n_subscribers == 0
    assert bus.closed


def test_subscribe_after_close_gets_sentinel_immediately(bus: EventBus) -> None:
    bus.close()
    sub = bus.subscribe()
    assert drain(sub) == [None]
    assert bus.n_subscribers == 0


def test_close_is_idempotent_and_blocks_emit(bus: EventBus) -> None:
    sub = bus.subscribe()
    bus.close()
    bus.close()
    assert drain(sub) == [None]
    with pytest.raises(RuntimeError):
        bus.emit("log", log_data())


def test_terminal_event_blocks_further_emits(bus: EventBus, events_path: Path) -> None:
    bus.emit("run.state_changed", {"from_state": "created", "to_state": "failed"})
    bus.emit("run.failed", {"stage": "created", "error": {"kind": "internal", "message": "x"}})
    assert bus.terminal
    with pytest.raises(RuntimeError):
        bus.emit("log", log_data())
    assert len(disk_lines(events_path)) == 2


# -- open steps ----------------------------------------------------------------------


def test_open_step_tracking(bus: EventBus) -> None:
    bus.emit("run.clone.started", {"url": None})
    assert bus.is_open("run.clone.started")
    assert bus.open_steps() == [OpenStep(type="run.clone.started")]
    bus.emit("function.started", _function_started("m:f"), function_id="m:f")
    bus.emit("candidate.check.started", function_id="m:f", candidate_id="A", attempt=0)
    assert bus.is_open("candidate.check.started", "m:f", "A", 0)
    assert not bus.is_open("candidate.check.started", "m:f", "A", 1)
    assert not bus.is_open("candidate.check.started", "m:f", "B", 0)
    bus.emit("candidate.check.completed", {"ok": True, "duration_ms": 1}, **_cand("m:f", "A", 0))
    assert not bus.is_open("candidate.check.started", "m:f", "A", 0)
    bus.emit("run.clone.completed", {"ok": True, "duration_ms": 1})
    assert bus.open_steps() == [OpenStep(type="function.started", function_id="m:f")]


def test_close_open_steps_innermost_first(events_path: Path) -> None:
    now = [1000]
    b = EventBus(RUN_ID, events_path, clock=lambda: now[0])
    try:
        b.emit("function.started", _function_started("m:f"), function_id="m:f")
        b.emit("function.tests.write.started", {"kind": "write"}, function_id="m:f", attempt=0)
        b.emit("candidate.write.started", _cand_write(), **_cand("m:f", "A", 0))
        now[0] = 1250
        err = ErrorInfo(kind="cancelled", message="cancelled by user")
        closed = b.close_open_steps(err)
        assert [s.type for s in closed] == [
            "candidate.write.started",
            "function.tests.write.started",
            "function.started",
        ]
        assert b.open_steps() == []
        events = [parse_event(ln) for ln in disk_lines(events_path)][3:]
        assert [e.type for e in events] == [
            "candidate.write.completed",
            "function.tests.write.completed",
            "function.completed",
        ]
        for e in events[:2]:
            assert e.data.ok is False  # type: ignore[union-attr]
            assert e.data.error == err  # type: ignore[union-attr]
            assert e.data.duration_ms == 250  # type: ignore[union-attr]
        fc = events[2]
        assert (fc.function_id, fc.data.outcome, fc.data.reason) == (
            "m:f",
            "cancelled",
            err.message,
        )  # type: ignore[union-attr]
        assert (events[0].candidate_id, events[0].attempt) == ("A", 0)
    finally:
        b.close()


def test_close_open_steps_where_and_outcome(bus: EventBus) -> None:
    bus.emit("run.clone.started", {"url": None})
    bus.emit("function.started", _function_started("m:f"), function_id="m:f")
    closed = bus.close_open_steps(
        ErrorInfo(kind="internal", message="x"),
        function_outcome="failed",
        where=lambda st: st.type == "function.started",
    )
    assert closed == [OpenStep(type="function.started", function_id="m:f")]
    assert bus.open_steps() == [OpenStep(type="run.clone.started")]


def test_restore_rebuilds_open_steps_and_terminal(tmp_path: Path) -> None:
    first = EventBus(RUN_ID, tmp_path / "a.jsonl")
    evs = [
        first.emit("run.clone.started", {"url": None}),
        first.emit("run.env.started", {"python_request": "3.12"}),
        first.emit("run.env.completed", {"ok": True, "duration_ms": 1}),
    ]
    first.close()
    second = EventBus(RUN_ID, tmp_path / "b.jsonl", start_seq=3)
    try:
        second.restore(evs)
        assert second.open_steps() == [OpenStep(type="run.clone.started")]
        assert not second.terminal
        second.restore([_terminal(4)])
        assert second.terminal
    finally:
        second.close()


# -- LogSink -------------------------------------------------------------------------


def log_events(path: Path) -> list[EventBase]:
    return [e for ln in disk_lines(path) if (e := parse_event(ln)).type == "log"]


def test_log_sink_coalesces_until_flush(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "pytest", stream="stdout", function_id="m:f", attempt=0)
    sink.write("one\n")
    sink.write_lines("two\nthree")
    assert bus.last_seq == 0  # nothing has a seq yet
    sink.flush()
    (ev,) = log_events(events_path)
    assert ev.data.lines == ["one", "two", "three"]  # type: ignore[union-attr]
    assert (ev.data.source, ev.data.stream, ev.data.level) == ("pytest", "stdout", "info")  # type: ignore[union-attr]
    assert (ev.function_id, ev.candidate_id, ev.attempt) == ("m:f", None, 0)
    sink.flush()  # empty buffer: no event
    assert bus.last_seq == 1


def test_log_sink_keeps_the_pytest_header_out_of_the_stream(
    bus: EventBus, events_path: Path, tmp_path: Path
) -> None:
    log_file = tmp_path / "pytest.log"
    sink = LogSink(bus, "pytest", log_file=log_file)
    header = [
        "============================= test session starts ==============================",
        "platform darwin -- Python 3.12.2, pytest-9.0.1, pluggy-1.6.0",
        "rootdir: /tmp/x",
        "configfile: pytest.ini",
        "collected 4 items",
    ]
    sink.write_lines([*header, "", "test_nz.py ....  [100%]", "===== 4 passed in 0.02s ====="])
    sink.close()
    (ev,) = log_events(events_path)
    assert ev.data.lines == ["", "test_nz.py ....  [100%]", "===== 4 passed in 0.02s ====="]  # type: ignore[union-attr]
    assert log_file.read_text().splitlines()[: len(header)] == header


def test_log_sink_flushes_full_batches(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "bench")
    sink.write_lines([f"l{i}" for i in range(250)])
    assert [len(e.data.lines) for e in log_events(events_path)] == [100, 100]  # type: ignore[union-attr]
    sink.close()
    assert [len(e.data.lines) for e in log_events(events_path)] == [100, 100, 50]  # type: ignore[union-attr]


async def test_log_sink_flushes_on_timer(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "llm")
    sink.FLUSH_S = 0.01
    sink.write("a")
    sink.write("b")
    assert log_events(events_path) == []
    await asyncio.sleep(0.05)
    assert [e.data.lines for e in log_events(events_path)] == [["a", "b"]]  # type: ignore[union-attr]
    sink.close()


def test_log_sink_caps_lines_and_marks_truncated(
    bus: EventBus, events_path: Path, tmp_path: Path
) -> None:
    log_file = tmp_path / "full.log"
    sink = LogSink(bus, "pytest", cap=3, log_file=log_file)
    sink.write_lines(["a", "b"])
    sink.flush()
    sink.write_lines(["c", "d", "e"])
    sink.close()
    evs = log_events(events_path)
    assert [e.data.lines for e in evs] == [["a", "b"], ["c"]]  # type: ignore[union-attr]
    assert [e.data.truncated for e in evs] == [False, True]  # type: ignore[union-attr]
    assert log_file.read_text().splitlines() == ["a", "b", "c", "d", "e"]


def test_log_sink_close_adds_truncation_notice(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "pytest", cap=2)
    sink.write_lines(["a", "b"])
    sink.flush()
    sink.write("c")  # over the cap: dropped
    sink.close()
    evs = log_events(events_path)
    assert evs[0].data.lines == ["a", "b"]  # type: ignore[union-attr]
    assert evs[1].data.truncated  # type: ignore[union-attr]
    assert "truncated after 2 lines" in evs[1].data.lines[0]  # type: ignore[union-attr]


def test_log_sink_shortens_long_lines(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "sandbox")
    sink.write("x" * (LogSink.MAX_LINE + 50))
    sink.close()
    (line,) = log_events(events_path)[0].data.lines  # type: ignore[union-attr]
    assert line == "x" * LogSink.MAX_LINE + " …"


def test_disabled_log_sink_only_writes_the_file(
    bus: EventBus, events_path: Path, tmp_path: Path
) -> None:
    log_file = tmp_path / "debug.log"
    with LogSink(bus, "sandbox", level="debug", enabled=False, log_file=log_file) as sink:
        sink.write_lines(["a", "b"])
    assert bus.last_seq == 0
    assert log_file.read_text() == "a\nb\n"


def test_log_sink_drops_buffer_after_terminal(bus: EventBus, events_path: Path) -> None:
    sink = LogSink(bus, "orchestrator")
    sink.write("late")
    bus.emit("run.state_changed", {"from_state": "created", "to_state": "cancelled"})
    bus.emit("run.cancelled", {"at_state": "created"})
    sink.close()
    assert log_events(events_path) == []


# -- helpers --------------------------------------------------------------------------


def _cand(fid: str, cid: str, attempt: int) -> dict:
    return {"function_id": fid, "candidate_id": cid, "attempt": attempt}


def _cand_write() -> dict:
    return {"kind": "write", "strategy_hint": "x"}


def _function_started(fid: str) -> dict:
    module, qualname = fid.split(":")
    return {
        "index": 0,
        "total": 1,
        "info": {
            "function_id": fid,
            "module": module,
            "qualname": qualname,
            "kind": "function",
            "file": "m.py",
            "line": 1,
            "end_line": 2,
            "loc": 2,
            "import_line": "from m import f",
            "call_hint": "f()",
            "source": "def f(): ...",
        },
    }


def _terminal(seq: int) -> EventBase:
    return make_event("run.cancelled", seq=seq, ts=1, run_id=RUN_ID, data={"at_state": "created"})
