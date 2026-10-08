"""Connections and schema migrations (``migrations/NNNN_*.sql``, applied in order)."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

# pg_advisory_xact_lock keys; any bigint, one per purpose
MIGRATE_LOCK = 0x6E7A_0001
APPEND_LOCK = 0x6E7A_0002


def connect(url: str, *, timeout_s: int = 5) -> psycopg.Connection:
    """An autocommit connection; callers open transactions explicitly."""
    return psycopg.connect(url, autocommit=True, connect_timeout=timeout_s)


def describe(url: str) -> str:
    """``dbname on host:port``, without the password, for status lines."""
    try:
        info = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return "an unparseable NETZERO_DATABASE_URL"
    host = info.get("host") or "localhost"
    port = info.get("port") or "5432"
    return f"{info.get('dbname') or info.get('user') or 'postgres'} on {host}:{port}"


def migrations() -> list[tuple[str, str]]:
    """``(version, sql)`` for every migration file, oldest first."""
    return [
        (p.stem.split("_", 1)[0], p.read_text("utf-8"))
        for p in sorted(MIGRATIONS_DIR.glob("*.sql"))
    ]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations; returns the versions applied now.

    Each migration and its bookkeeping row commit together, under an advisory
    lock, so two processes starting at once don't both apply one.
    """
    encoding = conn.execute("show server_encoding").fetchone()[0]  # type: ignore[index]
    if isinstance(encoding, bytes):  # what a SQL_ASCII database hands back
        encoding = encoding.decode()
    if encoding != "UTF8":  # psycopg would hand back bytes, and jsonb can't hold all of a log
        raise psycopg.NotSupportedError(
            f"the database is {encoding}; history needs a UTF8 one (createdb -E UTF8 -T template0)"
        )
    applied: list[str] = []
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATE_LOCK,))
        conn.execute(
            "create table if not exists schema_migrations ("
            " version text primary key, applied_at timestamptz not null default now())"
        )
        done = {row[0] for row in conn.execute("select version from schema_migrations")}
        for version, sql in migrations():
            if version in done:
                continue
            conn.execute(sql)  # type: ignore[arg-type]  # a trusted, multi-statement file
            conn.execute("insert into schema_migrations (version) values (%s)", (version,))
            applied.append(version)
    return applied


def applied_migrations(conn: psycopg.Connection) -> list[str]:
    try:
        with conn.transaction():
            return [r[0] for r in conn.execute("select version from schema_migrations order by 1")]
    except psycopg.errors.UndefinedTable:
        return []
