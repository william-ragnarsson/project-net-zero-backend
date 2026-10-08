"""Fold stored events into the read models: ``projects``, ``runs``, ``function_results``.

The fold is the same ``Projection`` that writes ``run.json``, plus a few
fields history needs (the commit measured, when it ended, where each
function lives). A run touched since the checkpoint is refolded from its
whole stream and its rows replaced, in the transaction that moves the
checkpoint, so each event takes effect exactly once and a refold is always
safe. ``rebuild`` throws the read models away and folds everything again.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from netzero.events import TERMINAL_EVENT_TYPES, EventBase, FunctionInfo, RunSource
from netzero.pipeline.clone import InvalidUrl, parse_github_url
from netzero.pipeline.projection import Projection, rebuild
from netzero.pipeline.store import slugify

logger = logging.getLogger("netzero.history")

NAME = "history"


@dataclass(frozen=True)
class Project:
    id: str
    key: str
    kind: str
    name: str
    url: str | None


def project_of(source: RunSource) -> Project:
    """Runs of one repository share a project, whatever ref they measured."""
    if source.kind == "github" and source.url:
        try:
            repo = parse_github_url(source.url)
        except InvalidUrl:
            key = name = source.url
            url = source.url
        else:
            name = f"{repo.owner}/{repo.repo}"
            key, url = f"github.com/{name}".lower(), repo.url
    else:
        key, name, url = "demo", "demo", None
    digest = hashlib.sha1(key.encode()).hexdigest()[:6]
    return Project(f"{slugify(name, 40)}-{digest}", key, source.kind, name, url)


@dataclass
class _Done:
    kwh: float | None
    duration_ms: int
    ts: int


class HistoryFold(Projection):
    """``Projection`` plus what the history tables keep beyond ``run.json``."""

    def __init__(self, detail):
        super().__init__(detail)
        self.base_sha: str | None = None
        self.ended_ts: int | None = None
        self.info: dict[str, FunctionInfo] = {}
        self.done: dict[str, _Done] = {}

    def apply(self, ev: EventBase) -> None:
        super().apply(ev)
        t = ev.type  # type: ignore[attr-defined]
        data = ev.data  # type: ignore[attr-defined]
        if t == "run.clone.completed" and data.commit_sha:
            self.base_sha = data.commit_sha
        elif t == "function.started":
            self.info[data.info.function_id] = data.info
        elif t == "function.completed" and ev.function_id:
            self.done[ev.function_id] = _Done(data.kwh_saved_per_1m_calls, data.duration_ms, ev.ts)
        elif t in TERMINAL_EVENT_TYPES:
            self.ended_ts = ev.ts


def fold(lines: list[str], run_id: str) -> HistoryFold:
    return rebuild(lines, run_id, HistoryFold)


def write(conn: psycopg.Connection, f: HistoryFold) -> None:
    d = f.detail
    p = project_of(d.source)
    conn.execute(
        "insert into projects (id, key, kind, name, url) values (%s, %s, %s, %s, %s)"
        " on conflict (id) do update set name = excluded.name, url = excluded.url",
        (p.id, p.key, p.kind, p.name, p.url),
    )
    totals = f.totals(duration_ms=0)
    conn.execute(
        """
        insert into runs (run_id, project_id, state, mode, ref, base_sha, created_ts, updated_ts,
                          ended_ts, last_seq, functions_total, functions_done, counts_by_outcome,
                          mean_reduction_pct, g_saved_per_1m_calls, kwh_saved_per_1m_calls,
                          llm_cost_usd, error)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id) do update set
          state = excluded.state, ref = excluded.ref, base_sha = excluded.base_sha,
          updated_ts = excluded.updated_ts, ended_ts = excluded.ended_ts,
          last_seq = excluded.last_seq, functions_total = excluded.functions_total,
          functions_done = excluded.functions_done, counts_by_outcome = excluded.counts_by_outcome,
          mean_reduction_pct = excluded.mean_reduction_pct,
          g_saved_per_1m_calls = excluded.g_saved_per_1m_calls,
          kwh_saved_per_1m_calls = excluded.kwh_saved_per_1m_calls,
          llm_cost_usd = excluded.llm_cost_usd, error = excluded.error
        """,
        (
            d.id, p.id, d.state, d.mode, d.source.ref, f.base_sha, d.created_ts, d.updated_ts,
            f.ended_ts, d.last_seq, d.functions_total, d.functions_done,
            Jsonb(d.counts_by_outcome), d.mean_reduction_pct, d.g_saved_per_1m_calls,
            totals.kwh_saved_per_1m_calls, d.llm_cost_usd, d.error.message if d.error else None,
        ),
    )  # fmt: skip
    conn.execute("delete from function_results where run_id = %s", (d.id,))
    triage = {item.function_id: item for item in d.triage}
    rows = []
    for ord_, rec in enumerate(d.functions):
        where = f.info.get(rec.function_id) or triage.get(rec.function_id)
        done = f.done.get(rec.function_id)
        ci = rec.delta_ci_pct
        rows.append((
            d.id, rec.function_id, p.id, ord_, rec.qualname,
            where.module if where else None, where.file if where else None,
            rec.outcome, rec.winner, rec.delta_pct, ci.lo if ci else None, ci.hi if ci else None,
            rec.g_saved_per_1m_calls, done.kwh if done else None, rec.reason,
            done.duration_ms if done else None, done.ts if done else None,
        ))  # fmt: skip
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                "insert into function_results (run_id, function_id, project_id, ord, qualname,"
                " module, file, outcome, winner, delta_pct, ci_lo, ci_hi, g_saved_per_1m_calls,"
                " kwh_saved_per_1m_calls, reason, duration_ms, completed_ts)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )


@dataclass
class ProjectResult:
    runs: int = 0
    skipped: int = 0
    position: int = 0


def project_pending(conn: psycopg.Connection) -> ProjectResult:
    """Fold every run with events past the checkpoint; returns what it did."""
    result = ProjectResult()
    with conn.transaction():
        conn.execute(
            "insert into projector_checkpoints (name) values (%s) on conflict do nothing", (NAME,)
        )
        (checkpoint,) = conn.execute(
            "select position from projector_checkpoints where name = %s for update", (NAME,)
        ).fetchone()  # type: ignore[misc]
        touched = conn.execute(
            "select stream_id, max(position) from events where position > %s"
            " group by stream_id order by 2",
            (checkpoint,),
        ).fetchall()
        result.position = checkpoint
        for sid, _ in touched:
            run_id = sid.removeprefix("run:")
            lines = [
                row[0]
                for row in conn.execute(
                    "select line from events where stream_id = %s order by stream_seq", (sid,)
                )
            ]
            try:
                f = fold(lines, run_id)
            except (ValueError, ValidationError) as exc:
                result.skipped += 1
                logger.warning("history: can't fold %s: %s", run_id, str(exc).splitlines()[0])
                continue
            write(conn, f)
            result.runs += 1
        if touched:
            # the highest position read, not max(position) now: rows appended
            # since are picked up next time
            result.position = max(pos for _, pos in touched)
            conn.execute(
                "update projector_checkpoints set position = %s, updated_at = now()"
                " where name = %s",
                (result.position, NAME),
            )
    return result


def rebuild_all(conn: psycopg.Connection) -> ProjectResult:
    """Drop the read models and fold every stream again, in one transaction."""
    with conn.transaction():
        conn.execute("truncate function_results, runs, projects")
        conn.execute("delete from projector_checkpoints where name = %s", (NAME,))
        return project_pending(conn)
