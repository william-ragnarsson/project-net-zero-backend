"""Thin async wrapper around the ``git`` CLI, with a fixed identity and no user config."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from netzero.errors import NetzeroError
from netzero.events import ErrorKind
from netzero.sandbox import runner
from netzero.sandbox.env_scrub import tool_env
from netzero.sandbox.procs import ProcRegistry

# every call: a fixed identity, no hooks, no signing, no pager (M9)
GIT_CONFIG = (
    "-c",
    "user.name=netzero",
    "-c",
    "user.email=netzero@localhost",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    "core.pager=cat",
    "-c",
    "advice.detachedHead=false",
    "-c",
    "init.defaultBranch=main",
)

# deterministic commits (the demo repo's base sha is the same on every machine)
FIXED_DATE = "2026-01-01T00:00:00+00:00"


class GitError(NetzeroError):
    kind: ErrorKind = "internal"


async def git(
    args: Sequence[str],
    *,
    cwd: Path,
    procs: ProcRegistry | None = None,
    timeout: float = 60.0,
    on_line: runner.LineCallback | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    kind: ErrorKind = "internal",
) -> runner.ProcResult:
    e = tool_env({"GIT_LFS_SKIP_SMUDGE": "1", **(env or {})})
    r = await runner.run(
        ["git", *GIT_CONFIG, *args],
        cwd=cwd,
        env=e,
        timeout=timeout,
        procs=procs,
        label=f"git {_verb(args)}",
        on_line=on_line,
    )
    if check and not r.ok:
        what = "timed out" if r.timed_out else f"exited with {r.returncode}"
        raise GitError(f"git {_verb(args)} {what}", detail=r.tail(20), kind=kind)
    return r


def _verb(args: Sequence[str]) -> str:
    """The subcommand, skipping leading ``-c key=value`` options."""
    it = iter(args)
    for a in it:
        if a == "-c":
            next(it, None)
        elif not a.startswith("-"):
            return a
    return args[0] if args else "?"


async def head_sha(repo: Path, procs: ProcRegistry | None = None) -> str:
    return (await git(["rev-parse", "HEAD"], cwd=repo, procs=procs)).stdout.strip()


async def commit_all(
    repo: Path, message: str, procs: ProcRegistry | None = None, *, fixed_date: bool = False
) -> str:
    await git(["add", "-A"], cwd=repo, procs=procs)
    env = {"GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE} if fixed_date else None
    await git(
        ["commit", "-q", "--no-verify", "--allow-empty", "-m", message],
        cwd=repo,
        procs=procs,
        env=env,
    )
    return await head_sha(repo, procs)


async def commit_files(
    repo: Path,
    message: str,
    files: Sequence[str],
    procs: ProcRegistry | None = None,
    *,
    fixed_date: bool = False,
) -> str:
    """Commit just ``files`` (repo-relative), whatever else the worktree holds."""
    await git(["add", "--", *files], cwd=repo, procs=procs)
    env = {"GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE} if fixed_date else None
    await git(
        ["commit", "-q", "--no-verify", "-m", message, "--", *files],
        cwd=repo,
        procs=procs,
        env=env,
    )
    return await head_sha(repo, procs)
