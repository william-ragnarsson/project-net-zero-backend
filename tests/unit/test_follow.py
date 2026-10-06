"""``follow``: replay the persisted log, then switch to the bus without gaps or repeats."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from netzero.events import ErrorInfo, LogData, RunFailedData, line_seq
from netzero.pipeline.bus import Subscription
from netzero.pipeline.follow import follow
from netzero.pipeline.run import RunContext
from tests.unit.support import read_lines

Item = tuple[int, str] | None


def log(ctx: RunContext, text: str) -> None:
    ctx.emit("log", LogData(level="info", source="pytest", lines=[text]))


def fail(ctx: RunContext) -> None:
    """``run.state_changed -> failed`` then the terminal ``run.failed``."""
    stage = ctx.state
    ctx.transition("failed")
    error = ErrorInfo(kind="internal", message="boom")
    ctx.emit("run.failed", RunFailedData(stage=stage, error=error))


async def take(gen: AsyncIterator[Item], n: int) -> list[Item]:
    return [await anext(gen) for _ in range(n)]


async def drain(gen: AsyncIterator[Item]) -> list[Item]:
    """Everything until the generator returns (``None`` idles are kept)."""
    return [item async for item in gen]


def seqs(items: list[Item]) -> list[int]:
    return [item[0] for item in items if item is not None]


async def test_replays_the_file_then_streams_live_events(ctx: RunContext) -> None:
    ctx.emit_created()
    for i in range(4):
        log(ctx, f"before {i}")
    gen = follow(ctx, 0, idle_s=1.0)
    replayed = await take(gen, 5)
    assert seqs(replayed) == [1, 2, 3, 4, 5]
    assert [line for _, line in replayed] == read_lines(ctx)  # type: ignore[misc]

    async def producer() -> None:
        for i in range(20):
            log(ctx, f"live {i}")
            if i % 3 == 0:
                await asyncio.sleep(0)
        fail(ctx)

    task = asyncio.create_task(producer())
    rest = await drain(gen)
    await task
    assert None not in rest
    assert seqs(replayed + rest) == list(range(1, ctx.last_seq + 1))
    assert [line for _, line in replayed + rest] == read_lines(ctx)  # type: ignore[misc]
    assert ctx.bus.n_subscribers == 0


async def test_events_emitted_during_replay_arrive_once(ctx: RunContext) -> None:
    """Events published while the file snapshot is being replayed come from the queue."""
    ctx.emit_created()
    log(ctx, "a")
    gen = follow(ctx, 0, idle_s=1.0)
    first = await anext(gen)  # subscribed, file read: seq 1 and 2
    log(ctx, "b")  # seq 3: on disk and in the queue, but not in the file snapshot
    fail(ctx)
    rest = await drain(gen)
    assert seqs([first, *rest]) == list(range(1, ctx.last_seq + 1))


async def test_after_skips_what_the_client_has(ctx: RunContext) -> None:
    ctx.emit_created()
    for i in range(5):
        log(ctx, str(i))
    gen = follow(ctx, 4, idle_s=1.0)
    assert seqs(await take(gen, 2)) == [5, 6]
    log(ctx, "live")
    assert seqs(await take(gen, 1)) == [7]
    await gen.aclose()
    assert ctx.bus.n_subscribers == 0


async def test_after_beyond_the_log_waits_for_new_events(ctx: RunContext) -> None:
    ctx.emit_created()
    log(ctx, "x")
    gen = follow(ctx, 2, idle_s=1.0)
    nxt = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)
    assert not nxt.done()
    log(ctx, "y")
    assert seqs([await nxt]) == [3]
    await gen.aclose()


async def test_idle_yields_none(ctx: RunContext) -> None:
    ctx.emit_created()
    gen = follow(ctx, 1, idle_s=0.02)
    assert await anext(gen) is None
    assert await anext(gen) is None
    log(ctx, "wake")
    assert seqs([await anext(gen)]) == [2]
    await gen.aclose()
    assert ctx.bus.n_subscribers == 0


async def test_returns_after_a_terminal_event_in_the_file(ctx: RunContext) -> None:
    ctx.emit_created()
    fail(ctx)
    items = await drain(follow(ctx, 0, idle_s=1.0))
    assert seqs(items) == [1, 2, 3]
    assert ctx.bus.n_subscribers == 0


async def test_returns_after_a_live_terminal_event(ctx: RunContext) -> None:
    ctx.emit_created()
    gen = follow(ctx, 0, idle_s=1.0)
    assert seqs(await take(gen, 1)) == [1]
    fail(ctx)
    assert seqs(await drain(gen)) == [2, 3]


async def test_returns_when_the_bus_closes(ctx: RunContext) -> None:
    ctx.emit_created()
    gen = follow(ctx, 0, idle_s=1.0)
    assert seqs(await take(gen, 1)) == [1]
    log(ctx, "last words")
    ctx.close()  # no terminal event: e.g. the process is shutting down
    assert seqs(await drain(gen)) == [2]


async def test_following_a_closed_run_replays_and_returns(ctx: RunContext) -> None:
    ctx.emit_created()
    log(ctx, "x")
    ctx.close()
    assert seqs(await drain(follow(ctx, 0, idle_s=1.0))) == [1, 2]


async def test_overflow_ends_the_stream_and_the_client_can_resume(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.emit_created()
    real_subscribe = ctx.bus.subscribe

    def tiny() -> Subscription:
        sub = real_subscribe()
        sub.queue = asyncio.Queue(2)
        return sub

    monkeypatch.setattr(ctx.bus, "subscribe", tiny)
    gen = follow(ctx, 0, idle_s=1.0)
    assert seqs(await take(gen, 1)) == [1]
    for i in range(5):  # 2 fit, the rest overflow
        log(ctx, str(i))
    got = seqs(await drain(gen))
    assert got == [2, 3]
    assert ctx.bus.n_subscribers == 0
    resumed = follow(ctx, got[-1], idle_s=1.0)
    assert seqs(await take(resumed, 3)) == [4, 5, 6]
    await resumed.aclose()


async def test_a_gap_in_the_queue_is_filled_from_the_file(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.emit_created()
    real_subscribe = ctx.bus.subscribe

    def lossy() -> Subscription:
        sub = real_subscribe()
        push = sub.push

        def drop_seq_3(item: Item) -> None:
            if item is None or item[0] != 3:
                push(item)

        sub.push = drop_seq_3  # type: ignore[method-assign]
        return sub

    monkeypatch.setattr(ctx.bus, "subscribe", lossy)
    gen = follow(ctx, 0, idle_s=1.0)
    assert seqs(await take(gen, 1)) == [1]
    for i in range(3):
        log(ctx, str(i))
    items = await take(gen, 3)
    assert seqs(items) == [2, 3, 4]
    lines = read_lines(ctx)
    assert [line for _, line in items] == lines[1:4]  # type: ignore[misc]
    await gen.aclose()


async def test_queued_items_already_replayed_are_skipped(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.emit_created()
    log(ctx, "a")
    real_subscribe = ctx.bus.subscribe

    def with_stale_items() -> Subscription:
        sub = real_subscribe()
        for line in read_lines(ctx):  # as if published after subscribing
            sub.push((line_seq(line), line))
        return sub

    monkeypatch.setattr(ctx.bus, "subscribe", with_stale_items)
    gen = follow(ctx, 0, idle_s=1.0)
    assert seqs(await take(gen, 2)) == [1, 2]
    log(ctx, "b")
    assert seqs(await take(gen, 1)) == [3]
    await gen.aclose()
