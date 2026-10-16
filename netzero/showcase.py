"""``netzero showcase``: finished runs as static files, for a web build with no server.

The showcase build of the UI (``VITE_SHOWCASE=1``, see ``web/src/showcase/config.ts``) reads
these files where the normal build calls ``/api``:

- ``runs.json`` answers ``GET /api/runs`` (newest first)
- ``runs/<id>/run.json`` and ``runs/<id>/events.jsonl`` answer the two per-run reads
- ``health.json`` answers ``GET /api/health``
- ``showcase.json`` is the showcase's own: ``samples`` lists the runs that are made up, and
  ``artifacts`` maps each artifact URL in the logs to its static copy. The UI hides a
  download whose URL is not in it. The zip is never copied: it holds the whole repository.

Sample runs (``SAMPLES``) are the FakePipeline on repositories that don't exist, so the
pages have something to show before real runs are recorded. The UI labels them as samples.

The folder is rebuilt from scratch each time, so the arguments list the whole showcase.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from netzero import __version__
from netzero.api.schemas import CreateRunRequest, Health
from netzero.events import RunDetail, parse_event
from netzero.pipeline.fake import FakePipeline, Scenario, run_fake
from netzero.pipeline.grammar import check_grammar
from netzero.pipeline.projection import project
from netzero.pipeline.store import RUN_ID_RE, RunPaths, fn_slug, read_complete_lines, to_summary

URL_BASE = "/showcase"
MARKER = "runs.json"  # a folder holding this is ours to replace

SAMPLE_OWNER = "net-zero-samples"


@dataclass(frozen=True)
class Sample:
    """A made-up run: the FakePipeline on ``github.com/net-zero-samples/<repo>``, which doesn't exist."""

    repo: str
    at: str  # when it starts, ISO 8601
    functions: tuple[str, ...] = ()  # the scenario's own selection when empty
    scenario: Scenario = "demo"
    seed: int = 0


# Each function that improves appears in one repository only: the fake gives a function the
# same numbers in every run, and a dashboard listing it twice would show that.
SAMPLES: tuple[Sample, ...] = (
    Sample(
        "invoice-renderer",
        "2026-09-08T09:40:00+02:00",
        (
            "textkit.dedupe:dedupe_preserve_order",
            "textkit.fields:join_fields",
            "textkit.normalize:normalize_whitespace",
        ),
        seed=11,
    ),
    Sample(
        "geo-tiles",
        "2026-09-15T14:05:00+02:00",
        (
            "algos.graph:Graph.shortest_path_lengths",
            "datakit.rank:top_k_inplace",
            "datakit.series:moving_average",
        ),
        seed=12,
    ),
    Sample(
        "search-indexer",
        "2026-09-23T10:20:00+02:00",
        (
            "algos.strings:levenshtein",
            "textkit.freq:word_frequencies",
            "datakit.stats:zscore",
            "textkit.normalize:normalize_whitespace",
        ),
        seed=13,
    ),
    Sample("ledger-api", "2026-09-30T16:45:00+02:00", scenario="fail_env", seed=14),
    Sample(
        "ledger-api",
        "2026-10-01T11:10:00+02:00",
        (
            "datakit.aggregate:total_by_category",
            "algos.search:index_of_sorted",
            "datakit.io_free:parse_records",
            "datakit.rates:fetch_exchange_rates",
        ),
        seed=15,
    ),
    Sample(
        "metrics-agent",
        "2026-10-07T08:55:00+02:00",
        ("algos.primes:primes_below", "datakit.stats:zscore", "algos.walk:random_walk"),
        seed=16,
    ),
    Sample(
        "etl-nightly",
        "2026-10-13T13:30:00+02:00",
        (
            "algos.pairs:has_pair_with_sum",
            "textkit.fields:join_fields",
            "datakit.rank:top_k_inplace",
            "algos.search:index_of_sorted",
            "datakit.series:moving_average",
        ),
        seed=17,
    ),
)


class ShowcaseError(Exception):
    """An input that can't go in the showcase, or an --out that isn't one."""


@dataclass
class Bundled:
    id: str
    events: int
    sample: bool = False
    artifacts: int = 0  # files copied
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Run:
    events: Path
    text: str
    n: int
    detail: RunDetail


