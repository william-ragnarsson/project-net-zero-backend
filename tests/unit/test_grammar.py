"""``check_grammar``: a real run passes, and each kind of corruption is reported."""

from __future__ import annotations

import pytest

from netzero.events import (
    EventBase,
    LogData,
    RunSelectionConfirmedData,
    RunStateChangedData,
    dump_event,
    make_event,
)
from netzero.pipeline.grammar import assert_grammar, check_grammar
from netzero.pipeline.run import RunContext
from tests.unit.support import FID, FID2, drive_valid_run, parse_all, read_lines, renumber


@pytest.fixture
async def events(ctx: RunContext) -> list[EventBase]:
    """A complete valid run: FID accepted, FID2 failed by an exception in its block."""
    await drive_valid_run(ctx, (FID, FID2), failing={FID2})
    return parse_all(read_lines(ctx))


def at(events: list[EventBase], type_: str, fid: str | None = None, nth: int = 0) -> int:
    """Index of the ``nth`` event of ``type_`` (optionally for function ``fid``)."""
    hits = [
        i
        for i, ev in enumerate(events)
        if ev.type == type_ and (fid is None or ev.function_id == fid)  # type: ignore[attr-defined]
    ]
    return hits[nth]


def new(events: list[EventBase], type_: str, data=None, **scope) -> EventBase:
    """An event for the same run and timestamp as ``events`` (seq fixed by ``renumber``)."""
    first = events[0]
    return make_event(type_, seq=1, ts=first.ts, run_id=first.run_id, data=data, **scope)


def check(events: list[EventBase], *, complete: bool = True) -> list[str]:
    return check_grammar(renumber(events), complete=complete)


def has(problems: list[str], text: str) -> bool:
    return any(text in p for p in problems)


def log_event(events: list[EventBase], **scope) -> EventBase:
    return new(events, "log", LogData(level="info", source="pytest", lines=["x"]), **scope)


# -- valid logs ------------------------------------------------------------------


def test_real_run_is_valid(events: list[EventBase]) -> None:
    lines = [dump_event(ev) for ev in events]
    assert check_grammar(lines) == []
    assert_grammar(lines)
    types = [ev.type for ev in events]  # type: ignore[attr-defined]
    # the shape the rules below are checked against
    assert types[0] == "run.created" and types[-1] == "run.completed"
    assert at(events, "function.merge.started", FID) > at(events, "function.decision", FID)
    assert "log" in types  # FID2's exception was logged


def test_every_prefix_is_valid_while_running(events: list[EventBase]) -> None:
    lines = [dump_event(ev) for ev in events]
    for n in range(1, len(lines)):
        assert check_grammar(lines[:n], complete=False) == [], n
        # complete=True adds the missing terminal, plus any steps left open
        first, *rest = check_grammar(lines[:n])
        assert first == "log does not end with a terminal event", n
        assert all(p.startswith("open steps at the end: ") for p in rest), n
        assert len(rest) <= 1, n


def test_assert_grammar_raises_with_the_problems() -> None:
    with pytest.raises(AssertionError, match="no events"):
        assert_grammar([])


def test_unparseable_line_stops_the_check(events: list[EventBase]) -> None:
    lines = [dump_event(ev) for ev in events[:3]] + ['{"seq": 4, "nope": true}']
    problems = check_grammar(lines)
    assert len(problems) == 1 and problems[0].startswith("line 4: does not parse")


# -- envelope ----------------------------------------------------------------------


def test_seq_gap(events: list[EventBase]) -> None:
    dropped = at(events, "candidate.rejected", FID)
    lines = [dump_event(ev) for i, ev in enumerate(events) if i != dropped]
    problems = check_grammar(lines)
    assert problems and all("seq gap" in p for p in problems)
    # the event that moved up is the first one flagged
    assert problems[0] == f"seq {dropped + 2} function.decision: seq gap: expected {dropped + 1}"


def test_duplicate_line_is_a_seq_gap(events: list[EventBase]) -> None:
    lines = [dump_event(ev) for ev in events]
    i = at(events, "candidate.rejected", FID)
    problems = check_grammar(lines[: i + 1] + lines[i:])
    assert has(problems, f"seq {i + 1} candidate.rejected: seq gap: expected {i + 2}")


