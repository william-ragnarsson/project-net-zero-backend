"""The HTTP API and the SSE stream, in process (ASGI) and over a real socket (uvicorn)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from netzero.api.app import create_app
from netzero.events import Heartbeat, RunEventAdapter
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.orchestrator import RunManager
from tests.integration.helpers import (
    event_frames,
    event_lines,
    make_settings,
    parse_sse,
    wait_until,
)
from tests.integration.scripted import FAST, IO, SLOW, Scripted


@dataclass
class Api:
    app: FastAPI
    manager: RunManager
    client: httpx.AsyncClient


ApiFactory = Callable[..., Awaitable[Api]]


@pytest.fixture
async def api_factory(runs_dir: Path) -> AsyncIterator[ApiFactory]:
    """An app with a scripted pipeline, its lifespan running, and an in-process client."""
    async with AsyncExitStack() as stack:

        async def make(pipeline=None, **overrides) -> Api:
            settings = make_settings(runs_dir, **overrides)
            manager = RunManager(settings, pipeline=pipeline or Scripted(), power=lambda: None)
            app = create_app(settings, manager=manager)
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nz")
            )
            return Api(app, manager, client)

        yield make


async def start_run(api: Api, **body) -> str:
    r = await api.client.post("/api/runs", json={"demo": True} | body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def awaiting_selection(api: Api, run_id: str) -> None:
    await wait_until(lambda: (c := api.manager.live(run_id)) is not None and c.awaiting_selection)


def assert_error(r: httpx.Response, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["code"] == code and body["detail"]
    return body


# -- the stream ------------------------------------------------------------------------


async def test_stream_from_start_to_terminal(api_factory):
    api = await api_factory()
    r = await api.client.post("/api/runs", json={"demo": True, "auto_select": True})
    assert r.status_code == 201
    created = r.json()
    assert created["state"] == "created" and created["mode"] == "demo"
    run_id = created["id"]

    # through ASGITransport the body arrives once the stream has ended
    r = await api.client.get(f"/api/runs/{run_id}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(r.text)
    assert frames[0]["retry"] == 2000 and frames[0]["comment"] == ["netzero"]

    evs = event_frames(frames)
    lines = [f["data"] for f in evs]
    for f in evs:
        RunEventAdapter.validate_json(f["data"])
        assert int(f["id"]) == json.loads(f["data"])["seq"]
    assert lines == event_lines(api.manager, run_id)
    assert_grammar(lines)
    assert json.loads(lines[-1])["type"] == "run.completed"

    # the raw log is the same
    r = await api.client.get(f"/api/runs/{run_id}/events.jsonl")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["cache-control"] == "no-store"
    assert r.text.splitlines() == lines


async def test_resume_after_the_run_ended(api_factory):
    api = await api_factory()
    run_id = await start_run(api, auto_select=True)
    await asyncio.wait_for(api.manager.wait(run_id), 5)
    n = len(event_lines(api.manager, run_id))
    url = f"/api/runs/{run_id}/events"

    r = await api.client.get(url, headers={"Last-Event-ID": str(n)})
    assert r.status_code == 204 and r.content == b""
    r = await api.client.get(url, params={"after": n + 10})
    assert r.status_code == 204

    def seqs(r: httpx.Response) -> list[int]:
        assert r.status_code == 200
        return [int(f["id"]) for f in event_frames(parse_sse(r.text))]

    assert seqs(await api.client.get(url, params={"after": 3})) == list(range(4, n + 1))
    assert seqs(await api.client.get(url, headers={"Last-Event-ID": str(n - 2)})) == [n - 1, n]
    # both given: the later position wins
    r = await api.client.get(url, params={"after": 2}, headers={"Last-Event-ID": "5"})
    assert seqs(r)[0] == 6
    r = await api.client.get(url, params={"after": 7}, headers={"Last-Event-ID": "5"})
    assert seqs(r)[0] == 8
    # garbage positions count as 0
    r = await api.client.get(url, params={"after": "x"}, headers={"Last-Event-ID": "-4"})
    assert seqs(r) == list(range(1, n + 1))


async def test_live_follow_with_heartbeats_and_selection(api_factory):
    api = await api_factory(sse_heartbeat_s=0.05)
    run_id = await start_run(api)
    stream = asyncio.create_task(api.client.get(f"/api/runs/{run_id}/events"))
    await awaiting_selection(api, run_id)
    ctx = api.manager.live(run_id)
    await wait_until(lambda: ctx.bus.n_subscribers == 1)
    await asyncio.sleep(0.2)  # a few idle heartbeats

    r = await api.client.post(f"/api/runs/{run_id}/select", json={"function_ids": [FAST]})
    assert r.status_code == 200 and r.json()["id"] == run_id

    r = await asyncio.wait_for(stream, 5)
    frames = parse_sse(r.text)
    beats = [f for f in frames if f["event"] == "heartbeat"]
    assert beats, "no heartbeat while the run waited"
    for f in beats:
        assert f["id"] is None  # never resumable, never persisted
        hb = Heartbeat.model_validate_json(f["data"])
        assert hb.run_id == run_id and hb.state == "awaiting_selection"
    lines = [f["data"] for f in event_frames(frames)]
    assert lines == event_lines(api.manager, run_id)
    assert_grammar(lines)
    sel = next(json.loads(x) for x in lines if '"run.selection.confirmed"' in x)
    assert sel["data"] == {"function_ids": [FAST], "auto": False}
    assert ctx.bus.n_subscribers == 0


async def test_two_followers_get_the_same_events(api_factory):
    api = await api_factory()
    run_id = await start_run(api)
    url = f"/api/runs/{run_id}/events"
    first = asyncio.create_task(api.client.get(url))
    await awaiting_selection(api, run_id)
    second = asyncio.create_task(api.client.get(url, params={"after": 2}))
    await wait_until(lambda: api.manager.live(run_id).bus.n_subscribers == 2)
    await api.client.post(f"/api/runs/{run_id}/select", json={"function_ids": [SLOW]})
    a, b = await asyncio.wait_for(asyncio.gather(first, second), 5)
    lines_a = [f["data"] for f in event_frames(parse_sse(a.text))]
    lines_b = [f["data"] for f in event_frames(parse_sse(b.text))]
    assert lines_a[2:] == lines_b
    assert lines_a == event_lines(api.manager, run_id)


# -- commands and errors ---------------------------------------------------------------


async def test_unknown_runs_are_404(api_factory):
    api = await api_factory()
    for path in (
        "/api/runs/20990101-000000-nope",
        "/api/runs/20990101-000000-nope/events",
        "/api/runs/20990101-000000-nope/events.jsonl",
        "/api/runs/not..a..run/events",
        "/api/runs/20990101-000000-nope/artifacts/patch",
    ):
        assert_error(await api.client.get(path), 404, "not_found")
    r = await api.client.post(
        "/api/runs/20990101-000000-nope/select", json={"function_ids": [FAST]}
    )
    assert_error(r, 404, "not_found")
    assert_error(await api.client.post("/api/runs/20990101-000000-nope/cancel"), 404, "not_found")


@pytest.mark.parametrize(
    "body, code",
    [
        ({}, "bad_request"),
        ({"demo": True, "github_url": "https://github.com/a/b"}, "bad_request"),
        ({"demo": True, "colour": "green"}, "bad_request"),
        ({"demo": "please"}, "bad_request"),
        ({"github_url": "https://gitlab.com/a/b"}, "invalid_url"),
        ({"github_url": "https://github.com/a/b", "ref": "../x"}, "invalid_url"),
    ],
)
async def test_create_validation(api_factory, body, code):
    api = await api_factory()
    assert_error(await api.client.post("/api/runs", json=body), 400, code)


async def test_missing_key_and_missing_demo_are_412(api_factory):
    api = await api_factory(fake_pipeline=False)
    r = await api.client.post("/api/runs", json={"github_url": "https://github.com/a/b"})
    assert_error(r, 412, "no_api_key")
    caps = (await api.client.get("/api/capabilities")).json()
    assert caps["modes"] == ["demo"] and caps["has_api_key"] is False


async def test_one_run_at_a_time_and_select_validation(api_factory):
    api = await api_factory()
    run_id = await start_run(api)
    await awaiting_selection(api, run_id)

    body = assert_error(await api.client.post("/api/runs", json={"demo": True}), 409, "run_active")
    assert body["active_run"]["id"] == run_id
    assert body["active_run"]["state"] == "awaiting_selection"
    caps = (await api.client.get("/api/capabilities")).json()
    assert caps["active_run"]["id"] == run_id

    sel = f"/api/runs/{run_id}/select"
    for bad in ({"function_ids": []}, {"function_ids": "x"}, {"function_ids": [FAST], "all": 1}):
        assert_error(await api.client.post(sel, json=bad), 400, "bad_request")
    assert_error(await api.client.post(sel, json={"function_ids": ["a:b"]}), 400, "bad_request")
    assert_error(await api.client.post(sel, json={"function_ids": [IO]}), 400, "bad_request")

    r = await api.client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    r = await api.client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert_error(await api.client.post(sel, json={"function_ids": [FAST]}), 409, "bad_state")

    last = json.loads(event_lines(api.manager, run_id)[-1])
    assert last["type"] == "run.cancelled"
    r = await api.client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"})
    assert r.status_code == 200
    assert json.loads(event_frames(parse_sse(r.text))[-1]["data"])["type"] == "run.cancelled"
    # the slot is free again
    await start_run(api, auto_select=True)


async def test_list_detail_health_capabilities(api_factory):
    api = await api_factory()
    run_id = await start_run(api, auto_select=True)
    await asyncio.wait_for(api.manager.wait(run_id), 5)

    runs = (await api.client.get("/api/runs")).json()
    assert [r["id"] for r in runs] == [run_id] and runs[0]["state"] == "completed"
    d = (await api.client.get(f"/api/runs/{run_id}")).json()
    assert d["state"] == "completed"
    assert [f["function_id"] for f in d["functions"]] == [FAST, SLOW]
    assert len(d["triage"]) == 3
    assert d["last_seq"] == len(event_lines(api.manager, run_id))

    assert (await api.client.get("/api/health")).json()["ok"] is True
    caps = (await api.client.get("/api/capabilities")).json()
    assert caps["modes"] == ["live", "demo"] and caps["active_run"] is None
    assert caps["demo_available"] is True


async def test_artifacts(api_factory):
    api = await api_factory()
    run_id = await start_run(api, auto_select=True)
    await asyncio.wait_for(api.manager.wait(run_id), 5)
    base = f"/api/runs/{run_id}/artifacts"
    rp = api.manager.store.paths(run_id)

    assert_error(await api.client.get(f"{base}/patch"), 404, "not_found")
    assert_error(await api.client.get(f"{base}/zip"), 404, "not_found")
    assert_error(await api.client.get(f"{base}/functions/{FAST}/diff"), 404, "not_found")
    assert_error(await api.client.get(f"{base}/functions/{IO}/diff"), 404, "not_found")

    rp.out.mkdir(parents=True, exist_ok=True)
    (rp.out / f"{run_id}.patch").write_text("diff --git a/x b/x\n")
    (rp.out / f"{run_id}.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    rp.fn(FAST).mkdir(parents=True, exist_ok=True)
    (rp.fn(FAST) / "merge.diff").write_text("-slow\n+fast\n")

    r = await api.client.get(f"{base}/patch")
    assert r.status_code == 200 and r.text.startswith("diff --git")
    assert r.headers["content-type"].startswith("text/x-diff")
    assert f"netzero-{run_id}.patch" in r.headers["content-disposition"]
    r = await api.client.get(f"{base}/zip")
    assert r.status_code == 200 and r.content.startswith(b"PK")
    r = await api.client.get(f"{base}/functions/{FAST}/diff")
    assert r.status_code == 200 and r.text == "-slow\n+fast\n"


async def test_fake_pipeline_end_to_end(api_factory):
    from netzero.pipeline.fake import FakePipeline

    api = await api_factory(pipeline=FakePipeline(speed=1000))
    run_id = await start_run(api, auto_select=True)
    r = await asyncio.wait_for(api.client.get(f"/api/runs/{run_id}/events"), 30)
    lines = [f["data"] for f in event_frames(parse_sse(r.text))]
    assert_grammar(lines)
    last = json.loads(lines[-1])
    assert last["type"] == "run.completed"
    summary = last["data"]["summary"]
    assert summary["functions_done"] == summary["functions_total"] == 8
    assert summary["counts_by_outcome"] == {
        "accepted": 6,
        "all_rejected": 1,
        "no_significant_win": 1,
    }

    detail = (await api.client.get(f"/api/runs/{run_id}")).json()
    assert detail["state"] == "completed"
    accepted = [
        json.loads(line)["function_id"]
        for line in lines
        if '"type":"function.completed"' in line and '"outcome":"accepted"' in line
    ]
    assert len(accepted) == 6

    base = f"/api/runs/{run_id}/artifacts"
    r = await api.client.get(f"{base}/patch")
    assert r.status_code == 200 and r.text.startswith("diff --git")
    assert (await api.client.get(f"{base}/zip")).content.startswith(b"PK")
    r = await api.client.get(f"{base}/functions/{accepted[0]}/diff")
    assert r.status_code == 200 and "+" in r.text


# -- over a real socket ----------------------------------------------------------------


@dataclass
class Live:
    app: FastAPI
    server: uvicorn.Server
    task: asyncio.Task
    url: str

    @property
    def manager(self) -> RunManager:
        return self.app.state.manager

    async def stop(self, timeout: float = 10) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout)


@pytest.fixture
async def live_server(runs_dir: Path) -> AsyncIterator[Live]:
    settings = make_settings(runs_dir, sse_heartbeat_s=0.05)
    manager = RunManager(settings, pipeline=Scripted(), power=lambda: None)
    app = create_app(settings, manager=manager)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await wait_until(lambda: server.started or task.done())
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    live = Live(app, server, task, f"http://127.0.0.1:{port}")
    yield live
    if not task.done():
        await live.stop()


async def read_frames(
    resp: httpx.Response, until: Callable[[dict], bool] | None = None
) -> list[dict]:
    """Frames from an open stream, up to and including the first that satisfies ``until``."""
    frames: list[dict] = []
    buf = ""
    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            for f in parse_sse(block + "\n\n"):
                frames.append(f)
                if until is not None and until(f):
                    return frames
    return frames


async def test_disconnect_and_resume_over_http(live_server):
    async with httpx.AsyncClient(base_url=live_server.url, timeout=5) as c:
        run_id = (await c.post("/api/runs", json={"demo": True})).json()["id"]
        url = f"/api/runs/{run_id}/events"

        async with c.stream("GET", url) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            first = await read_frames(
                resp,
                until=lambda f: (
                    f["event"] == "heartbeat"
                    and json.loads(f["data"])["state"] == "awaiting_selection"
                ),
            )
        # the client hung up; the server notices on its next write
        ctx = live_server.manager.live(run_id)
        await wait_until(lambda: ctx.bus.n_subscribers == 0)

        seen = [int(f["id"]) for f in event_frames(first)]
        assert seen == list(range(1, len(seen) + 1))
        r = await c.post(f"/api/runs/{run_id}/select", json={"function_ids": [FAST, SLOW]})
        assert r.status_code == 200

        async with c.stream("GET", url, headers={"Last-Event-ID": str(seen[-1])}) as resp:
            rest = await read_frames(resp)
        more = [int(f["id"]) for f in event_frames(rest)]
        assert more[0] == seen[-1] + 1
        lines = [f["data"] for f in event_frames(first) + event_frames(rest)]
        assert lines == event_lines(live_server.manager, run_id)
        assert_grammar(lines)

        r = await c.get(url, headers={"Last-Event-ID": str(more[-1])})
        assert r.status_code == 204


@pytest.mark.slow
async def test_server_shutdown_interrupts_the_run(live_server):
    async with httpx.AsyncClient(base_url=live_server.url, timeout=10) as c:
        run_id = (await c.post("/api/runs", json={"demo": True})).json()["id"]
        manager = live_server.manager

        async def follow() -> list[dict]:
            try:
                async with c.stream("GET", f"/api/runs/{run_id}/events") as resp:
                    return await read_frames(resp)
            except httpx.HTTPError:
                return []

        stream = asyncio.create_task(follow())
        await wait_until(lambda: (x := manager.live(run_id)) is not None and x.awaiting_selection)
        await wait_until(lambda: manager.live(run_id).bus.n_subscribers == 1)
        # an open SSE stream must not keep the server alive
        await live_server.stop(timeout=5)
        await asyncio.wait_for(stream, 5)

    lines = event_lines(manager, run_id)
    assert_grammar(lines)
    last = json.loads(lines[-1])
    assert last["type"] == "run.interrupted"
    assert last["data"]["previous_state"] == "awaiting_selection"
    assert manager.active is None
