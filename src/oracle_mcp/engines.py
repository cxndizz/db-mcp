"""Database-engine abstraction so db-mcp can speak to Oracle and PostgreSQL
from one codebase.

Each engine owns the dialect-specific bits: how to connect read-only with a
timeout, how to cap rows, how to EXPLAIN a query for the cost/sanity gate, how
to fold/quote identifiers, and the catalog queries behind the metadata tools.

Drivers are imported lazily so a machine that only talks to one engine does not
need the other's driver installed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

from .safety import SqlNotAllowed


def get_engine(name: str) -> "Engine":
    key = (name or "oracle").lower()
    if key == "oracle":
        return OracleEngine()
    if key in ("postgres", "postgresql", "pg"):
        return PostgresEngine()
    raise ValueError(f"Unsupported engine: {name}")


class Engine(ABC):
    name: str
    dialect: str  # sqlglot dialect

    # --- connection / session ---
    @abstractmethod
    def connect(self, conn, user: str, password: str, timeout_s: int): ...

    @abstractmethod
    def begin_read_only(self, cur) -> None: ...

    @abstractmethod
    def ping(self, cur) -> None: ...

    # --- identifiers ---
    @abstractmethod
    def fold(self, name: str) -> str:
        """Catalog-stored case for an unquoted identifier."""

    @abstractmethod
    def quote(self, name: str) -> str:
        """A safely-quoted identifier for building SQL."""

    # --- query guarding / execution ---
    @abstractmethod
    def row_cap(self, sql: str, n: int) -> str: ...

    @abstractmethod
    def execute_guarded(self, conn, sql: str, capped: str, max_rows: int,
                        cost_limit: int) -> dict[str, Any]: ...

    # --- metadata: each returns (sql, binds); aliases match the keys tools expect ---
    @abstractmethod
    def sql_list_schemas(self) -> tuple[str, dict]: ...

    @abstractmethod
    def sql_list_tables(self, schema: str) -> tuple[str, dict]: ...

    @abstractmethod
    def sql_columns(self, schema: str, table: str) -> tuple[str, dict]: ...

    @abstractmethod
    def sql_keys(self, schema: str, table: str) -> tuple[str, dict]: ...

    @abstractmethod
    def sql_search_objects(self, keyword: str, owners: list[str] | None) -> tuple[str, dict]: ...

    @abstractmethod
    def sql_search_columns(self, keyword: str, owners: list[str] | None) -> tuple[str, dict]: ...

    @abstractmethod
    def get_ddl(self, conn, schema: str, obj: str) -> str: ...


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

class OracleEngine(Engine):
    name = "oracle"
    dialect = "oracle"
    _BAD_CODES = {904, 918, 923, 933, 936, 942, 949, 1747, 1789, 1790}

    def connect(self, conn, user, password, timeout_s):
        import oracledb
        dsn = (oracledb.makedsn(conn.host, conn.port, service_name=conn.db_name)
               if conn.is_service else
               oracledb.makedsn(conn.host, conn.port, sid=conn.db_name))
        c = oracledb.connect(user=user, password=password, dsn=dsn)
        c.call_timeout = max(1, timeout_s) * 1000
        return c

    def begin_read_only(self, cur):
        cur.execute("SET TRANSACTION READ ONLY")

    def ping(self, cur):
        cur.execute("SELECT 1 FROM dual")
        cur.fetchone()

    def fold(self, name):
        return name.upper()

    def quote(self, name):
        return '"' + name.upper().replace('"', '') + '"'

    def row_cap(self, sql, n):
        inner = sql.strip().rstrip(";").strip()
        return f"SELECT * FROM (\n{inner}\n) WHERE ROWNUM <= {int(n)}"

    def execute_guarded(self, conn, sql, capped, max_rows, cost_limit):
        import oracledb
        cur = conn.cursor()
        cost = None
        try:
            sid = "mcp_" + uuid4().hex[:16]
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {sql}")
            cur.execute("SELECT MAX(cost) FROM plan_table WHERE statement_id = :sid", sid=sid)
            row = cur.fetchone()
            cost = int(row[0]) if row and row[0] is not None else None
        except oracledb.DatabaseError as e:
            code = e.args[0].code if e.args else None
            if code in self._BAD_CODES:
                raise SqlNotAllowed(f"Query rejected: {e.args[0].message.strip()}") from e
        conn.rollback()
        if cost is not None and cost > cost_limit:
            raise SqlNotAllowed(
                f"Query rejected: estimated cost {cost} exceeds limit {cost_limit}. "
                f"Add a WHERE filter or narrow the query."
            )
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(capped)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        return {"columns": columns, "rows": [dict(zip(columns, r)) for r in rows],
                "row_count": len(rows), "explain_cost": cost}

    def sql_list_schemas(self):
        return "SELECT username FROM all_users ORDER BY username", {}

    def sql_list_tables(self, schema):
        return ("SELECT table_name AS name, 'TABLE' AS kind FROM all_tables WHERE owner = :o "
                "UNION ALL "
                "SELECT view_name AS name, 'VIEW' AS kind FROM all_views WHERE owner = :o "
                "ORDER BY name", {"o": schema})

    def sql_columns(self, schema, table):
        return ("SELECT c.column_name, c.data_type, c.data_length, c.nullable, cc.comments "
                "FROM all_tab_columns c "
                "LEFT JOIN all_col_comments cc ON cc.owner=c.owner AND cc.table_name=c.table_name "
                "AND cc.column_name=c.column_name "
                "WHERE c.owner = :o AND c.table_name = :t ORDER BY c.column_id",
                {"o": schema, "t": table})

    def sql_keys(self, schema, table):
        return ("SELECT ac.constraint_type, acc.column_name FROM all_constraints ac "
                "JOIN all_cons_columns acc ON acc.owner=ac.owner AND acc.constraint_name=ac.constraint_name "
                "WHERE ac.owner=:o AND ac.table_name=:t AND ac.constraint_type IN ('P','R')",
                {"o": schema, "t": table})

    def sql_search_objects(self, keyword, owners):
        binds: dict[str, Any] = {"kw": f"%{keyword.upper()}%"}
        owner_sql = ""
        if owners:
            owner_sql = " AND owner IN (" + ",".join(f":s{i}" for i in range(len(owners))) + ")"
            binds.update({f"s{i}": o for i, o in enumerate(owners)})
        # Two statements joined for the two result sets is handled by the caller;
        # here we return the objects query; columns query is built inline in tools.
        return ("SELECT owner, object_name AS name, object_type AS kind FROM all_objects "
                "WHERE UPPER(object_name) LIKE :kw AND object_type IN ('TABLE','VIEW')"
                + owner_sql + " ORDER BY owner, object_name FETCH FIRST 100 ROWS ONLY", binds)

    def sql_search_columns(self, keyword, owners):
        binds: dict[str, Any] = {"kw": f"%{keyword.upper()}%"}
        owner_sql = ""
        if owners:
            owner_sql = " AND owner IN (" + ",".join(f":s{i}" for i in range(len(owners))) + ")"
            binds.update({f"s{i}": o for i, o in enumerate(owners)})
        return ("SELECT owner, table_name, column_name FROM all_tab_columns "
                "WHERE UPPER(column_name) LIKE :kw" + owner_sql +
                " ORDER BY owner, table_name, column_name FETCH FIRST 100 ROWS ONLY", binds)

    def get_ddl(self, conn, schema, obj):
        cur = conn.cursor()
        self.begin_read_only(cur)
        cur.execute(
            "SELECT object_type FROM all_objects WHERE owner=:o AND object_name=:n "
            "AND object_type IN ('TABLE','VIEW','INDEX','SEQUENCE','MATERIALIZED VIEW') "
            "ORDER BY object_type FETCH FIRST 1 ROWS ONLY", {"o": schema, "n": obj})
        kind = cur.fetchone()
        if not kind:
            raise ValueError(f"Object {schema}.{obj} not found.")
        otype = kind[0].replace(" ", "_")
        cur.execute("SELECT DBMS_METADATA.GET_DDL(:ty, :n, :o) FROM dual",
                    {"ty": otype, "n": obj, "o": schema})
        row = cur.fetchone()
        return str(row[0]) if row else ""


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

class PostgresEngine(Engine):
    name = "postgres"
    dialect = "postgres"
    # SQLSTATE classes meaning "the query itself is wrong".
    _BAD_SQLSTATES = {"42P01", "42703", "42601", "42883", "42P02", "42704", "42846"}

    def connect(self, conn, user, password, timeout_s):
        import psycopg
        # Enforce read-only and a statement timeout at the session level via
        # startup options, so no in-transaction SET is needed (which would block
        # a later SET TRANSACTION READ ONLY).
        opts = f"-c default_transaction_read_only=on -c statement_timeout={max(1, timeout_s) * 1000}"
        return psycopg.connect(host=conn.host, port=conn.port, dbname=conn.db_name,
                               user=user, password=password, autocommit=False, options=opts)

    def begin_read_only(self, cur):
        pass  # already read-only via connection options

    def ping(self, cur):
        cur.execute("SELECT 1")
        cur.fetchone()

    def fold(self, name):
        return name.lower()

    def quote(self, name):
        return '"' + name.lower().replace('"', '') + '"'

    def row_cap(self, sql, n):
        inner = sql.strip().rstrip(";").strip()
        return f"SELECT * FROM (\n{inner}\n) AS _capped LIMIT {int(n)}"

    def _is_bad(self, exc) -> bool:
        return getattr(exc, "sqlstate", None) in self._BAD_SQLSTATES

    def execute_guarded(self, conn, sql, capped, max_rows, cost_limit):
        import psycopg
        cur = conn.cursor()
        cost = None
        try:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            plan = cur.fetchone()[0]
            if isinstance(plan, str):
                plan = json.loads(plan)
            cost = int(plan[0]["Plan"]["Total Cost"])
        except psycopg.Error as e:
            if self._is_bad(e):
                raise SqlNotAllowed(f"Query rejected: {str(e).strip()}") from e
            conn.rollback()  # reset the aborted transaction so we can continue
        if cost is not None and cost > cost_limit:
            raise SqlNotAllowed(
                f"Query rejected: estimated cost {cost} exceeds limit {cost_limit}. "
                f"Add a WHERE filter or narrow the query."
            )
        cur.execute(capped)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        return {"columns": columns, "rows": [dict(zip(columns, r)) for r in rows],
                "row_count": len(rows), "explain_cost": cost}

    def sql_list_schemas(self):
        return ("SELECT schema_name AS username FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg\\_%' AND schema_name <> 'information_schema' "
                "ORDER BY schema_name", {})

    def sql_list_tables(self, schema):
        return ("SELECT table_name AS name, "
                "CASE table_type WHEN 'VIEW' THEN 'VIEW' ELSE 'TABLE' END AS kind "
                "FROM information_schema.tables WHERE table_schema = %(o)s ORDER BY table_name",
                {"o": schema})

    def sql_columns(self, schema, table):
        return ("SELECT c.column_name, c.data_type, "
                "COALESCE(c.character_maximum_length, c.numeric_precision) AS data_length, "
                "CASE c.is_nullable WHEN 'YES' THEN 'Y' ELSE 'N' END AS nullable, "
                "pgd.description AS comments "
                "FROM information_schema.columns c "
                "LEFT JOIN pg_catalog.pg_statio_all_tables st "
                "  ON st.schemaname=c.table_schema AND st.relname=c.table_name "
                "LEFT JOIN pg_catalog.pg_description pgd "
                "  ON pgd.objoid=st.relid AND pgd.objsubid=c.ordinal_position "
                "WHERE c.table_schema=%(o)s AND c.table_name=%(t)s ORDER BY c.ordinal_position",
                {"o": schema, "t": table})

    def sql_keys(self, schema, table):
        return ("SELECT CASE tc.constraint_type WHEN 'PRIMARY KEY' THEN 'P' "
                "WHEN 'FOREIGN KEY' THEN 'R' END AS constraint_type, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name=tc.constraint_name AND kcu.table_schema=tc.table_schema "
                "WHERE tc.table_schema=%(o)s AND tc.table_name=%(t)s "
                "AND tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY')",
                {"o": schema, "t": table})

    def sql_search_objects(self, keyword, owners):
        binds: dict[str, Any] = {"kw": f"%{keyword.lower()}%"}
        owner_sql = ""
        if owners:
            owner_sql = " AND table_schema IN (" + ",".join(f"%(s{i})s" for i in range(len(owners))) + ")"
            binds.update({f"s{i}": o for i, o in enumerate(owners)})
        return ("SELECT table_schema AS owner, table_name AS name, "
                "CASE table_type WHEN 'VIEW' THEN 'VIEW' ELSE 'TABLE' END AS kind "
                "FROM information_schema.tables "
                "WHERE lower(table_name) LIKE %(kw)s AND table_type IN ('BASE TABLE','VIEW')"
                + owner_sql + " ORDER BY table_schema, table_name LIMIT 100", binds)

    def sql_search_columns(self, keyword, owners):
        binds: dict[str, Any] = {"kw": f"%{keyword.lower()}%"}
        owner_sql = ""
        if owners:
            owner_sql = " AND table_schema IN (" + ",".join(f"%(s{i})s" for i in range(len(owners))) + ")"
            binds.update({f"s{i}": o for i, o in enumerate(owners)})
        return ("SELECT table_schema AS owner, table_name, column_name "
                "FROM information_schema.columns "
                "WHERE lower(column_name) LIKE %(kw)s" + owner_sql +
                " ORDER BY table_schema, table_name, column_name LIMIT 100", binds)

    def get_ddl(self, conn, schema, obj):
        # Postgres has no DBMS_METADATA; synthesize a basic CREATE from the catalog.
        cur = conn.cursor()
        sql, binds = self.sql_columns(schema, obj)
        cur.execute(sql, binds)
        cols = cur.fetchall()
        if not cols:
            raise ValueError(f"Object {schema}.{obj} not found.")
        lines = [f'CREATE TABLE {self.quote(schema)}.{self.quote(obj)} (']
        defs = []
        for name, dtype, length, nullable, _c in cols:
            t = f"{dtype}({length})" if length else dtype
            defs.append(f'  {self.quote(name)} {t}{"" if nullable == "Y" else " NOT NULL"}')
        lines.append(",\n".join(defs))
        lines.append(");")
        return "\n".join(lines)