def test_run_id_mismatch(events: list[EventBase]) -> None:
    i = at(events, "candidate.rejected", FID)
    events[i] = events[i].model_copy(update={"run_id": "20261005-000000-other"})
    assert has(check(events), "run_id '20261005-000000-other' !=")


def test_ts_going_backwards(events: list[EventBase]) -> None:
    i = at(events, "candidate.rejected", FID)
    events[i] = events[i].model_copy(update={"ts": events[0].ts - 1})
    assert has(check(events), "ts went backwards")


def test_first_event_must_be_run_created(events: list[EventBase]) -> None:
    assert check(events[1:]) == ["seq 1 run.state_changed: first event must be run.created"]


# -- steps -------------------------------------------------------------------------


def test_completed_without_started(events: list[EventBase]) -> None:
    del events[at(events, "run.clone.started")]
    assert check(events) == [
        "seq 3 run.clone.completed: run.clone.completed without an open run.clone.started"
        " for (None, None, None)"
    ]


def test_started_twice(events: list[EventBase]) -> None:
    i = at(events, "run.clone.started")
    events.insert(i, events[i])
    problems = check(events)
    assert has(problems, "run.clone.started started twice")


def test_started_never_completed_is_reported_at_the_terminal(events: list[EventBase]) -> None:
    del events[at(events, "run.clone.completed")]
    problems = check(events)
    assert len(problems) == 1
    assert "run.completed: terminal event with open steps" in problems[0]
    assert "run.clone.started" in problems[0]


def test_started_never_completed_without_terminal(events: list[EventBase]) -> None:
    upto = at(events, "run.clone.started")
    assert check(events[: upto + 1]) == [
        "log does not end with a terminal event",
        "open steps at the end: [\"('run.clone.started', None, None, None)\"]",
    ]
    assert check(events[: upto + 1], complete=False) == []


def test_function_completed_with_open_steps(events: list[EventBase]) -> None:
    del events[at(events, "candidate.write.completed", FID)]
    problems = check(events)
    assert has(problems, "function.completed: function completed with open steps")
    assert has(problems, "terminal event with open steps")


# -- run state ---------------------------------------------------------------------


def test_invalid_transition(events: list[EventBase]) -> None:
    jump = new(
        events,
        "run.state_changed",
        RunStateChangedData(from_state="created", to_state="optimizing"),
    )
    events.insert(1, jump)
    problems = check(events)
    assert problems[0] == "seq 2 run.state_changed: invalid transition created -> optimizing"
    assert has(problems, "seq 3 run.state_changed: from_state created but the run is optimizing")


def test_from_state_must_match(events: list[EventBase]) -> None:
    i = at(events, "run.state_changed", nth=1)  # cloning -> installing
    wrong = RunStateChangedData(from_state="discovering", to_state="installing")
    events[i] = events[i].model_copy(update={"data": wrong})
    assert check(events) == [
        f"seq {i + 1} run.state_changed: from_state discovering but the run is cloning"
    ]


def test_event_after_terminal(events: list[EventBase]) -> None:
    events.append(log_event(events))
    n = len(events)
    assert check(events) == [f"seq {n} log: event after the terminal event (seq {n - 1})"]


def test_transition_out_of_a_terminal_state(events: list[EventBase]) -> None:
    events.append(
        new(
            events,
            "run.state_changed",
            RunStateChangedData(from_state="completed", to_state="failed"),
        )
    )
    problems = check(events)
    assert has(problems, "event after the terminal event")
    assert has(problems, "invalid transition completed -> failed")


def test_terminal_must_match_the_state(events: list[EventBase]) -> None:
    del events[-2]  # finalizing -> completed
    problems = check(events)
    assert has(problems, "terminal run.completed but the run is finalizing")
    assert has(problems, "terminal event must directly follow run.state_changed")


def test_terminal_must_directly_follow_state_changed(events: list[EventBase]) -> None:
    events.insert(len(events) - 1, log_event(events))
    assert check(events) == [
        f"seq {len(events)} run.completed: terminal event must directly follow run.state_changed"
    ]


def test_terminal_with_open_function(events: list[EventBase]) -> None:
    del events[at(events, "function.completed", FID2)]
    assert has(check(events), f"terminal event with open function {FID2}")


# -- selection + functions ---------------------------------------------------------


def test_selection_confirmed_twice(events: list[EventBase]) -> None:
    i = at(events, "run.selection.confirmed")
    events.insert(i, events[i])
    assert has(check(events), "selection confirmed twice")


