"""Shared fixtures: isolated settings, a temp ``RunStore`` and ``RunContext`` factories."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from netzero.config import Settings
from netzero.events import RunMode, RunSource
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import RunStore


class FakeClock:
    """Deterministic millisecond clock for event timestamps and step durations."""

    def __init__(self, start: int = 1_700_000_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> int:
        self.now += ms
        return self.now


def make_context(
    store: RunStore,
    settings: Settings,
    *,
    label: str = "test",
    mode: RunMode = "demo",
    source: RunSource | None = None,
    clock: Callable[[], int] | None = None,
) -> RunContext:
    """A fresh ``RunContext`` with its run folder created under ``store.runs_dir``."""
    run_id = store.new_run_id(label)
    paths = store.paths(run_id)
    paths.mkdirs()
    return RunContext(
        run_id=run_id,
        paths=paths,
        settings=settings,
        store=store,
        mode=mode,
        source=source or RunSource(kind="demo"),
        clock=clock,
    )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings isolated from the developer's environment and ``.env``."""
    for key in list(os.environ):
        if key.startswith("NETZERO_") or key == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key)
    return Settings(_env_file=None, runs_dir=tmp_path / "runs")  # type: ignore[call-arg]


@pytest.fixture
def store(settings: Settings) -> RunStore:
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return RunStore(settings.runs_dir)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_ctx(store: RunStore, settings: Settings) -> Iterator[Callable[..., RunContext]]:
    """Factory for ``RunContext``s on the temp store; every context is closed at teardown."""
    created: list[RunContext] = []

    def factory(**kwargs) -> RunContext:
        ctx = make_context(store, settings, **kwargs)
        created.append(ctx)
        return ctx

    yield factory
    for ctx in created:
        ctx.close()


@pytest.fixture
def ctx(make_ctx: Callable[..., RunContext], clock: FakeClock) -> RunContext:
    """A live ``RunContext`` driven by the fake clock."""
    return make_ctx(clock=clock)
