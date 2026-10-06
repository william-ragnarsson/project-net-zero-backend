"""Fold events into a ``RunDetail`` (what ``run.json`` and ``GET /api/runs/{id}`` serve).

``run.json`` is always derivable from ``events.jsonl``: ``project(lines)``
rebuilds it from scratch. The frontend reducer mirrors these rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from netzero.events import (
    EventBase,
    FunctionRecord,
    RunDetail,
    RunSource,
    RunTotals,
    parse_event,
)


class Projection:
    def __init__(self, detail: RunDetail):
        self.detail = detail
        self._kwh_saved: dict[str, float] = {}  # accepted function -> kWh saved per 1M calls

    @classmethod
    def empty(cls, run_id: str, *, created_ts: int, source: RunSource, mode: str) -> Projection:
        return cls(
            RunDetail(
                id=run_id,
                created_ts=created_ts,
                updated_ts=created_ts,
                state="created",
                source=source,
                mode=mode,  # type: ignore[arg-type]
            )
        )

    def apply(self, ev: EventBase) -> None:
        d = self.detail
        d.last_seq = ev.seq
        d.updated_ts = ev.ts
        t = ev.type  # type: ignore[attr-defined]
        data = ev.data  # type: ignore[attr-defined]

        if t == "run.created":
            d.created_ts = ev.ts
            d.source = data.source
            d.mode = data.mode
            d.models = dict(data.models)
            d.settings = data.settings
            d.assumptions = data.assumptions
        elif t == "run.state_changed":
            d.state = data.to_state
        elif t == "run.power.detected":
            d.power = data.power
        elif t == "run.power.calibration.completed":
            if data.ok and data.p_core_w is not None and d.power is not None:
                d.power = d.power.model_copy(update={"p_core_w": data.p_core_w})
        elif t == "run.discovery.completed":
            if data.ok and not d.triage:
                d.triage = list(data.heuristic_ranked)
        elif t == "run.triage.completed":
            if data.ok:
                d.triage = list(data.items)
        elif t == "run.selection.confirmed":
            d.selection = list(data.function_ids)
            by_id = {item.function_id: item for item in d.triage}
            d.functions = [
                FunctionRecord(
                    function_id=fid,
                    qualname=by_id[fid].qualname if fid in by_id else fid.split(":")[-1],
                )
                for fid in data.function_ids
            ]
            d.functions_total = len(d.functions)
        elif t == "function.completed":
            self._function_completed(ev.function_id or "", data)
        elif t == "run.artifacts.completed":
            d.artifacts = data
        elif t == "llm.usage":
            d.llm_cost_usd = data.run_cost_usd
        elif t == "run.failed":
            d.error = data.error
        elif t == "run.completed":
            s = data.summary
            d.functions_total = s.functions_total
            d.functions_done = s.functions_done
            d.counts_by_outcome = dict(s.counts_by_outcome)
            d.mean_reduction_pct = s.mean_reduction_pct
            d.g_saved_per_1m_calls = s.g_saved_per_1m_calls
            d.llm_cost_usd = s.llm_cost_usd

    def _function_completed(self, function_id: str, data) -> None:
        d = self.detail
        rec = next((f for f in d.functions if f.function_id == function_id), None)
        if rec is None:
            rec = FunctionRecord(function_id=function_id, qualname=function_id.split(":")[-1])
            d.functions.append(rec)
            d.functions_total = max(d.functions_total, len(d.functions))
        first = rec.outcome is None
        rec.outcome = data.outcome
        rec.winner = data.winner
        rec.delta_pct = data.delta_pct
        rec.delta_ci_pct = data.delta_ci_pct
        rec.g_saved_per_1m_calls = data.g_saved_per_1m_calls
        rec.reason = data.reason
        if data.outcome == "accepted" and data.kwh_saved_per_1m_calls is not None:
            self._kwh_saved[function_id] = data.kwh_saved_per_1m_calls
        else:
            self._kwh_saved.pop(function_id, None)
        if first:
            d.functions_done += 1
        self._recount()

    def _recount(self) -> None:
        d = self.detail
        counts: dict[str, int] = {}
        for f in d.functions:
            if f.outcome is not None:
                counts[f.outcome] = counts.get(f.outcome, 0) + 1
        d.counts_by_outcome = counts  # type: ignore[assignment]
        accepted = [f for f in d.functions if f.outcome == "accepted" and f.delta_pct is not None]
        d.mean_reduction_pct = (
            sum(f.delta_pct for f in accepted) / len(accepted) if accepted else None  # type: ignore[misc]
        )
        d.g_saved_per_1m_calls = sum(f.g_saved_per_1m_calls or 0.0 for f in accepted)

    def totals(self, *, duration_ms: int) -> RunTotals:
        d = self.detail
        return RunTotals(
            functions_total=d.functions_total,
            functions_done=d.functions_done,
            counts_by_outcome=dict(d.counts_by_outcome),
            mean_reduction_pct=d.mean_reduction_pct,
            g_saved_per_1m_calls=d.g_saved_per_1m_calls,
            kwh_saved_per_1m_calls=sum(self._kwh_saved.values()),
            llm_cost_usd=d.llm_cost_usd,
            duration_ms=duration_ms,
        )


def project(lines: Iterable[str], run_id: str) -> RunDetail:
    """Rebuild a ``RunDetail`` from persisted event lines."""
    return rebuild(lines, run_id).detail


def rebuild(lines: Iterable[str], run_id: str) -> Projection:
    """Rebuild the full ``Projection`` (detail + accumulators) from persisted lines."""
    proj: Projection | None = None
    for line in lines:
        ev = parse_event(line)
        if proj is None:
            if ev.type != "run.created":  # type: ignore[union-attr]
                raise ValueError("events.jsonl must start with run.created")
            proj = Projection.empty(
                run_id,
                created_ts=ev.ts,
                source=ev.data.source,
                mode=ev.data.mode,  # type: ignore[union-attr]
            )
        proj.apply(ev)  # type: ignore[arg-type]
    if proj is None:
        raise ValueError("no events")
    return proj
