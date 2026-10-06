"""An event loop whose clock jumps to the next timer instead of waiting for it.

``asyncio.Runner(loop_factory=VirtualTimeLoop)`` runs sleep-driven code (the
FakePipeline) in no wall time while ``loop.time()`` advances exactly as if it
had waited, so timestamps and durations keep their scripted shape. Real I/O
still works: the selector is polled before every jump, and blocks for real
only when no timer is pending. Threads are not tracked, so time may jump past
a thread that is still running.
"""

from __future__ import annotations

import asyncio
import selectors


class _JumpingSelector(selectors.DefaultSelector):  # type: ignore[misc,valid-type]
    def __init__(self, loop: VirtualTimeLoop):
        super().__init__()
        self._loop = loop

    def select(self, timeout: float | None = None):
        ready = super().select(0)
        if ready or timeout is not None and timeout <= 0:
            return ready
        if timeout is None:  # nothing scheduled: only I/O can wake us
            return super().select(None)
        self._loop.advance(timeout)
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._now = 0.0
        super().__init__(_JumpingSelector(self))

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
