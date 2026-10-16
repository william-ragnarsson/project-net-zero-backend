"""The append-only event store: one stream per run, each event its exact line.

A stream's ``version`` is the seq of its last event. ``append`` takes the
version the writer last saw and fails if another writer moved it first
(optimistic concurrency), so two processes shipping the same run can't fork
its history. The table refuses UPDATE, DELETE and TRUNCATE (see the migration).
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg

from netzero.events import line_seq
from netzero.history.db import APPEND_LOCK


class ConcurrencyError(Exception):
    """The stream's version is not the one the writer expected."""


def stream_id(run_id: str) -> str:
    return f"run:{run_id}"


def append(
    conn: psycopg.Connection, run_id: str, expected_version: int, lines: Sequence[str]
) -> int:
    """Append ``lines`` (seq ``expected_version + 1`` onwards); returns the new version.

    Appends are serialized by one advisory lock. That costs throughput nobody
    needs locally and buys an ordering guarantee: positions commit in order,
    so a projector reading ``position > checkpoint`` never skips a row that
    was still uncommitted when it read.
    """
    if not lines:
        return expected_version
    for want, line in enumerate(lines, expected_version + 1):
        if line_seq(line) != want:
            raise ValueError(f"run {run_id}: expected seq {want}, got {line_seq(line)}")
    sid = stream_id(run_id)
    version = expected_version + len(lines)
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (APPEND_LOCK,))
        conn.execute(
            "insert into streams (stream_id, kind) values (%s, 'run') on conflict do nothing",
            (sid,),
        )
        moved = conn.execute(
            "update streams set version = %s, updated_at = now()"
            " where stream_id = %s and version = %s",
            (version, sid, expected_version),
        )
        if moved.rowcount != 1:
            raise ConcurrencyError(f"{sid} is no longer at version {expected_version}")
        with conn.cursor().copy("copy events (stream_id, stream_seq, line) from stdin") as copy:
            for seq, line in enumerate(lines, expected_version + 1):
                copy.write_row((sid, seq, line))
    return version


def versions(conn: psycopg.Connection) -> dict[str, int]:
    """Run id -> the seq of its last stored event."""
    rows = conn.execute("select stream_id, version from streams where kind = 'run'")
    return {sid.removeprefix("run:"): version for sid, version in rows}


def read_lines(conn: psycopg.Connection, run_id: str, after: int = 0) -> list[str]:
    """The run's stored lines with ``seq > after``, in order."""
    rows = conn.execute(
        "select line from events where stream_id = %s and stream_seq > %s order by stream_seq",
        (stream_id(run_id), after),
    )
    return [row[0] for row in rows]
