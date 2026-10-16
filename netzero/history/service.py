"""The server's side of history: a connection pool and a loop that keeps Postgres in sync.

Every ``history_sync_s`` the loop migrates (once), ships new lines from
``runs/`` and folds them into the read models. A database that is down or
not created yet only delays that: the loop logs the error once and keeps
trying, and the history endpoints answer 503 ``no_database`` meanwhile.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from netzero.api.schemas import HistoryStatus
from netzero.config import Settings
from netzero.history import db, projector, queries
from netzero.history.ingest import Shipper, ShipResult
from netzero.history.projector import ProjectResult
from netzero.pipeline.store import RunStore

logger = logging.getLogger("netzero.history")
# the pool warns on every reconnect attempt; the sync loop below reports each new error once
logging.getLogger("psycopg.pool").setLevel(logging.ERROR)

T = TypeVar("T")

READ_TIMEOUT_S = 3.0
STATUS_TIMEOUT_S = 1.5


class HistoryUnavailable(Exception):
    """History is off, or the database can't be reached; the API answers 503."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


class History:
    def __init__(self, settings: Settings, store: RunStore):
        assert settings.database_url
        self.url = settings.database_url
        self.interval_s = settings.history_sync_s
        self.database = db.describe(self.url)
        self.pool = ConnectionPool(
            self.url,
            min_size=1,
            max_size=4,
            open=False,
            timeout=READ_TIMEOUT_S,
            kwargs={"autocommit": True, "connect_timeout": 3},
            check=ConnectionPool.check_connection,
            name="netzero-history",
        )
        self.shipper = Shipper(store)
        self.migrated = False
        self.last_sync_ts: int | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()  # one sync at a time
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle (on the event loop) ----------------------------------------------------

    def start(self) -> None:
        self.pool.open(wait=False)  # connects in the background; a down database is fine
        self._task = asyncio.create_task(self._loop(), name="netzero-history")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.last_error is None:  # one last pass, for the events of the shutdown itself
            try:
                await asyncio.to_thread(self.sync_once)
            except Exception as exc:  # noqa: BLE001  # the next start ships them instead
                logger.warning("history: final sync failed: %s", first_line(exc))
        await asyncio.to_thread(self.pool.close)

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sync_once)
            except Exception as exc:  # noqa: BLE001  # keep trying; say so once per new error
                if isinstance(exc, psycopg.errors.UndefinedTable):
                    # the database was recreated under us: migrate and reship
                    self.migrated = False
                    self.shipper.forget()
                if isinstance(exc, PoolTimeout):
                    msg = await asyncio.to_thread(self._why_unreachable)
                else:
                    msg = first_line(exc)
                if msg != self.last_error:
                    logger.warning("history: sync to %s failed: %s", self.database, msg)
                self.last_error = msg
            await asyncio.sleep(self.interval_s)

    # -- work (in threads) ----------------------------------------------------------------

    def sync_once(self) -> tuple[ShipResult, ProjectResult]:
        """Migrate if needed, ship new lines, fold them. Raises on database errors."""
        with self._lock, self.pool.connection() as conn:
            if not self.migrated:
                applied = db.migrate(conn)
                if applied:
                    logger.info("history: applied migrations %s", ", ".join(applied))
                self.migrated = True
            shipped = self.shipper.ship(conn)
            projected = projector.project_pending(conn)
        if self.last_error:
            logger.info("history: syncing to %s again", self.database)
        self.last_error = None
        self.last_sync_ts = int(time.time() * 1000)
        return shipped, projected

    def read(self, fn: Callable[[psycopg.Connection], T]) -> T:
        try:
            with self.pool.connection() as conn:
                return fn(conn)
        except psycopg.errors.UndefinedTable:
            raise HistoryUnavailable(f"{self.database} has no history tables yet") from None
        except (PoolTimeout, psycopg.OperationalError) as exc:
            raise HistoryUnavailable(self._unreachable(exc)) from None

    def status(self) -> HistoryStatus:
        base = HistoryStatus(
            enabled=True,
            ok=False,
            detail="",
            database=self.database,
            last_sync_ts=self.last_sync_ts,
        )
        try:
            with self.pool.connection(timeout=STATUS_TIMEOUT_S) as conn:
                base.server_version = conn.execute("show server_version").fetchone()[0]  # type: ignore[index]
                base.migrations = db.applied_migrations(conn)
                if not base.migrations:
                    base.detail = self.last_error or "connected; creating the tables"
                    return base
                base.tables = queries.tables(conn)
                base.head_position, base.projected_position = queries.positions(conn)
                base.recent = queries.recent_events(conn)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            base.detail = self._unreachable(exc)
            return base
        base.ok = self.last_error is None and self.last_sync_ts is not None
        base.detail = self.last_error or ("in sync" if base.ok else "starting")
        return base

    def _why_unreachable(self) -> str:
        """The pool only says it timed out; one direct attempt says why."""
        try:
            db.connect(self.url, timeout_s=2).close()
        except psycopg.Error as exc:
            return first_line(exc)
        return "timed out waiting for a pooled connection"

    def _unreachable(self, exc: Exception) -> str:
        # a pool timeout says nothing useful; the sync loop's error usually does
        why = self.last_error or first_line(exc)
        return f"can't reach {self.database}: {why}"
