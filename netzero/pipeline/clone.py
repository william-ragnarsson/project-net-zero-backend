"""Get the target repo into ``runs/<id>/repo``: GitHub URL validation and cloning."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from netzero.errors import CloneError, NetzeroError
from netzero.pipeline.discovery import EXCLUDED_DIRS
from netzero.pipeline.git import commit_all, git, head_sha
from netzero.sandbox import runner
from netzero.sandbox.procs import ProcRegistry

GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class InvalidUrl(NetzeroError):
    kind = "validation"


@dataclass(frozen=True)
class GithubRepo:
    owner: str
    repo: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def label(self) -> str:
        return f"{self.owner}-{self.repo}"


def parse_github_url(url: str) -> GithubRepo:
    """Accept only ``https://github.com/<owner>/<repo>[.git][/]``."""
    m = GITHUB_URL_RE.match(url.strip())
    if not m or m["repo"] in (".", ".."):
        raise InvalidUrl(
            "expected a GitHub repository URL like https://github.com/owner/repo",
            detail=url[:200],
        )
    return GithubRepo(m["owner"], m["repo"])


def validate_ref(ref: str | None) -> str | None:
    if ref is None or ref == "":
        return None
    if not REF_RE.match(ref) or ref.startswith(("-", "/")) or ".." in ref:
        raise InvalidUrl("invalid git ref", detail=ref[:200])
    return ref


@dataclass(frozen=True)
class CloneResult:
    commit_sha: str
    n_files: int
    n_py_files: int
    size_bytes: int


def repo_stats(root: Path) -> tuple[int, int, int]:
    """(files, .py files, bytes), ignoring ``.git``; symlinks are not followed."""
    n_files = n_py = size = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            n_files += 1
            n_py += name.endswith(".py")
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return n_files, n_py, size


def check_limits(root: Path, *, max_mb: int, max_files: int) -> tuple[int, int, int]:
    n_files, n_py, size = repo_stats(root)
    if n_files > max_files:
        raise CloneError(f"repository has {n_files} files; the limit is {max_files}")
    if size > max_mb * 1024 * 1024:
        raise CloneError(f"repository is {size / 2**20:.0f} MB; the limit is {max_mb} MB")
    if n_py == 0:
        raise CloneError("repository has no Python files")
    return n_files, n_py, size


async def clone_github(
    repo: GithubRepo,
    ref: str | None,
    dest: Path,
    *,
    timeout: float,
    max_mb: int,
    max_files: int,
    procs: ProcRegistry | None = None,
    on_line: runner.LineCallback | None = None,
) -> CloneResult:
    """Shallow, single-branch clone over https only; no submodules, no LFS blobs."""
    args = [
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
    ]
    if ref:
        args += ["--branch", ref]
    args += ["--", f"{repo.url}.git", str(dest)]
    r = await git(args, cwd=dest.parent, procs=procs, timeout=timeout, on_line=on_line, check=False)
    if not r.ok:
        shutil.rmtree(dest, ignore_errors=True)
        tail = r.tail(8)
        low = tail.lower()
        if r.timed_out:
            msg = f"clone timed out after {timeout:.0f} s"
        elif ref and "remote branch" in low:
            msg = f"branch or tag {ref!r} not found"
        elif "not found" in low or "could not read username" in low:
            msg = "repository not found (or it is private)"
        else:
            msg = "git clone failed"
        raise CloneError(msg, detail=tail)
    n_files, n_py, size = check_limits(dest, max_mb=max_mb, max_files=max_files)
    return CloneResult(await head_sha(dest, procs), n_files, n_py, size)


DEMO_IGNORE = shutil.ignore_patterns(
    ".netzero-cassettes", ".git", "*.pyc", ".pytest_cache", ".venv", *EXCLUDED_DIRS
)


async def copy_demo(src: Path, dest: Path, *, procs: ProcRegistry | None = None) -> CloneResult:
    """Copy the bundled demo repo and commit it with a fixed date, so its base
    commit is the same everywhere (cassettes and replays refer to it)."""
    if not src.is_dir():
        raise CloneError(f"the bundled demo repo is missing ({src})")
    shutil.copytree(src, dest, ignore=DEMO_IGNORE, symlinks=True)
    await git(["init", "-q"], cwd=dest, procs=procs)
    sha = await commit_all(dest, "netzero demo repo", procs, fixed_date=True)
    n_files, n_py, size = repo_stats(dest)
    return CloneResult(sha, n_files, n_py, size)
