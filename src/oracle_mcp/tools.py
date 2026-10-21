"""MCP tool definitions. Each tool enforces the schema allowlist, applies column
masking to results, and writes an audit record.
"""

from __future__ import annotations

import re
from typing import Any

from . import db, keyvault
from .config import SETTINGS, get_override
from .discovery import discover
from .safety import SqlNotAllowed
from .security import (
    SchemaNotAllowed,
    audit,
    check_schema,
    mask_rows,
    masked_names_for_table,
    sensitive_column_names,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


def _ident(name: str, what: str) -> str:
    """Validate an Oracle identifier and return it uppercased."""
    if not name or not _IDENT.match(name):
        raise ValueError(f"Invalid {what}: {name!r}")
    return name.upper()


def _allowed_schemas(selector: str) -> list[str] | None:
    ov = get_override(db.find(selector).id) if db.find(selector) else None
    return ov.schemas if ov and ov.schemas else None


def register(mcp) -> None:
    @mcp.tool
    def list_connections() -> list[dict[str, Any]]:
        """List Oracle connections discovered from DBeaver and SQL Developer.

        Shows each connection's id, friendly names, host/port/database, the
        source app(s), whether a username is known, and whether a password is
        stored. Use an id or name as the `connection` argument of other tools.
        """
        out = []
        for c in discover():
            d = c.to_public_dict()
            ov = get_override(c.id)
            d["username"] = c.username or ov.username
            d["has_password"] = keyvault.has_password(c.id)
            d["ready"] = bool(d["username"]) and d["has_password"]
            out.append(d)
        return out

    @mcp.tool
    def list_schemas(connection: str) -> list[str]:
        """List schemas (owners) visible on a connection, limited to the allowlist
        if one is configured."""
        allow = _allowed_schemas(connection)
        if allow:
            result = list(allow)
        else:
            rows = db.meta_query(connection, "SELECT username FROM all_users ORDER BY username")
            result = [r["USERNAME"] for r in rows]
        audit(SETTINGS.audit_log_path, {"tool": "list_schemas", "connection": connection,
                                        "row_count": len(result)})
        return result

    @mcp.tool
    def list_tables(schema: str, connection: str) -> list[dict[str, str]]:
        """List tables and views in a schema."""
        schema_u = _ident(schema, "schema")
        check_schema(get_override(db.resolve(connection)[0].id), schema_u)
        rows = db.meta_query(
            connection,
            "SELECT table_name AS name, 'TABLE' AS kind FROM all_tables WHERE owner = :o "
            "UNION ALL "
            "SELECT view_name AS name, 'VIEW' AS kind FROM all_views WHERE owner = :o "
            "ORDER BY name",
            {"o": schema_u},
        )
        audit(SETTINGS.audit_log_path, {"tool": "list_tables", "connection": connection,
                                        "schema": schema_u, "row_count": len(rows)})
        return rows

    @mcp.tool
    def describe_table(table: str, schema: str, connection: str) -> dict[str, Any]:
        """Describe a table/view: columns, types, nullability, PK/FK, and comments.
        Sensitive columns are reported but their sample values are masked elsewhere."""
        schema_u = _ident(schema, "schema")
        table_u = _ident(table, "table")
        check_schema(get_override(db.resolve(connection)[0].id), schema_u)
        cols = db.meta_query(
            connection,
            "SELECT c.column_name, c.data_type, c.data_length, c.nullable, "
            "cc.comments "
            "FROM all_tab_columns c "
            "LEFT JOIN all_col_comments cc "
            "  ON cc.owner=c.owner AND cc.table_name=c.table_name AND cc.column_name=c.column_name "
            "WHERE c.owner = :o AND c.table_name = :t ORDER BY c.column_id",
            {"o": schema_u, "t": table_u},
        )
        keys = db.meta_query(
            connection,
            "SELECT ac.constraint_type, acc.column_name "
            "FROM all_constraints ac "
            "JOIN all_cons_columns acc ON acc.owner=ac.owner AND acc.constraint_name=ac.constraint_name "
            "WHERE ac.owner=:o AND ac.table_name=:t AND ac.constraint_type IN ('P','R')",
            {"o": schema_u, "t": table_u},
        )
        pk = {k["COLUMN_NAME"] for k in keys if k["CONSTRAINT_TYPE"] == "P"}
        fk = {k["COLUMN_NAME"] for k in keys if k["CONSTRAINT_TYPE"] == "R"}
        masked = masked_names_for_table(get_override(db.resolve(connection)[0].id), schema_u, table_u)
        for c in cols:
            c["is_pk"] = c["COLUMN_NAME"] in pk
            c["is_fk"] = c["COLUMN_NAME"] in fk
            c["masked"] = c["COLUMN_NAME"] in masked
        audit(SETTINGS.audit_log_path, {"tool": "describe_table", "connection": connection,
                                        "schema": schema_u, "table": table_u})
        return {"schema": schema_u, "table": table_u, "columns": cols}

    @mcp.tool
    def get_object_ddl(object_name: str, schema: str, connection: str) -> str:
        """Return the CREATE DDL for a table/view/other object via DBMS_METADATA."""
        schema_u = _ident(schema, "schema")
        obj_u = _ident(object_name, "object")
        check_schema(get_override(db.resolve(connection)[0].id), schema_u)
        kind = db.meta_query(
            connection,
            "SELECT object_type FROM all_objects WHERE owner=:o AND object_name=:n "
            "AND object_type IN ('TABLE','VIEW','INDEX','SEQUENCE','MATERIALIZED VIEW') "
            "ORDER BY object_type FETCH FIRST 1 ROWS ONLY",
            {"o": schema_u, "n": obj_u},
        )
        if not kind:
            raise ValueError(f"Object {schema_u}.{obj_u} not found.")
        otype = kind[0]["OBJECT_TYPE"].replace(" ", "_")
        rows = db.meta_query(
            connection,
            "SELECT DBMS_METADATA.GET_DDL(:ty, :n, :o) AS ddl FROM dual",
            {"ty": otype, "n": obj_u, "o": schema_u},
        )
        audit(SETTINGS.audit_log_path, {"tool": "get_object_ddl", "connection": connection,
                                        "schema": schema_u, "object": obj_u})
        return str(rows[0]["DDL"]) if rows else ""

    @mcp.tool
    def search_objects(keyword: str, connection: str) -> dict[str, list[dict[str, str]]]:
        """Search for tables/views and columns whose name contains `keyword`."""
        kw = f"%{keyword.upper()}%"
        allow = _allowed_schemas(connection)
        owner_filter = ""
        binds: dict[str, Any] = {"kw": kw}
        if allow:
            owner_filter = " AND owner IN (" + ",".join(f":s{i}" for i in range(len(allow))) + ")"
            binds.update({f"s{i}": s for i, s in enumerate(allow)})
        objs = db.meta_query(
            connection,
            "SELECT owner, object_name AS name, object_type AS kind FROM all_objects "
            "WHERE UPPER(object_name) LIKE :kw AND object_type IN ('TABLE','VIEW')"
            + owner_filter + " ORDER BY owner, object_name FETCH FIRST 100 ROWS ONLY",
            binds,
        )
        cols = db.meta_query(
            connection,
            "SELECT owner, table_name, column_name FROM all_tab_columns "
            "WHERE UPPER(column_name) LIKE :kw" + owner_filter
            + " ORDER BY owner, table_name, column_name FETCH FIRST 100 ROWS ONLY",
            binds,
        )
        audit(SETTINGS.audit_log_path, {"tool": "search_objects", "connection": connection,
                                        "keyword": keyword})
        return {"objects": objs, "columns": cols}

    @mcp.tool
    def sample_table(table: str, schema: str, connection: str, limit: int = 20) -> dict[str, Any]:
        """Preview up to `limit` rows from a table (masked). Great for seeing shape."""
        schema_u = _ident(schema, "schema")
        table_u = _ident(table, "table")
        ov = get_override(db.resolve(connection)[0].id)
        check_schema(ov, schema_u)
        limit = max(1, min(int(limit), SETTINGS.max_rows))
        result = db.run_select(connection, f'SELECT * FROM "{schema_u}"."{table_u}"', limit)
        result["rows"] = mask_rows(result["rows"], masked_names_for_table(ov, schema_u, table_u))
        audit(SETTINGS.audit_log_path, {"tool": "sample_table", "connection": connection,
                                        "schema": schema_u, "table": table_u,
                                        "row_count": result["row_count"]})
        return result

    @mcp.tool
    def run_query(sql: str, connection: str, max_rows: int = 200) -> dict[str, Any]:
        """Run a read-only SELECT query and return the rows.

        Only a single SELECT/WITH statement is allowed. The query is checked with
        EXPLAIN PLAN first (rejecting unknown tables/columns and very expensive
        plans), a row cap is always applied, and the session is read-only.
        Sensitive columns are masked in the result.
        """
        ov = get_override(db.resolve(connection)[0].id)
        err = None
        try:
            result = db.run_select(connection, sql, max_rows)
        except (SqlNotAllowed, SchemaNotAllowed) as e:
            err = str(e)
            audit(SETTINGS.audit_log_path, {"tool": "run_query", "connection": connection,
                                            "sql": sql, "rejected": err})
            raise
        result["rows"] = mask_rows(result["rows"], sensitive_column_names(ov))
        audit(SETTINGS.audit_log_path, {"tool": "run_query", "connection": connection,
                                        "sql": sql, "row_count": result["row_count"],
                                        "explain_cost": result.get("explain_cost")})
        return result
