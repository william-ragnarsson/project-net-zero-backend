"""The read side of ``/api/history``: read models, plus a few questions asked of the log itself."""

from __future__ import annotations

from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from netzero.api.schemas import (
    ActivityItem,
    ActivityPage,
    CandidateStat,
    FunctionDiff,
    FunctionHistory,
    FunctionPoint,
    HistoryRun,
    HistoryTable,
    ProjectDetail,
    ProjectSummary,
    RejectStat,
    StoredEvent,
    TrendPoint,
)
from netzero.events import CANDIDATE_IDS
from netzero.history.eventstore import stream_id
from netzero.history.projector import NAME as PROJECTOR
from netzero.pipeline.common import STRATEGY_HINTS

TABLES = ("events", "streams", "projects", "runs", "function_results", "projector_checkpoints")
TREND_RUNS = 24
ACTIVITY_TYPES = (
    "run.created",
    "run.selection.confirmed",
    "function.completed",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.interrupted",
)


# -- the store itself ------------------------------------------------------------------


def tables(conn: psycopg.Connection) -> list[HistoryTable]:
    out = []
    for name in TABLES:  # names from the constant above, never from a request
        (rows, size) = conn.execute(
            f"select (select count(*) from {name}), pg_total_relation_size(%s)", (name,)
        ).fetchone()  # type: ignore[misc]
        out.append(HistoryTable(name=name, rows=rows, bytes=size))
    return out


def positions(conn: psycopg.Connection) -> tuple[int, int]:
    """(last event stored, last event folded into the read models)."""
    return conn.execute(
        "select coalesce((select max(position) from events), 0),"
        " coalesce((select position from projector_checkpoints where name = %s), 0)",
        (PROJECTOR,),
    ).fetchone()  # type: ignore[return-value]


def recent_events(conn: psycopg.Connection, limit: int = 8) -> list[StoredEvent]:
    rows = conn.execute(
        "select position, stream_id, stream_seq, type, ts,"
        " (extract(epoch from recorded_at) * 1000)::bigint, octet_length(line)"
        " from events order by position desc limit %s",
        (limit,),
    )
    return [
        StoredEvent(position=p, stream_id=s, stream_seq=q, type=t, ts=ts, recorded_at=rec, bytes=n)
        for p, s, q, t, ts, rec, n in rows
    ]


# -- projects ----------------------------------------------------------------------------

_PROJECTS = """
select p.id, p.name, p.kind, p.url,
       count(r.run_id) as runs,
       count(r.run_id) filter (where r.state = 'completed') as runs_completed,
       max(r.created_ts) as last_run_ts,
       (array_agg(r.state order by r.created_ts desc))[1] as last_state,
       (array_agg(r.g_saved_per_1m_calls order by r.created_ts desc)
          filter (where r.state = 'completed'))[1] as latest_g_saved_per_1m_calls,
       max(r.g_saved_per_1m_calls) filter (where r.state = 'completed') as best_g_saved_per_1m_calls,
       coalesce(f.tracked, 0) as functions_tracked,
       coalesce(f.improved, 0) as functions_improved
from projects p
left join runs r on r.project_id = p.id
left join (
  select project_id,
         count(distinct function_id) filter (where outcome is not null) as tracked,
         count(distinct function_id) filter (where outcome = 'accepted') as improved
  from function_results group by project_id
) f on f.project_id = p.id
where %(project)s::text is null or p.id = %(project)s
group by p.id, f.tracked, f.improved
order by max(r.created_ts) desc nulls last, p.name
"""

_TREND = """
select project_id, run_id, created_ts, state, g_saved_per_1m_calls,
       coalesce((counts_by_outcome ->> 'accepted')::int, 0) as accepted
from (
  select *, row_number() over (partition by project_id order by created_ts desc) as n
  from runs where %(project)s::text is null or project_id = %(project)s
) recent
where n <= %(n)s
order by project_id, created_ts
"""


