from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from netzero.pipeline.orchestrator import PipelineFn, RunManager
from tests.integration.helpers import make_settings


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


@pytest.fixture
async def manager_factory(runs_dir: Path) -> AsyncIterator[Callable[..., RunManager]]:
    """Started managers over one runs dir; each is shut down after the test."""
    made: list[RunManager] = []

    def make(pipeline: PipelineFn, **overrides) -> RunManager:
        m = RunManager(make_settings(runs_dir, **overrides), pipeline=pipeline, power=lambda: None)
        m.start()
        made.append(m)
        return m

    yield make
    for m in made:
        await m.shutdown(timeout=2)
