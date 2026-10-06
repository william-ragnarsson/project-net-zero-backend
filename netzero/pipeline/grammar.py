"""Check an ``events.jsonl`` against the event grammar.

Used by the tests (every fake, recorded and replayed run must pass) and by
``netzero replay --check``. The rules mirror what the frontend reducer relies on.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from netzero.events import (
    STARTED_TO_COMPLETED,
    TERMINAL_EVENT_TYPES,
    EventBase,
    parse_event,
)
from netzero.pipeline.run import can_transition

COMPLETED_TO_STARTED = {v: k for k, v in STARTED_TO_COMPLETED.items()}
# function.decision outcome -> the function.completed outcomes it allows
DECISION_TO_OUTCOMES = {
    "winner": {"accepted", "reverted", "failed", "cancelled"},
    "no_significant_win": {"no_significant_win", "failed", "cancelled"},
    "all_rejected": {"all_rejected", "failed", "cancelled"},
}
TERMINAL_TO_STATE = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.interrupted": "interrupted",
}


def check_grammar(lines: Iterable[str], *, complete: bool = True) -> list[str]:
    """Violations of the grammar, as human-readable strings (empty == valid).

    ``complete`` requires the log to end with a terminal event.
    """
    problems: list[str] = []

    def bad(ev: EventBase | None, msg: str) -> None:
        where = f"seq {ev.seq} {ev.type}: " if ev is not None else ""  # type: ignore[attr-defined]
        problems.append(where + msg)

    events: list[EventBase] = []
    for i, line in enumerate(lines, 1):
        try:
            events.append(parse_event(line))  # type: ignore[arg-type]
        except (ValidationError, ValueError) as exc:
            problems.append(f"line {i}: does not parse: {str(exc).splitlines()[0]}")
            return problems
    if not events:
        return ["no events"]

    first = events[0]
    if first.type != "run.created":  # type: ignore[attr-defined]
        bad(first, "first event must be run.created")
    run_id = first.run_id

    state = "created"
    open_steps: dict[tuple, EventBase] = {}
    open_function: str | None = None
    decided: dict[str, str] = {}  # function_id -> decision outcome
    done_functions: set[str] = set()
    selection: list[str] | None = None
    terminal_at: int | None = None
    prev_ts = first.ts

    for idx, ev in enumerate(events):
        t: str = ev.type  # type: ignore[attr-defined]
        fid, cid, attempt = ev.function_id, ev.candidate_id, ev.attempt

        if ev.seq != idx + 1:
            bad(ev, f"seq gap: expected {idx + 1}")
        if ev.run_id != run_id:
            bad(ev, f"run_id {ev.run_id!r} != {run_id!r}")
        if ev.ts < prev_ts:
            bad(ev, f"ts went backwards ({ev.ts} < {prev_ts})")
        prev_ts = max(prev_ts, ev.ts)
        if terminal_at is not None:
            bad(ev, f"event after the terminal event (seq {terminal_at})")

        # -- scope ---------------------------------------------------------------
        if t.startswith(("function.", "candidate.")) and fid is None:
            bad(ev, "function-scoped event without function_id")
        if t.startswith("candidate.") and cid is None:
            bad(ev, "candidate event without candidate_id")
        if t.startswith("run.") and (fid is not None or cid is not None):
            bad(ev, "run event with a function/candidate scope")
        if cid is not None and fid is None:
            bad(ev, "candidate_id without function_id")
        if t == "run.created" and idx > 0:
            bad(ev, "run.created again")
        if fid is not None and t != "function.started":
            if fid != open_function:
                bad(ev, f"{fid} is not the open function ({open_function})")
        if t.startswith("candidate.") and fid in decided:
            bad(ev, "candidate event after function.decision")

        # -- steps ---------------------------------------------------------------
        key = (fid, cid, attempt)
        if t in STARTED_TO_COMPLETED:
            if (t, *key) in open_steps:
                bad(ev, f"{t} started twice for {key} without completing")
            open_steps[(t, *key)] = ev
        elif t in COMPLETED_TO_STARTED:
            started = COMPLETED_TO_STARTED[t]
            if open_steps.pop((started, *key), None) is None:
                bad(ev, f"{t} without an open {started} for {key}")

        # -- run state + functions -------------------------------------------------
        if t == "run.state_changed":
            frm, to = ev.data.from_state, ev.data.to_state  # type: ignore[attr-defined]
            if frm != state:
                bad(ev, f"from_state {frm} but the run is {state}")
            if not can_transition(state, to):
                bad(ev, f"invalid transition {state} -> {to}")
            state = to
        elif t == "run.selection.confirmed":
            if selection is not None:
                bad(ev, "selection confirmed twice")
            if state != "awaiting_selection":
                bad(ev, f"selection confirmed in state {state}")
            selection = list(ev.data.function_ids)  # type: ignore[attr-defined]
        elif t == "function.started":
            if state != "optimizing":
                bad(ev, f"function started in state {state}")
            if open_function is not None:
                bad(ev, f"function started while {open_function} is still open")
            if selection is not None and fid not in selection:
                bad(ev, f"{fid} is not in the selection")
            if fid in done_functions:
                bad(ev, f"{fid} started twice")
            open_function = fid
        elif t == "function.decision":
            if fid in decided:
                bad(ev, "decided twice")
            decided[fid] = ev.data.outcome  # type: ignore[attr-defined,index]
        elif t == "function.merge.started":
            if decided.get(fid) != "winner":  # type: ignore[arg-type]
                bad(ev, "merge without a winning decision")
        elif t == "function.completed":
            leftover = [k for k in open_steps if k[1] == fid]
            if leftover:
                bad(ev, f"function completed with open steps {leftover}")
            outcome = ev.data.outcome  # type: ignore[attr-defined]
            decision = decided.get(fid)  # type: ignore[arg-type]
            if decision is not None and outcome not in DECISION_TO_OUTCOMES[decision]:
                bad(ev, f"outcome {outcome} contradicts decision {decision}")
            if decision is None and outcome in ("accepted", "reverted"):
                bad(ev, f"outcome {outcome} without a decision")
            done_functions.add(fid)  # type: ignore[arg-type]
            open_function = None
        elif t in TERMINAL_EVENT_TYPES:
            terminal_at = ev.seq
            want = TERMINAL_TO_STATE[t]
            if state != want:
                bad(ev, f"terminal {t} but the run is {state}")
            prev = events[idx - 1] if idx else None
            if prev is None or prev.type != "run.state_changed":  # type: ignore[attr-defined]
                bad(ev, "terminal event must directly follow run.state_changed")
            if open_steps:
                bad(ev, f"terminal event with open steps {sorted(map(str, open_steps))}")
            if open_function is not None:
                bad(ev, f"terminal event with open function {open_function}")

    if complete and terminal_at is None:
        problems.append("log does not end with a terminal event")
        if open_steps:
            problems.append(f"open steps at the end: {sorted(map(str, open_steps))}")
    return problems


def assert_grammar(lines: Iterable[str], *, complete: bool = True) -> None:
    problems = check_grammar(lines, complete=complete)
    if problems:
        raise AssertionError("event grammar violated:\n  " + "\n  ".join(problems[:30]))