def projects(conn: psycopg.Connection, project_id: str | None = None) -> list[ProjectSummary]:
    args = {"project": project_id, "n": TREND_RUNS}
    trend: dict[str, list[TrendPoint]] = defaultdict(list)
    with conn.cursor(row_factory=dict_row) as cur:
        for row in cur.execute(_TREND, args):
            trend[row.pop("project_id")].append(TrendPoint(**row))
        return [
            ProjectSummary(**row, trend=trend[row["id"]]) for row in cur.execute(_PROJECTS, args)
        ]


_RUN_COLUMNS = (
    "run_id, created_ts, ended_ts, state, mode, ref, base_sha, functions_total, functions_done,"
    " counts_by_outcome, mean_reduction_pct, g_saved_per_1m_calls, kwh_saved_per_1m_calls,"
    " llm_cost_usd, error, last_seq as events"
)


def project_detail(conn: psycopg.Connection, project_id: str) -> ProjectDetail | None:
    found = projects(conn, project_id)
    if not found:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        runs = [
            HistoryRun(**row)
            for row in cur.execute(
                f"select {_RUN_COLUMNS} from runs where project_id = %s order by created_ts desc",
                (project_id,),
            )
        ]
        points = cur.execute(
            "select fr.function_id, fr.qualname, fr.module, fr.file, r.run_id, r.created_ts,"
            " r.base_sha, fr.outcome, fr.winner, fr.delta_pct, fr.ci_lo, fr.ci_hi,"
            " fr.g_saved_per_1m_calls, fr.reason"
            " from function_results fr join runs r using (run_id)"
            " where fr.project_id = %s order by fr.function_id, r.created_ts",
            (project_id,),
        ).fetchall()
    return ProjectDetail(
        project=found[0],
        runs=runs,
        functions=_functions(points),
        candidates=candidate_stats(conn, project_id),
        rejections=rejections(conn, project_id),
    )


def _functions(rows: list[dict]) -> list[FunctionHistory]:
    by_fn: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_fn[row["function_id"]].append(row)
    out = []
    for fid, rs in by_fn.items():
        last = rs[-1]
        accepted = [
            r["delta_pct"] for r in rs if r["outcome"] == "accepted" and r["delta_pct"] is not None
        ]
        out.append(
            FunctionHistory(
                function_id=fid,
                qualname=last["qualname"],
                module=last["module"],
                file=last["file"],
                runs=len(rs),
                accepted=len(accepted),
                best_delta_pct=min(accepted) if accepted else None,
                points=[FunctionPoint(**{k: r[k] for k in FunctionPoint.model_fields}) for r in rs],
            )
        )
    # improved most often first, then the biggest win, then by name
    out.sort(key=lambda f: (-f.accepted, f.best_delta_pct or 0.0, f.qualname))
    return out


# -- straight off the log: what the read models don't keep --------------------------------


def _project_events(also: str = "") -> str:
    """``from ... where`` for one project's events of one type (``%(project)s``, ``%(type)s``)."""
    return (
        " from events e join runs r on e.stream_id = 'run:' || r.run_id"
        + also
        + " where r.project_id = %(project)s and e.type = %(type)s"
    )


def candidate_stats(conn: psycopg.Connection, project_id: str) -> list[CandidateStat]:
    """How each rewrite strategy fares, from every ``function.decision`` ranking."""
    ranked = {
        cid: (proposed, eligible, significant)
        for cid, proposed, eligible, significant in conn.execute(
            "select entry ->> 'candidate_id', count(*),"
            " count(*) filter (where entry ->> 'status' = 'eligible'),"
            " count(*) filter (where (entry ->> 'significant')::boolean)"
            + _project_events(" cross join lateral jsonb_array_elements(e.data -> 'ranking') entry")
            + " group by 1",
            {"project": project_id, "type": "function.decision"},
        )
    }
    accepted = dict(
        conn.execute(
            "select winner, count(*) from function_results"
            " where project_id = %s and outcome = 'accepted' and winner is not null group by 1",
            (project_id,),
        ).fetchall()
    )
    out = []
    for cid in CANDIDATE_IDS:
        proposed, eligible, significant = ranked.get(cid, (0, 0, 0))
        out.append(
            CandidateStat(
                candidate_id=cid,
                hint=STRATEGY_HINTS[cid],
                proposed=proposed,
                eligible=eligible,
                significant=significant,
                accepted=accepted.get(cid, 0),
            )
        )
    return out