def test_selection_confirmed_in_the_wrong_state(events: list[EventBase]) -> None:
    sel = events.pop(at(events, "run.selection.confirmed"))
    events.insert(at(events, "run.state_changed", nth=4), sel)  # before triaging -> awaiting
    assert has(check(events), "selection confirmed in state triaging")


def test_function_not_in_selection(events: list[EventBase]) -> None:
    i = at(events, "run.selection.confirmed")
    only = RunSelectionConfirmedData(function_ids=[FID])
    events[i] = events[i].model_copy(update={"data": only})
    assert has(check(events), f"{FID2} is not in the selection")


def test_function_started_outside_optimizing(events: list[EventBase]) -> None:
    del events[at(events, "run.selection.confirmed") + 1]  # awaiting_selection -> optimizing
    assert has(check(events), "function started in state awaiting_selection")


def test_function_started_twice(events: list[EventBase]) -> None:
    s, c = at(events, "function.started", FID), at(events, "function.completed", FID)
    events[c + 1 : c + 1] = [events[s], events[c]]
    assert has(check(events), f"{FID} started twice")


def test_function_started_while_another_is_open(events: list[EventBase]) -> None:
    del events[at(events, "function.completed", FID)]
    assert has(check(events), f"function started while {FID} is still open")


def test_decided_twice(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    events.insert(i, events[i])
    assert has(check(events), "decided twice")


def test_candidate_event_after_decision(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    late = new(
        events, "candidate.rejected", {"reason": "identical"}, function_id=FID, candidate_id="C"
    )
    events.insert(i + 1, late)
    assert check(events) == [
        f"seq {i + 2} candidate.rejected: candidate event after function.decision"
    ]


# -- scope -------------------------------------------------------------------------


def test_run_event_with_function_scope(events: list[EventBase]) -> None:
    i = at(events, "run.clone.started")
    events[i] = events[i].model_copy(update={"function_id": FID})
    events[i + 1] = events[i + 1].model_copy(update={"function_id": FID})
    problems = check(events)
    assert has(problems, "run.clone.started: run event with a function/candidate scope")
    assert has(problems, f"{FID} is not the open function (None)")


def test_function_event_without_function_id(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    events[i] = events[i].model_copy(update={"function_id": None})
    assert has(check(events), "function.decision: function-scoped event without function_id")


def test_candidate_event_without_candidate_id(events: list[EventBase]) -> None:
    i = at(events, "candidate.rejected", FID)
    events[i] = events[i].model_copy(update={"candidate_id": None})
    assert has(check(events), "candidate.rejected: candidate event without candidate_id")


def test_event_for_a_function_that_is_not_open(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    events.insert(i, log_event(events, function_id=FID2))
    assert check(events) == [f"seq {i + 1} log: {FID2} is not the open function ({FID})"]


def test_no_events() -> None:
    assert check_grammar([]) == ["no events"]


def test_candidate_id_without_function_id(events: list[EventBase]) -> None:
    i = at(events, "run.clone.completed")
    events.insert(i + 1, log_event(events, candidate_id="A"))
    assert has(check(events), "log: candidate_id without function_id")


def test_run_created_twice(events: list[EventBase]) -> None:
    events.insert(1, events[0])
    assert has(check(events), "run.created: run.created again")


# -- decisions ---------------------------------------------------------------------


def test_outcome_contradicts_decision(events: list[EventBase]) -> None:
    i = at(events, "function.completed", FID)
    data = events[i].data.model_copy(update={"outcome": "no_significant_win"})  # type: ignore[attr-defined]
    events[i] = events[i].model_copy(update={"data": data})
    assert check(events) == [
        f"seq {i + 1} function.completed: outcome no_significant_win contradicts decision winner"
    ]


def test_merge_without_a_winning_decision(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    data = events[i].data.model_copy(update={"outcome": "all_rejected", "winner": None})  # type: ignore[attr-defined]
    events[i] = events[i].model_copy(update={"data": data})
    problems = check(events)
    assert has(problems, "function.merge.started: merge without a winning decision")
    assert has(problems, "outcome accepted contradicts decision all_rejected")


def test_accepted_without_a_decision(events: list[EventBase]) -> None:
    i = at(events, "function.decision", FID)
    del events[i]
    problems = check(events)
    assert has(problems, "merge without a winning decision")
    assert has(problems, "function.completed: outcome accepted without a decision")
