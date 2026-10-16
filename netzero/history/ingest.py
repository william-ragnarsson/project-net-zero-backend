"""Getting events into the store: follow ``runs/*/events.jsonl``, or import a file.

The pipeline keeps writing files (the bus appends a line before it publishes
the event), and the shipper copies what is new. So a run started from the
CLI is stored as soon as a server or ``netzero db sync`` sees it, and a
database outage delays history instead of failing runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from netzero.events import line_seq, parse_event
from netzero.history import eventstore
from netzero.history.eventstore import ConcurrencyError
from netzero.pipeline.store import RunStore, read_complete_lines

logger = logging.getLogger("netzero.history")


@dataclass
class _Seen:
    size: int
    mtime_ns: int
    offset: int  # bytes of complete lines read so far
    version: int  # the stream's version after the last append


@dataclass
class ShipResult:
    events: int = 0
    runs: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class Shipper:
    def __init__(self, store: RunStore):
        self.store = store
        self._seen: dict[str, _Seen] = {}

    def forget(self) -> None:
        """Drop the file cache, e.g. after the database was recreated."""
        self._seen.clear()

    def ship(self, conn: psycopg.Connection) -> ShipResult:
        """Append every run's unshipped lines. Unchanged files cost one ``stat``."""
        result = ShipResult()
        stored: dict[str, int] | None = None
        for run_id in self.store.list_ids():
            path = self.store.paths(run_id).events
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            seen = self._seen.get(run_id)
            if seen and (seen.size, seen.mtime_ns) == (st.st_size, st.st_mtime_ns):
                continue
            if stored is None:
                stored = eventstore.versions(conn)
            version = stored.get(run_id, 0)
            resume = seen is not None and seen.version == version and seen.offset <= st.st_size
            offset = seen.offset if seen and resume else 0
            lines, offset = read_complete_lines(path, offset)
            try:
                new = [line for line in lines if line_seq(line) > version]
                version = eventstore.append(conn, run_id, version, new)
            except ConcurrencyError:
                self._seen.pop(run_id, None)  # another writer got there; retry next pass
                continue
            except (ValueError, psycopg.errors.IntegrityError) as exc:
                # a malformed log: report it once, then wait for the file to change
                result.problems.append(f"{run_id}: {exc}")
                logger.warning("history: not storing %s: %s", run_id, exc)
            else:
                if new:
                    result.events += len(new)
                    result.runs.append(run_id)
            self._seen[run_id] = _Seen(st.st_size, st.st_mtime_ns, offset, version)
        return result


def import_file(conn: psycopg.Connection, path: Path) -> tuple[str, int]:
    """Store an ``events.jsonl`` from anywhere (a replay, another machine's run).

    Returns ``(run_id, events appended)``. Every line must parse; re-importing
    is a no-op, and a file that grew since is appended to. A file that
    disagrees with what is stored is refused: the store is append-only.
    """
    lines, _ = read_complete_lines(path)
    if not lines:
        raise ValueError(f"{path}: no events")
    first = parse_event(lines[0])
    if first.type != "run.created":  # type: ignore[union-attr]
        raise ValueError(f"{path}: the first event must be run.created")
    for i, line in enumerate(lines, 1):
        ev = parse_event(line)
        if ev.seq != i or ev.run_id != first.run_id:
            raise ValueError(f"{path}: line {i} is seq {ev.seq} of run {ev.run_id}")
    run_id = first.run_id
    stored = eventstore.read_lines(conn, run_id)
    if stored != lines[: len(stored)]:
        raise ValueError(f"{path}: run {run_id} is already stored with different events")
    new = lines[len(stored) :]
    eventstore.append(conn, run_id, len(stored), new)
    return run_id, len(new)