def rejections(conn: psycopg.Connection, project_id: str) -> list[RejectStat]:
    """Why candidates were thrown out, from every ``candidate.rejected``."""
    rows = conn.execute(
        "select e.data ->> 'reason', count(*)"
        + _project_events()
        + " group by 1 order by 2 desc, 1",
        {"project": project_id, "type": "candidate.rejected"},
    )
    return [RejectStat(reason=reason, count=n) for reason, n in rows]


def activity(
    conn: psycopg.Connection,
    *,
    project_id: str | None = None,
    before: tuple[int, int] | None = None,
    limit: int = 30,
) -> ActivityPage:
    """Notable events across every run, newest first, keyset-paginated on ``(ts, position)``."""
    rows = conn.execute(
        """
        select e.position, e.stream_seq, e.ts, e.type, r.run_id, p.id, p.name, e.function_id, e.data
        from events e
        join runs r on e.stream_id = 'run:' || r.run_id
        join projects p on p.id = r.project_id
        where e.type = any(%(types)s)
          and (%(project)s::text is null or r.project_id = %(project)s)
          and (%(ts)s::bigint is null or (e.ts, e.position) < (%(ts)s, %(pos)s))
        order by e.ts desc, e.position desc
        limit %(limit)s
        """,
        {
            "types": list(ACTIVITY_TYPES),
            "project": project_id,
            "ts": before[0] if before else None,
            "pos": before[1] if before else None,
            "limit": limit + 1,
        },
    ).fetchall()
    items = [_activity_item(*row) for row in rows[:limit]]
    more = len(rows) > limit
    return ActivityPage(
        items=items, next=f"{items[-1].ts}:{items[-1].position}" if more and items else None
    )


def _activity_item(position, seq, ts, type_, run_id, project_id, project_name, function_id, data):
    item = ActivityItem(
        position=position,
        seq=seq,
        ts=ts,
        type=type_,
        run_id=run_id,
        project_id=project_id,
        project_name=project_name,
        function_id=function_id,
    )
    if type_ == "run.selection.confirmed":
        item.functions = len(data["function_ids"])
    elif type_ == "function.completed":
        item.outcome = data["outcome"]
        item.winner = data["winner"]
        item.delta_pct = data["delta_pct"]
        item.g_saved_per_1m_calls = data["g_saved_per_1m_calls"]
        item.message = data["reason"] or None
    elif type_ == "run.completed":
        s = data["summary"]
        item.functions = s["functions_done"]
        item.accepted = s["counts_by_outcome"].get("accepted", 0)
        item.delta_pct = s["mean_reduction_pct"]
        item.g_saved_per_1m_calls = s["g_saved_per_1m_calls"]
    elif type_ == "run.failed":
        item.message = data["error"]["message"]
    elif type_ == "run.cancelled":
        item.message = f"cancelled while {data['at_state']}"
    elif type_ == "run.interrupted":
        item.message = f"interrupted while {data['previous_state']}"
    return item


def parse_cursor(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    ts, _, pos = value.partition(":")
    try:
        return int(ts), int(pos)
    except ValueError:
        raise ValueError(f"bad cursor {value!r}") from None


# -- one run -----------------------------------------------------------------------------


def run_lines(conn: psycopg.Connection, run_id: str) -> list[str]:
    rows = conn.execute(
        "select line from events where stream_id = %s order by stream_seq", (stream_id(run_id),)
    )
    return [row[0] for row in rows]


def function_diff(conn: psycopg.Connection, run_id: str, function_id: str) -> FunctionDiff | None:
    """The diff a run's ``function.completed`` carried for one function."""
    row = conn.execute(
        "select data ->> 'outcome', data ->> 'diff' from events"
        " where stream_id = %s and type = 'function.completed' and function_id = %s"
        " order by stream_seq desc limit 1",
        (stream_id(run_id), function_id),
    ).fetchone()
    if row is None:
        return None
    return FunctionDiff(run_id=run_id, function_id=function_id, outcome=row[0], diff=row[1])
