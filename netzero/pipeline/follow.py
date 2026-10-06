"""Follow a live run: the persisted log first, then the bus, without gaps or repeats."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from netzero.events import TERMINAL_EVENT_TYPES, line_type
from netzero.pipeline.run import RunContext


async def follow(
    ctx: RunContext, after: int, *, idle_s: float
) -> AsyncIterator[tuple[int, str] | None]:
    """Yield ``(seq, line)`` for every event after ``after``, in order.

    Subscribing before reading the file means nothing falls between the two:
    every event is on disk before it is published, and queued events that the
    file already supplied are skipped by seq.

    Yields ``None`` after ``idle_s`` without events (time for a heartbeat).
    Ends after the terminal event, when the bus closes, or when this
    subscriber overflowed; the caller can resume from its last seq.
    """
    store = ctx.store
    sub = ctx.bus.subscribe()
    last = after
    try:
        for seq, line in store.iter_lines(ctx.run_id, after=last):
            yield seq, line
            last = seq
            if line_type(line) in TERMINAL_EVENT_TYPES:
                return
        while True:
            if sub.overflowed and sub.queue.empty():
                return  # too slow
            try:
                item = await asyncio.wait_for(sub.get(), idle_s)
            except TimeoutError:
                yield None
                continue
            if item is None:
                # the bus closed: whatever it wrote is on disk
                for seq, line in store.iter_lines(ctx.run_id, after=last):
                    yield seq, line
                    last = seq
                return
            seq, line = item
            if seq <= last:
                continue
            if seq > last + 1:  # defensive: fill a gap from the file
                for s, ln in store.iter_lines(ctx.run_id, after=last):
                    if s >= seq:
                        break
                    yield s, ln
                    last = s
            yield seq, line
            last = seq
            if line_type(line) in TERMINAL_EVENT_TYPES:
                return
    finally:
        ctx.bus.unsubscribe(sub)
