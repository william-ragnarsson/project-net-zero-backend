"""Finalize: the ``.patch`` (base commit -> optimized trunk) and the optimized repo ``.zip``.

The zip is ``git archive HEAD`` of the trunk under ``<repo>/``, plus
``netzero-report.json``, ``netzero.patch`` and the generated tests under
``netzero-tests/`` (they ran against the original; they are not wired into the
repo's own suite).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import zipfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from netzero.events import ArtifactRef, FunctionDiffRef, PatchRef
from netzero.pipeline.function_flow import TESTS_DIR, Merged
from netzero.pipeline.git import git
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import slugify

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _ref_bytes(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _add_text(zf: zipfile.ZipFile, name: str, text: str) -> None:
    zi = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    zf.writestr(zi, text)


def _extend_zip(
    archive: Path, dest: Path, extra: Sequence[tuple[str, str]], tests: Sequence[Path]
) -> None:
    with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zf:
        for name, text in extra:
            _add_text(zf, name, text)
        for path in tests:
            _add_text(zf, f"{TESTS_DIR}/{path.name}", path.read_text(encoding="utf-8"))
    os.replace(archive, dest)


def report(ctx: RunContext, base_sha: str, head: str) -> dict:
    totals = ctx.projection.totals(duration_ms=0).model_dump(mode="json", exclude={"duration_ms"})
    return {
        "run_id": ctx.run_id,
        "source": ctx.source.model_dump(mode="json"),
        "base_sha": base_sha,
        "head_sha": head,
        "power": ctx.detail.power.model_dump(mode="json") if ctx.detail.power else None,
        "functions": [f.model_dump(mode="json") for f in ctx.detail.functions],
        "totals": totals,
        "note": "the generated tests in netzero-tests/ ran against the original code; "
        "the repository's own test suite was not run",
    }


async def write_artifacts(
    ctx: RunContext, base_sha: str, merged: Sequence[Merged], tests: Sequence[Path]
) -> None:
    """``run.artifacts.*``: same payload shape as ``FakePipeline.artifacts``."""
    trunk, out, procs = ctx.paths.trunk, ctx.paths.out, ctx.procs
    run_id = ctx.run_id
    base = f"/api/runs/{run_id}/artifacts"
    async with ctx.step("run.artifacts.started") as st:
        out.mkdir(parents=True, exist_ok=True)
        head = (await git(["rev-parse", "HEAD"], cwd=trunk, procs=procs)).stdout.strip()
        patch_text, patch_ref = "", None
        if merged:
            patch_path = out / f"{run_id}.patch"
            # --output: the runner keeps only a tail of stdout
            diff = ["diff", "--no-color", "--binary", f"--output={patch_path}", base_sha, head]
            await git(diff, cwd=trunk, procs=procs)
            patch_text = patch_path.read_text(encoding="utf-8")
            names = await git(["diff", "--name-only", base_sha, head], cwd=trunk, procs=procs)
            files_changed = len([n for n in names.stdout.splitlines() if n.strip()])
            n, digest = _ref_bytes(patch_path)
            patch_ref = PatchRef(
                url=f"{base}/patch", bytes=n, sha256=digest, files_changed=files_changed
            )

        name = (ctx.source.url or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        prefix = (slugify(name) if name else "demo-repo") + "/"
        tmp_zip = ctx.paths.tmp / f"{run_id}.zip.part"
        tmp_zip.unlink(missing_ok=True)
        await git(
            ["archive", "--format=zip", f"--prefix={prefix}", "-o", str(tmp_zip), "HEAD"],
            cwd=trunk,
            procs=procs,
        )
        extra = [("netzero-report.json", json.dumps(report(ctx, base_sha, head), indent=2))]
        if merged:
            extra.insert(0, ("netzero.patch", patch_text))
        zip_path = out / f"{run_id}.zip"
        await asyncio.to_thread(_extend_zip, tmp_zip, zip_path, extra, tests)
        zbytes, zdigest = _ref_bytes(zip_path)
        ctx.log(
            "orchestrator",
            [
                f"patch: {patch_ref.files_changed} files changed ({len(merged)} functions)"
                if patch_ref
                else "patch: nothing merged",
                f"zip: {zip_path.name} ({zbytes} bytes, {len(tests)} generated test files)",
            ],
        )
        st.ok(
            patch=patch_ref,
            zip=ArtifactRef(url=f"{base}/zip", bytes=zbytes, sha256=zdigest),
            function_diffs=[
                FunctionDiffRef(
                    function_id=m.function_id,
                    url=f"{base}/functions/{quote(m.function_id, safe='')}/diff",
                )
                for m in merged
            ],
        )


__all__ = ["report", "write_artifacts"]
