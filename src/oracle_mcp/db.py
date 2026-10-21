"""Database access layer. Engine-agnostic: it resolves a connection + username
+ keyring/secret password, opens it through the right engine (Oracle or
Postgres), and runs guarded read-only queries and metadata lookups.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from . import keyvault
from .config import SETTINGS, ConnOverride, get_override
from .discovery import Connection, find
from .engines import Engine, get_engine
from .safety import parse_single_select

_semaphore = threading.BoundedSemaphore(SETTINGS.max_concurrency)


class DbError(RuntimeError):
    pass


class PasswordMissing(DbError):
    def __init__(self, conn_id: str):
        super().__init__(
            f"No password stored for connection '{conn_id}'. "
            f"Run:  oracle-mcp set-password \"{conn_id}\""
        )
        self.conn_id = conn_id


class UsernameMissing(DbError):
    def __init__(self, conn_id: str):
        super().__init__(
            f"No username known for connection '{conn_id}'. "
            f"Provide one with:  oracle-mcp set-password \"{conn_id}\" --user <USER>"
        )
        self.conn_id = conn_id


def _secret(conn_id: str) -> str | None:
    """Password from the keyring, or a DB_MCP_PW_<id> env var (server mode)."""
    import os
    env_key = "DB_MCP_PW_" + "".join(ch if ch.isalnum() else "_" for ch in conn_id).upper()
    return os.environ.get(env_key) or keyvault.get_password(conn_id)


def resolve(selector: str) -> tuple[Connection, ConnOverride, str, str]:
    """Resolve a selector to (connection, override, username, password)."""
    conn = find(selector)
    if conn is None:
        raise DbError(f"Unknown connection '{selector}'. Use list_connections to see available ones.")
    override = get_override(conn.id)
    username = conn.username or override.username
    if not username:
        raise UsernameMissing(conn.id)
    password = _secret(conn.id)
    if not password:
        raise PasswordMissing(conn.id)
    return conn, override, username, password


def engine_for(selector: str) -> Engine:
    conn = find(selector)
    if conn is None:
        raise DbError(f"Unknown connection '{selector}'.")
    return get_engine(conn.engine)


@contextmanager
def _open(engine: Engine, conn: Connection, user: str, password: str) -> Iterator[Any]:
    with _semaphore:
        connection = engine.connect(conn, user, password, SETTINGS.query_timeout)
        try:
            yield connection
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            connection.close()


def run_select(selector: str, sql: str, max_rows: int) -> dict[str, Any]:
    """Validate, cost-check, and execute a read-only SELECT. Returns columns+rows."""
    conn, _override, username, password = resolve(selector)
    engine = get_engine(conn.engine)
    parse_single_select(sql, dialect=engine.dialect)  # raises SqlNotAllowed
    max_rows = max(1, min(int(max_rows), SETTINGS.hard_max_rows))
    capped = engine.row_cap(sql, max_rows)
    with _open(engine, conn, username, password) as c:
        return engine.execute_guarded(c, sql, capped, max_rows, SETTINGS.explain_cost_limit)


def meta_query(selector: str, sql: str, binds: dict[str, Any] | None = None,
               max_rows: int | None = None) -> list[dict[str, Any]]:
    """Run a trusted internal metadata SELECT read-only. Keys are upper-cased so
    results look the same across engines."""
    conn, _override, username, password = resolve(selector)
    engine = get_engine(conn.engine)
    cap = max_rows or SETTINGS.hard_max_rows
    with _open(engine, conn, username, password) as c:
        cur = c.cursor()
        engine.begin_read_only(cur)
        # Pass params only when present: psycopg scans for '%' placeholders when
        # params are given, which breaks a literal '%' in SQL (e.g. LIKE 'pg\_%').
        if binds:
            cur.execute(sql, binds)
        else:
            cur.execute(sql)
        columns = [d[0].upper() for d in cur.description]
        return [dict(zip(columns, r)) for r in cur.fetchmany(cap)]


def get_ddl(selector: str, schema: str, obj: str) -> str:
    conn, _override, username, password = resolve(selector)
    engine = get_engine(conn.engine)
    with _open(engine, conn, username, password) as c:
        return engine.get_ddl(c, engine.fold(schema), engine.fold(obj))


def test_connection(selector: str) -> bool:
    """Return True if we can open the connection and run a trivial SELECT."""
    try:
        conn, _o, username, password = resolve(selector)
        engine = get_engine(conn.engine)
        with _open(engine, conn, username, password) as c:
            cur = c.cursor()
            engine.begin_read_only(cur)
            engine.ping(cur)
        return True
    except Exception:
        return False
