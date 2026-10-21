"""Database access layer (python-oracledb thin mode).

Responsibilities:
* resolve a discovered connection + username + keyring password,
* open a connection with a query timeout,
* run a cheap EXPLAIN PLAN first to reject bad/hallucinated/expensive queries,
* execute the actual data fetch inside a ``SET TRANSACTION READ ONLY`` session,
* provide read-only metadata helpers used by the MCP tools.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

import oracledb

from . import keyvault
from .config import SETTINGS, ConnOverride, get_override
from .discovery import Connection, find
from .safety import SqlNotAllowed, enforce_row_cap, parse_single_select

# ORA error codes that mean "the query itself is wrong" -> reject, don't run.
_BAD_QUERY_CODES = {904, 918, 923, 933, 936, 942, 949, 1747, 1789, 1790}

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
            f"No username known for connection '{conn_id}' (DBeaver does not store it). "
            f"Provide one with:  oracle-mcp set-password \"{conn_id}\" --user <USER>"
        )
        self.conn_id = conn_id


def resolve(selector: str) -> tuple[Connection, ConnOverride, str, str]:
    """Resolve a connection selector to (connection, override, username, password)."""
    conn = find(selector)
    if conn is None:
        raise DbError(f"Unknown connection '{selector}'. Use list_connections to see available ones.")
    override = get_override(conn.id)
    username = conn.username or override.username
    if not username:
        raise UsernameMissing(conn.id)
    password = keyvault.get_password(conn.id)
    if not password:
        raise PasswordMissing(conn.id)
    return conn, override, username, password


@contextmanager
def _open(conn: Connection, username: str, password: str) -> Iterator[oracledb.Connection]:
    with _semaphore:
        connection = oracledb.connect(user=username, password=password, dsn=conn.dsn)
        connection.call_timeout = max(1, SETTINGS.query_timeout) * 1000  # ms
        try:
            yield connection
        finally:
            try:
                connection.rollback()
            except oracledb.Error:
                pass
            connection.close()


def _begin_read_only(cur: oracledb.Cursor) -> None:
    cur.execute("SET TRANSACTION READ ONLY")


def _explain_cost(cur: oracledb.Cursor, sql: str) -> int | None:
    """Run EXPLAIN PLAN (cheap, no execution) and return the plan's top cost.

    Doubles as an object/column-existence check: a wrong table or column makes
    EXPLAIN fail with a bad-query ORA code, which we surface as a rejection
    instead of a costly runtime error. Writes to the session-private PLAN_TABLE
    are rolled back by the caller.
    """
    sid = "mcp_" + uuid4().hex[:16]
    cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {sql}")
    cur.execute(
        "SELECT MAX(cost) FROM plan_table WHERE statement_id = :sid",
        sid=sid,
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def run_select(selector: str, sql: str, max_rows: int) -> dict[str, Any]:
    """Validate, cost-check, and execute a read-only SELECT. Returns columns+rows."""
    parse_single_select(sql)  # raises SqlNotAllowed on anything but a single SELECT
    max_rows = max(1, min(int(max_rows), SETTINGS.hard_max_rows))
    conn, _override, username, password = resolve(selector)
    capped = enforce_row_cap(sql, max_rows)

    with _open(conn, username, password) as connection:
        cur = connection.cursor()

        # --- cost / sanity gate (writes to GTT, then rolled back) ---
        cost: int | None = None
        try:
            cost = _explain_cost(cur, sql)
        except oracledb.DatabaseError as e:
            code = e.args[0].code if e.args else None
            if code in _BAD_QUERY_CODES:
                raise SqlNotAllowed(f"Query rejected: {e.args[0].message.strip()}") from e
            # Otherwise (e.g. no privilege to EXPLAIN) fall through best-effort.
        connection.rollback()

        if cost is not None and cost > SETTINGS.explain_cost_limit:
            raise SqlNotAllowed(
                f"Query rejected: estimated cost {cost} exceeds limit "
                f"{SETTINGS.explain_cost_limit}. Add a WHERE filter or narrow the query."
            )

        # --- actual data fetch, read-only ---
        _begin_read_only(cur)
        cur.execute(capped)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        dict_rows = [dict(zip(columns, r)) for r in rows]

    return {"columns": columns, "rows": dict_rows, "row_count": len(dict_rows), "explain_cost": cost}


def meta_query(selector: str, sql: str, binds: dict[str, Any] | None = None,
               max_rows: int | None = None) -> list[dict[str, Any]]:
    """Run a trusted internal metadata SELECT (ALL_* views) read-only."""
    conn, _override, username, password = resolve(selector)
    cap = max_rows or SETTINGS.hard_max_rows
    with _open(conn, username, password) as connection:
        cur = connection.cursor()
        _begin_read_only(cur)
        cur.execute(sql, binds or {})
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, r)) for r in cur.fetchmany(cap)]


def test_connection(selector: str) -> bool:
    """Return True if we can open the connection and run SELECT 1."""
    try:
        conn, _o, username, password = resolve(selector)
        with _open(conn, username, password) as connection:
            cur = connection.cursor()
            cur.execute("SELECT 1 FROM dual")
            cur.fetchone()
        return True
    except (DbError, oracledb.Error):
        return False
