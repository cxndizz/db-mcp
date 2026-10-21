"""SQL safety guard: only single read-only SELECT statements are allowed, and a
row cap is always injected. This runs BEFORE the query is sent to the database.

Expensive-query checks that need the database (EXPLAIN PLAN, object existence)
live in ``db``; this module is pure so it is trivially unit-testable.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DIALECT = "oracle"

# Expression types that are never allowed at the top level.
_FORBIDDEN_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Command,            # Command covers unparsed / DDL-ish / PLSQL
)

# Allowed top-level shapes: plain SELECT, set operations, and WITH ... SELECT.
_ALLOWED_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


class SqlNotAllowed(ValueError):
    """Raised when a statement fails the read-only / sanity guard."""


def _strip_trailing_semicolons(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def parse_single_select(sql: str) -> exp.Expression:
    """Parse ``sql`` and assert it is exactly one read-only SELECT statement."""
    raw = _strip_trailing_semicolons(sql)
    if not raw:
        raise SqlNotAllowed("Empty query.")

    try:
        statements = sqlglot.parse(raw, dialect=DIALECT)
    except sqlglot.errors.ParseError as e:
        raise SqlNotAllowed(f"Could not parse SQL (syntax error): {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) == 0:
        raise SqlNotAllowed("No statement found.")
    if len(statements) > 1:
        raise SqlNotAllowed("Only a single statement is allowed; multiple statements were found.")

    stmt = statements[0]

    if isinstance(stmt, _FORBIDDEN_TYPES):
        raise SqlNotAllowed(
            f"Only read-only SELECT queries are allowed; got {stmt.key.upper()}."
        )
    if not isinstance(stmt, _ALLOWED_TYPES):
        raise SqlNotAllowed(
            f"Only SELECT / WITH queries are allowed; got {stmt.key.upper()}."
        )

    # Guard against SELECT ... FOR UPDATE (acquires locks).
    select = stmt.find(exp.Select)
    if select is not None and select.args.get("locks"):
        raise SqlNotAllowed("SELECT ... FOR UPDATE is not allowed (read-only).")

    return stmt


def referenced_tables(stmt: exp.Expression) -> set[tuple[str | None, str]]:
    """Return {(schema_or_None, table_upper)} for real tables referenced.

    CTE names are excluded so they are not mistaken for missing tables.
    """
    cte_names = {c.alias_or_name.upper() for c in stmt.find_all(exp.CTE)}
    out: set[tuple[str | None, str]] = set()
    for t in stmt.find_all(exp.Table):
        name = (t.name or "").upper()
        if not name or name in cte_names:
            continue
        schema = t.db.upper() if t.db else None
        out.add((schema, name))
    return out


def has_cartesian_risk(stmt: exp.Expression) -> bool:
    """Heuristic: multiple base tables in a FROM with no JOIN ... ON and no WHERE.

    A rough signal of an accidental cross join; the EXPLAIN cost check in ``db``
    is the authoritative gate.
    """
    for select in stmt.find_all(exp.Select):
        from_ = select.args.get("from")
        joins = select.args.get("joins") or []
        where = select.args.get("where")
        # comma-joined tables show up as additional expressions in FROM on older
        # parses; modern sqlglot normalises them to joins, so rely on joins list.
        unqualified_joins = [
            j for j in joins
            if not j.args.get("on") and not j.args.get("using")
            and (j.kind or "").upper() not in ("CROSS",)  # explicit CROSS is intentional
        ]
        if unqualified_joins and where is None:
            return True
    return False


def enforce_row_cap(sql: str, max_rows: int) -> str:
    """Wrap a validated query so it can never return more than ``max_rows`` rows.

    Wrapping with an outer ROWNUM predicate also neutralises trailing content and
    works regardless of any inner ORDER BY / FETCH clause.
    """
    inner = _strip_trailing_semicolons(sql)
    return f"SELECT * FROM (\n{inner}\n) WHERE ROWNUM <= {int(max_rows)}"
