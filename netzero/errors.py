"""Exceptions that map onto ``ErrorInfo`` in step completions and ``run.failed``."""

from __future__ import annotations

import asyncio

from netzero.events import ErrorInfo, ErrorKind


class NetzeroError(Exception):
    kind: ErrorKind = "internal"

    def __init__(self, message: str, *, detail: str | None = None, kind: ErrorKind | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        if kind is not None:
            self.kind = kind

    def info(self) -> ErrorInfo:
        return ErrorInfo(kind=self.kind, message=self.message, detail=self.detail)


class CloneError(NetzeroError):
    kind: ErrorKind = "clone_error"


class EnvError(NetzeroError):
    kind: ErrorKind = "env_error"


class SandboxError(NetzeroError):
    kind: ErrorKind = "sandbox_error"


class StepTimeout(NetzeroError):
    kind: ErrorKind = "timeout"


class LlmError(NetzeroError):
    """``kind`` is ``llm_error``, ``llm_refusal`` or ``cassette_miss``."""

    kind: ErrorKind = "llm_error"


class BenchError(NetzeroError):
    kind: ErrorKind = "bench_error"


class ValidationFailed(NetzeroError):
    kind: ErrorKind = "validation"


def error_info(exc: BaseException) -> ErrorInfo:
    """Best-effort ``ErrorInfo`` for any exception."""
    if isinstance(exc, NetzeroError):
        return exc.info()
    if isinstance(exc, asyncio.CancelledError):
        return ErrorInfo(kind="cancelled", message="cancelled")
    if isinstance(exc, TimeoutError):
        return ErrorInfo(kind="timeout", message=str(exc) or "timed out")
    return ErrorInfo(kind="internal", message=f"{type(exc).__name__}: {exc}")
