"""The trunk: a worktree of the clone where accepted rewrites are committed, one per function.

``repo/`` stays at the base commit; ``work/trunk`` is on the ``netzero/trunk``
branch, so the patch is ``git diff <base> HEAD`` there.
"""

from __future__ import annotations

from pathlib import Path

from netzero.pipeline.git import git, head_sha
from netzero.sandbox.procs import ProcRegistry

TRUNK_BRANCH = "netzero/trunk"


async def create_trunk(
    repo: Path, trunk: Path, base_sha: str, procs: ProcRegistry | None = None
) -> str:
    """Add the trunk worktree at ``base_sha``; returns its HEAD."""
    trunk.parent.mkdir(parents=True, exist_ok=True)
    await git(
        ["worktree", "add", "-q", "-b", TRUNK_BRANCH, str(trunk), base_sha], cwd=repo, procs=procs
    )
    return await head_sha(trunk, procs)
