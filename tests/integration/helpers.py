"""Helpers shared by the integration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from netzero.api.schemas import CreateRunRequest
from netzero.config import Settings
from netzero.pipeline.orchestrator import RunManager
from netzero.pipeline.store import read_complete_lines


def make_settings(runs_dir: Path, **overrides) -> Settings:
    # fake_pipeline lifts the API-key and demo-repo checks; the tests pass
    # their own pipeline, so FakePipeline itself is never used here
    base = dict(_env_file=None, ANTHROPIC_API_KEY=None, runs_dir=runs_dir, fake_pipeline=True)
    return Settings(**(base | overrides))


DEMO = CreateRunRequest(demo=True)
DEMO_AUTO = CreateRunRequest(demo=True, auto_select=True)


def event_lines(manager: RunManager, run_id: str) -> list[str]:
    lines, _ = read_complete_lines(manager.store.paths(run_id).events)
    return lines


def events(manager: RunManager, run_id: str) -> list[dict]:
    return [json.loads(line) for line in event_lines(manager, run_id)]


def types(manager: RunManager, run_id: str) -> list[str]:
    return [e["type"] for e in events(manager, run_id)]


async def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


def parse_sse(text: str) -> list[dict]:
    """Split an SSE body into frames: ``{"comment", "event", "id", "data", "retry"}``."""
    frames = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        frame: dict = {"comment": [], "event": None, "id": None, "data": None, "retry": None}
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                frame["comment"].append(line[1:].strip())
                continue
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "data":
                data.append(value)
            elif name in ("event", "id"):
                frame[name] = value
            elif name == "retry":
                frame["retry"] = int(value)
        frame["data"] = "\n".join(data) if data else None
        frames.append(frame)
    return frames


def event_frames(frames: list[dict]) -> list[dict]:
    """Frames that carry a run event (not the preamble, heartbeats or pings)."""
    return [f for f in frames if f["data"] is not None and f["event"] is None]
