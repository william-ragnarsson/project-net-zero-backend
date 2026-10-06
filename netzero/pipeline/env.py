"""A dedicated venv per run: ``uv venv`` + ``uv pip install`` of the repo's deps and pytest.

The project itself is never installed; its modules are imported from the
trunk worktree through ``PYTHONPATH`` (the import roots). An import probe
records which modules import cleanly.

``python -m netzero.pipeline.env --warm-demo`` installs the demo lockfile into a
throwaway venv once, so later demo runs install ``--offline`` from uv's cache.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from netzero import paths as nz_paths
from netzero.errors import EnvError
from netzero.events import ImportProbe, PackageInfo
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import sandbox_env, tool_env, venv_python
from netzero.sandbox.harness.probe_imports import MARK
from netzero.sandbox.procs import ProcRegistry

PYTHON_REQUEST = "3.12"
TEST_DEPS = ("pytest>=8,<10",)
HARNESS_PACKAGE = "_netzero_harness"
DEMO_LOCK = "requirements.lock"
NON_RUNTIME_REQS = ("dev", "doc", "lint", "test", "ci", "build")


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise EnvError("uv is not installed (https://docs.astral.sh/uv/)")
    return uv


def dependency_sources(repo: Path, *, demo: bool) -> list[Path]:
    """What ``uv pip install`` reads: the demo lock, ``requirements.txt``, another
    runtime ``requirements*.txt``, or the project metadata (compiled first)."""
    if demo and (repo / DEMO_LOCK).is_file():
        return [repo / DEMO_LOCK]
    if (repo / "requirements.txt").is_file():
        return [repo / "requirements.txt"]
    reqs = sorted(
        p
        for p in repo.glob("requirements*.txt")
        if p.is_file() and not any(w in p.name.lower() for w in NON_RUNTIME_REQS)
    )
    if reqs:
        return reqs[:1]
    for name in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (repo / name).is_file():
            return [repo / name]
    return []


def install_harness(harness_root: Path) -> Path:
    """Copy the harness package to ``<harness_root>/_netzero_harness`` (replacing any
    old copy); ``harness_root`` is what goes on ``PYTHONPATH``."""
    target = harness_root / HARNESS_PACKAGE
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        nz_paths.HARNESS_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    return harness_root


class EnvBuilder:
    """``uv`` runs with ``tool_env`` from ``workdir`` (outside the repo) and
    ``--no-config``, so neither the repo's nor the user's uv settings apply."""

    def __init__(
        self,
        *,
        venv: Path,
        workdir: Path,
        procs: ProcRegistry | None = None,
        on_line: runner.LineCallback | None = None,
        timeout: float = 600.0,
    ):
        self.venv = venv
        self.workdir = workdir
        self.procs = procs
        self.on_line = on_line
        self.timeout = timeout
        self.uv = find_uv()

    @property
    def python(self) -> Path:
        return venv_python(self.venv)

    async def _uv(
        self, args: list[str], *, timeout: float, what: str, stream: bool = True
    ) -> runner.ProcResult:
        r = await runner.run(
            [self.uv, *args],
            cwd=self.workdir,
            env=tool_env(),
            timeout=timeout,
            procs=self.procs,
            label=f"uv {' '.join(args[:2])}",
            on_line=self.on_line if stream else None,
        )
        if not r.ok:
            reason = "timed out" if r.timed_out else f"failed (exit {r.returncode})"
            raise EnvError(f"{what} {reason}", detail=r.tail(30))
        return r

    async def _python(self, code: str) -> runner.ProcResult:
        return await runner.run(
            [self.python, "-c", code],
            cwd=self.workdir,
            env=tool_env(),
            timeout=60,
            procs=self.procs,
            label="python",
        )

    async def create(self) -> None:
        await self._uv(
            ["venv", "--quiet", "--no-config", "--python", PYTHON_REQUEST, str(self.venv)],
            timeout=300,
            what=f"creating a Python {PYTHON_REQUEST} venv",
        )

    async def install(self, sources: Sequence[Path], *, offline: bool = False) -> None:
        """Install ``sources`` plus pytest. ``offline`` tries uv's cache first and
        falls back to the network."""
        base = ["pip", "install", "--no-config", "--no-sources", "--python", str(self.python)]
        reqs: list[str] = []
        for src in sources:
            if src.suffix in (".txt", ".lock"):
                reqs += ["-r", str(src)]
            else:
                reqs += ["-r", str(await self._compile(src))]
        if offline:
            try:
                await self._uv(
                    [*base, "--offline", *reqs, *TEST_DEPS],
                    timeout=self.timeout,
                    what="installing dependencies offline",
                )
                return
            except EnvError:
                if self.on_line:
                    self.on_line("stderr", "offline install failed; retrying with network access")
        await self._uv(
            [*base, *reqs, *TEST_DEPS], timeout=self.timeout, what="installing dependencies"
        )

    async def _compile(self, src: Path) -> Path:
        """Pin a pyproject/setup.py's runtime dependencies into a requirements file."""
        out = self.workdir / f"{src.name}.requirements.txt"
        await self._uv(
            [
                "pip",
                "compile",
                "--quiet",
                "--no-config",
                "--no-sources",
                "--no-header",
                "--python",
                str(self.python),
                str(src),
                "-o",
                str(out),
            ],
            timeout=self.timeout,
            what=f"resolving the dependencies in {src.name}",
        )
        return out

    async def ensure_pytest(self) -> None:
        if not (await self._python("import pytest")).ok:
            await self._uv(
                ["pip", "install", "--no-config", "--python", str(self.python), *TEST_DEPS],
                timeout=self.timeout,
                what="installing pytest",
            )

    async def python_version(self) -> str:
        r = await self._python("import platform; print(platform.python_version())")
        return r.stdout.strip() if r.ok else "unknown"

    async def packages(self) -> list[PackageInfo]:
        r = await self._uv(
            ["pip", "list", "--no-config", "--python", str(self.python), "--format", "json"],
            timeout=120,
            what="listing packages",
            stream=False,  # one JSON line, not log material
        )
        try:
            return [PackageInfo(name=p["name"], version=p["version"]) for p in json.loads(r.stdout)]
        except (ValueError, KeyError, TypeError):
            return []

    async def probe_imports(
        self,
        modules: Sequence[str],
        *,
        cwd: Path,
        import_roots: Sequence[Path],
        harness_root: Path,
        home: Path,
        tmp: Path,
        max_crashes: int = 5,
    ) -> ImportProbe:
        """Import every module in the sandbox, one interpreter for all of them.

        Results stream in order, so when the interpreter dies (``os._exit``, a
        segfault, a hang past the timeout) the first module without a result is
        the culprit: it is marked failed and the rest are probed in a fresh one.
        Only the harness is on ``PYTHONPATH`` (``-P`` keeps the cwd off
        ``sys.path``); the harness prepends the import roots itself.
        """
        env = sandbox_env(venv=self.venv, home=home, tmp=tmp, pythonpath=[harness_root])
        argv = [self.python, "-P", "-m", f"{HARNESS_PACKAGE}.probe_imports"]
        roots = [str(r) for r in import_roots]
        pending = list(dict.fromkeys(modules))
        ok: list[str] = []
        failed: dict[str, str] = {}
        crashes = 0
        while pending:
            r = await runner.run(
                argv,
                cwd=cwd,
                env=env,
                timeout=60 + 3 * len(pending),
                procs=self.procs,
                label="import probe",
                stdin_data=json.dumps({"roots": roots, "modules": pending}).encode(),
            )
            waiting = set(pending)
            for line in r.stdout.splitlines():
                rec = _probe_record(line)
                if rec is None or rec[0] not in waiting:
                    continue  # not ours, or a module printing look-alike lines
                name, good, error = rec
                waiting.discard(name)
                if good:
                    ok.append(name)
                else:
                    failed[name] = error or "import failed"
            pending = [m for m in pending if m in waiting]
            if not pending:
                break
            culprit = pending.pop(0)
            why = "timed out" if r.timed_out else f"exit code {r.returncode}"
            failed[culprit] = f"importing it killed the interpreter ({why})"
            crashes += 1
            if crashes >= max_crashes:
                for m in pending:
                    failed[m] = "not probed: too many imports killed the interpreter"
                break
        return ImportProbe(ok_modules=ok, failed=failed)


def _probe_record(line: str) -> tuple[str, bool, str | None] | None:
    if not line.startswith(MARK):
        return None
    try:
        rec = json.loads(line[len(MARK) :])
        return str(rec["module"]), bool(rec["ok"]), rec.get("error")
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def warm_demo(lock: Path) -> None:
    """Fill uv's cache with the demo lockfile (and Python 3.12) for offline demo runs."""
    with tempfile.TemporaryDirectory(prefix="netzero-warm-") as tmp:
        b = EnvBuilder(
            venv=Path(tmp) / "venv",
            workdir=Path(tmp),
            on_line=lambda _s, line: print(line, file=sys.stderr),
        )
        await b.create()
        await b.install([lock])
    print(f"demo dependencies cached ({lock.name})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m netzero.pipeline.env")
    parser.add_argument(
        "--warm-demo", action="store_true", help="cache the demo repo's dependencies"
    )
    args = parser.parse_args()
    if args.warm_demo:
        lock = nz_paths.DEMO_REPO / DEMO_LOCK
        if not lock.is_file():
            print(f"no {lock}; nothing to warm", file=sys.stderr)
            return
        asyncio.run(warm_demo(lock))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
