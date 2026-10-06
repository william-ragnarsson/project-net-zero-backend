"""The event contract: round-trips, envelope key order, cheap line readers, step pairs, schema."""

from __future__ import annotations

import json
import types
import typing
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from netzero import paths
from netzero.events import (
    DATA_CLASSES,
    EVENT_CLASSES,
    STARTED_TO_COMPLETED,
    TERMINAL_EVENT_TYPES,
    TERMINAL_STATES,
    ErrorInfo,
    EventBase,
    Heartbeat,
    LogData,
    OpenStep,
    RunEventAdapter,
    RunInterruptedData,
    StepDone,
    dump_event,
    line_seq,
    line_type,
    make_event,
    parse_event,
)

ENVELOPE = ["seq", "ts", "run_id", "function_id", "candidate_id", "attempt"]
RUN_ID = "20261005-120000-demo"
# a string that would fool a naive "type" scan if JSON escaping were ever lost
NASTY = 'x "type":"evil" \\ \n'


def _example(tp: Any, *, full: bool) -> Any:
    """A valid value for annotation ``tp``; ``full`` also fills optional fields."""
    origin, args = get_origin(tp), get_args(tp)
    if tp is type(None):
        return None
    if origin is Literal:
        return args[0]
    if origin in (typing.Union, types.UnionType):
        return _example(next(a for a in args if a is not type(None)), full=full)
    if origin is list:
        return [_example(args[0], full=full)]
    if origin is dict:
        return {_example(args[0], full=full): _example(args[1], full=full)}
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return build(tp, full=full)
    if tp is bool:
        return True
    if tp is int:
        return 7
    if tp is float:
        return 1.5
    if tp is str:
        return NASTY
    raise TypeError(f"no example for {tp!r}")


def build(cls: type[BaseModel], *, full: bool) -> BaseModel:
    """An instance of ``cls`` with every field (``full``) or only the required ones set."""
    values = {
        name: _example(field.annotation, full=full)
        for name, field in cls.model_fields.items()
        if full or field.is_required()
    }
    return cls.model_validate(values)


def sample(event_type: str, *, full: bool, seq: int = 1) -> EventBase:
    scope: dict[str, Any] = {}
    if full:
        scope = {"function_id": "pkg.mod:Cls.method", "candidate_id": "B", "attempt": 1}
    return make_event(
        event_type,
        seq=seq,
        ts=1_700_000_000_000,
        run_id=RUN_ID,
        data=build(DATA_CLASSES[event_type], full=full),
        **scope,
    )


ALL_TYPES = sorted(EVENT_CLASSES)


def test_every_union_member_is_registered_once() -> None:
    assert len(ALL_TYPES) == 43
    for t, cls in EVENT_CLASSES.items():
        assert cls.model_fields["type"].default == t
        assert DATA_CLASSES[t] is cls.model_fields["data"].annotation


@pytest.mark.parametrize("full", [True, False], ids=["full", "minimal"])
@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_round_trip(event_type: str, full: bool) -> None:
    ev = sample(event_type, full=full, seq=42)
    line = dump_event(ev)
    assert "\n" not in line
    back = parse_event(line)
    assert type(back) is EVENT_CLASSES[event_type]
    assert back == ev
    assert dump_event(back) == line  # canonical: dumping is stable
    assert parse_event(line.encode("utf-8")) == ev


@pytest.mark.parametrize("full", [True, False], ids=["full", "minimal"])
@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_envelope_key_order(event_type: str, full: bool) -> None:
    line = dump_event(sample(event_type, full=full))
    assert line.startswith('{"seq":')
    assert list(json.loads(line)) == [*ENVELOPE, "type", "data"]


@pytest.mark.parametrize("full", [True, False], ids=["full", "minimal"])
@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_cheap_readers_agree_with_parse(event_type: str, full: bool) -> None:
    for seq in (1, 9, 10, 12345):
        line = dump_event(sample(event_type, full=full, seq=seq))
        ev = parse_event(line)
        assert line_seq(line) == ev.seq == seq
        assert line_seq(line.encode("utf-8")) == seq
        assert line_type(line) == ev.type == event_type


def test_line_type_ignores_nested_type_keys() -> None:
    """``OpenStep.type`` lives inside ``data``; the envelope type must win."""
    data = RunInterruptedData(
        previous_state="optimizing",
        open_steps=[
            OpenStep(type="candidate.bench.started", function_id="m:f", candidate_id="A"),
            OpenStep(type="function.started", function_id="m:f"),
        ],
    )
    ev = make_event("run.interrupted", seq=3, ts=1, run_id=RUN_ID, data=data)
    line = dump_event(ev)
    assert line.count('"type":') == 3
    assert line_type(line) == "run.interrupted"
    assert line_seq(line) == 3