def make_samples(runs_dir: Path) -> list[Path]:
    """Run every sample into ``runs_dir`` in virtual time, a second or so each; returns the logs."""
    from netzero.pipeline.vclock import VirtualTimeLoop

    async def run_all() -> list[Path]:
        return [
            await run_fake(
                runs_dir,
                FakePipeline(scenario=s.scenario, seed=s.seed, selection=s.functions or None),
                CreateRunRequest(github_url=f"https://github.com/{SAMPLE_OWNER}/{s.repo}"),
                epoch_ms=int(datetime.fromisoformat(s.at).timestamp() * 1000),
            )
            for s in SAMPLES
        ]

    with asyncio.Runner(loop_factory=VirtualTimeLoop) as runner:
        return runner.run(run_all())


def build(inputs: Sequence[Path], out: Path, *, samples: Sequence[Path] = ()) -> list[Bundled]:
    """Check every input, then replace ``out`` with the bundle. Nothing is written on error.

    ``samples`` are bundled like ``inputs`` and listed as made up.
    """
    runs = [_load(_events_file(p)) for p in [*inputs, *samples]]
    seen: set[str] = set()
    for r in runs:
        if r.detail.id in seen:
            raise ShowcaseError(f"run {r.detail.id} is given twice")
        seen.add(r.detail.id)
    _reset(out)

    bundled = [_write(r, out) for r in runs]
    for b in bundled[len(inputs) :]:
        b.sample = True
    artifacts: dict[str, str] = {}
    for r, b in zip(runs, bundled, strict=True):
        found = _copy_artifacts(r, out)
        b.artifacts = len(found)
        artifacts.update(found)

    newest_first = sorted(runs, key=lambda r: r.detail.created_ts, reverse=True)
    summaries = [to_summary(r.detail).model_dump(mode="json") for r in newest_first]
    _write_json(out / MARKER, summaries)
    _write_json(out / "health.json", Health(ok=True, version=__version__).model_dump(mode="json"))
    sample_ids = {b.id for b in bundled if b.sample}
    meta = {
        "samples": [r.detail.id for r in newest_first if r.detail.id in sample_ids],
        "artifacts": artifacts,
    }
    _write_json(out / "showcase.json", meta)
    return bundled


def _events_file(path: Path) -> Path:
    events = path / "events.jsonl" if path.is_dir() else path
    if not events.is_file():
        raise ShowcaseError(f"{path}: no events.jsonl")
    return events


def _load(events: Path) -> _Run:
    lines, _ = read_complete_lines(events)
    if not lines:
        raise ShowcaseError(f"{events}: no events")
    if problems := check_grammar(lines):
        raise ShowcaseError(f"{events}: {problems[0]} (only finished, valid runs can be shown)")
    run_id = parse_event(lines[0]).run_id
    if not RUN_ID_RE.fullmatch(run_id):
        raise ShowcaseError(f"{events}: {run_id!r} is not a run id")
    text = "\n".join(lines) + "\n"
    if "sk-ant-" in text:
        raise ShowcaseError(f"{events}: contains what looks like an Anthropic API key")
    return _Run(events=events, text=text, n=len(lines), detail=project(lines, run_id))


def _reset(out: Path) -> None:
    if out.exists():
        if not out.is_dir() or (any(out.iterdir()) and not (out / MARKER).is_file()):
            raise ShowcaseError(f"{out} exists and is not a showcase folder; pick another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)


def _write(r: _Run, out: Path) -> Bundled:
    root = out / "runs" / r.detail.id
    root.mkdir(parents=True)
    (root / "events.jsonl").write_text(r.text, "utf-8")
    (root / "run.json").write_text(r.detail.model_dump_json(indent=1), "utf-8")
    b = Bundled(id=r.detail.id, events=r.n)
    home = str(Path.home())
    if home != "/" and (count := r.text.count(home)):
        b.warnings.append(f"the log mentions {home} {count} times, and the showcase is public")
    return b


def _copy_artifacts(r: _Run, out: Path) -> dict[str, str]:
    """Copy the patch and per-function diffs that exist next to the log; returns url -> path."""
    a = r.detail.artifacts
    if a is None:
        return {}
    run_id = r.detail.id
    src = RunPaths(r.events.parent)
    wanted: list[tuple[str, Path, str]] = []  # (api url, source, name under runs/<id>/)
    if a.patch is not None:
        wanted.append((a.patch.url, src.out / f"{run_id}.patch", f"netzero-{run_id}.patch"))
    for d in a.function_diffs:
        wanted.append(
            (d.url, src.fn(d.function_id) / "merge.diff", f"fn/{fn_slug(d.function_id)}.diff")
        )
    found: dict[str, str] = {}
    for url, source, name in wanted:
        if not source.is_file():
            continue
        dest = out / "runs" / run_id / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        found[url] = f"{URL_BASE}/runs/{run_id}/{name}"
    return found


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", "utf-8")
