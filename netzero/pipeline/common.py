"""Pieces the real pipeline and the FakePipeline share: candidate ids, diffs, task
reaping and the number formats used in logs and reasons."""

from __future__ import annotations

import asyncio
import difflib

from netzero.events import CI, CandidateId

CANDIDATES: tuple[CandidateId, ...] = ("A", "B", "C")
STRATEGY_HINTS: dict[str, str] = {
    "A": "algorithmic: better complexity",
    "B": "builtins/stdlib: idiomatic fast paths",
    "C": "conservative: micro-optimizations only",
}


def unified_diff(path: str, before: str, after: str) -> str:
    """A ``git diff``-style patch for one file (empty when nothing changed)."""
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}" if body else ""


async def reap(tasks: list[asyncio.Task]) -> None:
    """Cancel unfinished tasks and wait until they have all finished.

    A cancellation of the caller while it waits is deferred until every task
    is done (so none of them can emit after the function completes), then re-raised.
    """
    pending = [t for t in tasks if not t.done()]
    for t in pending:
        t.cancel()
    cancelled = False
    while pending:
        try:
            await asyncio.wait(pending)
        except asyncio.CancelledError:
            cancelled = True
        pending = [t for t in pending if not t.done()]
    for t in tasks:
        if not t.cancelled():
            t.exception()  # mark retrieved; gather already propagated the first one
    if cancelled:
        raise asyncio.CancelledError


def pct(x: float) -> str:
    return f"{x:+.1f}".replace("-", "−")


def fmt_p(p: float, label: str = "p") -> str:
    """``p=0.0003`` / ``p<1e-6``."""
    return f"{label}<1e-6" if p < 1e-6 else f"{label}={p:.2g}"


def ci_text(ci: CI) -> str:
    return f"95% CI {pct(ci.lo)}…{pct(ci.hi)}"