def test_line_type_ignores_type_text_in_strings() -> None:
    ev = make_event(
        "log",
        seq=1,
        ts=1,
        run_id=RUN_ID,
        function_id='m:"type":"run.completed"',
        data=LogData(level="info", source="pytest", lines=['{"type":"run.failed"}']),
    )
    line = dump_event(ev)
    assert line_type(line) == "log"
    assert line_type(line) not in TERMINAL_EVENT_TYPES


def test_line_seq_rejects_non_event_lines() -> None:
    with pytest.raises(ValueError):
        line_seq('{"type":"log","seq":1}')
    with pytest.raises(ValueError):
        line_seq("")


def test_parse_rejects_unknown_type_and_extra_fields() -> None:
    line = dump_event(sample("log", full=True))
    obj = json.loads(line)
    with pytest.raises(ValidationError):
        parse_event(json.dumps({**obj, "type": "log.nope"}))
    with pytest.raises(ValidationError):
        parse_event(json.dumps({**obj, "surprise": 1}))
    with pytest.raises(ValidationError):
        parse_event(json.dumps({**obj, "data": {**obj["data"], "surprise": 1}}))
    with pytest.raises(ValidationError):
        parse_event(json.dumps({k: v for k, v in obj.items() if k != "seq"}))


def test_events_are_frozen() -> None:
    ev = sample("log", full=False)
    with pytest.raises(ValidationError):
        ev.seq = 2  # type: ignore[misc]


def test_make_event_rejects_wrong_payload_class() -> None:
    with pytest.raises(TypeError):
        make_event("log", seq=1, ts=1, run_id=RUN_ID, data=ErrorInfo(kind="internal", message="x"))


def test_adapter_matches_parse_event() -> None:
    line = dump_event(sample("candidate.bench.completed", full=True))
    assert RunEventAdapter.validate_json(line) == parse_event(line)


# -- step pairs + terminals -------------------------------------------------------


def test_started_to_completed_pairs_are_consistent() -> None:
    started = {t for t in EVENT_CLASSES if t.endswith(".started")}
    completed = {t for t in EVENT_CLASSES if t.endswith(".completed")}
    assert set(STARTED_TO_COMPLETED) == started
    assert set(STARTED_TO_COMPLETED.values()) == completed - {"run.completed"}
    for s, c in STARTED_TO_COMPLETED.items():
        assert s.removesuffix(".started") == c.removesuffix(".completed")
        assert s not in TERMINAL_EVENT_TYPES and c not in TERMINAL_EVENT_TYPES


@pytest.mark.parametrize("completed", sorted(set(STARTED_TO_COMPLETED.values())))
def test_completed_payloads_can_close_a_step_without_artifacts(completed: str) -> None:
    """``close_open_steps`` builds ``*.completed`` from ok/duration/error alone."""
    data_cls = DATA_CLASSES[completed]
    if completed == "function.completed":
        assert not issubclass(data_cls, StepDone)
        return
    assert issubclass(data_cls, StepDone)
    data = data_cls(ok=False, duration_ms=0, error=ErrorInfo(kind="cancelled", message="x"))
    ev = make_event(completed, seq=1, ts=1, run_id=RUN_ID, data=data)
    assert parse_event(dump_event(ev)) == ev


def test_terminal_types_match_terminal_states() -> None:
    assert {f"run.{s}" for s in TERMINAL_STATES} == set(TERMINAL_EVENT_TYPES)
    assert TERMINAL_EVENT_TYPES <= set(EVENT_CLASSES)


def test_heartbeat_is_not_an_event() -> None:
    hb = Heartbeat(run_id=RUN_ID, last_seq=3, state="optimizing", server_ts=1)
    assert "heartbeat" not in EVENT_CLASSES
    with pytest.raises(ValidationError):
        parse_event(hb.model_dump_json())


# -- schema export ------------------------------------------------------------------


def test_exported_schema_is_in_sync() -> None:
    if not paths.SCHEMA_OUT.exists():
        pytest.skip(f"{paths.SCHEMA_OUT} not generated")
    from netzero.api.schemas import contract_schema

    expected = json.dumps(contract_schema(), indent=2) + "\n"
    assert paths.SCHEMA_OUT.read_text("utf-8") == expected, (
        "web/src/gen/schema.json is stale; run `uv run netzero export-schema`"
    )
