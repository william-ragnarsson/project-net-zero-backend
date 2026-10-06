from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from netzero.sandbox import runner
from netzero.sandbox.env_scrub import is_secret_name, sandbox_env, tool_env
from netzero.sandbox.procs import ProcRegistry

PY = sys.executable


def env() -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "PYTHONUNBUFFERED": "1"}


def alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


SPAWN_GRANDCHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(child.pid, flush=True)
time.sleep({sleep})
"""


async def test_captures_output_and_exit_code(tmp_path: Path):
    lines: list[tuple[str, str]] = []
    r = await runner.run(
        [PY, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"],
        cwd=tmp_path,
        env=env(),
        timeout=10,
        on_line=lambda s, line: lines.append((s, line)),
    )
    assert r.returncode == 3 and not r.ok and not r.timed_out
    assert r.stdout == "a" and r.stderr == "b"
    assert sorted(lines) == [("stderr", "b"), ("stdout", "a")]


async def test_timeout_kills_the_whole_group(tmp_path: Path):
    procs = ProcRegistry(tmp_path / "procs.jsonl")
    t0 = time.perf_counter()
    r = await runner.run(
        [PY, "-c", SPAWN_GRANDCHILD.format(sleep=60)],
        cwd=tmp_path,
        env=env(),
        timeout=1.0,
        procs=procs,
    )
    assert r.timed_out and r.returncode is None
    assert time.perf_counter() - t0 < 8
    grandchild = int(r.stdout.split()[0])
    for _ in range(50):
        if not alive(grandchild):
            break
        await asyncio.sleep(0.05)
    assert not alive(grandchild)
    assert procs.live == {}
    ops = [json.loads(line)["op"] for line in procs.path.read_text().splitlines()]
    assert ops == ["start", "exit"]


async def test_a_daemon_holding_the_pipes_does_not_block(tmp_path: Path):
    # the leader exits at once; its grandchild keeps stdout open for 60 s
    t0 = time.perf_counter()
    r = await runner.run(
        [PY, "-c", SPAWN_GRANDCHILD.format(sleep=0)], cwd=tmp_path, env=env(), timeout=20
    )
    assert r.ok
    assert time.perf_counter() - t0 < 8
    grandchild = int(r.stdout.split()[0])
    for _ in range(50):
        if not alive(grandchild):
            break
        await asyncio.sleep(0.05)
    assert not alive(grandchild)


async def test_cancel_kills_the_group(tmp_path: Path):
    procs = ProcRegistry(tmp_path / "procs.jsonl")
    seen: list[str] = []
    task = asyncio.create_task(
        runner.run(
            [PY, "-c", SPAWN_GRANDCHILD.format(sleep=60)],
            cwd=tmp_path,
            env=env(),
            timeout=60,
            procs=procs,
            on_line=lambda s, line: seen.append(line),
        )
    )
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    grandchild = int(seen[0])
    for _ in range(50):
        if not alive(grandchild):
            break
        await asyncio.sleep(0.05)
    assert not alive(grandchild)
    assert procs.live == {}


async def test_output_is_capped_and_long_lines_split(tmp_path: Path):
    r = await runner.run(
        [PY, "-c", "print('x' * 200_000); [print(i) for i in range(5000)]"],
        cwd=tmp_path,
        env=env(),
        timeout=10,
        max_output=10_000,
    )
    assert r.ok and r.truncated
    assert len(r.stdout) <= 10_000 + 10
    assert r.stdout.endswith("4999")


async def test_stdin_data(tmp_path: Path):
    r = await runner.run(
        [PY, "-c", "import sys; print(sys.stdin.read().upper())"],
        cwd=tmp_path,
        env=env(),
        timeout=10,
        stdin_data=b"hello",
    )
    assert r.stdout == "HELLO"


def test_sandbox_env_is_allow_listed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CODECARBON_LOG_LEVEL", "debug")
    monkeypatch.setenv("PYTHONSTARTUP", "/evil.py")
    monkeypatch.setenv("SOME_RANDOM_VAR", "1")
    e = sandbox_env(
        venv=tmp_path / "venv", home=tmp_path / "home", tmp=tmp_path / "tmp", pythonpath=[tmp_path]
    )
    assert "ANTHROPIC_API_KEY" not in e and "SOME_RANDOM_VAR" not in e
    assert not any(k.startswith("CODECARBON_") for k in e)
    assert "PYTHONSTARTUP" not in e
    assert e["PATH"].split(os.pathsep)[0] == str(tmp_path / "venv" / "bin")
    assert e["HOME"] == str(tmp_path / "home") and e["TMPDIR"] == str(tmp_path / "tmp")
    assert e["PYTHONPATH"] == str(tmp_path)
    assert e["PYTHONHASHSEED"] == "0" and e["OMP_NUM_THREADS"] == "1"


def test_tool_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("MY_SERVICE_PASSWORD", "p")
    monkeypatch.setenv("NETZERO_FAKE_PIPELINE", "1")
    e = tool_env()
    for k in ("ANTHROPIC_API_KEY", "GH_TOKEN", "MY_SERVICE_PASSWORD", "NETZERO_FAKE_PIPELINE"):
        assert k not in e
    assert e["GIT_TERMINAL_PROMPT"] == "0" and e["PATH"] == os.environ["PATH"]
    assert is_secret_name("AWS_SECRET_ACCESS_KEY") and not is_secret_name("HOME")
