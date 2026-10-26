"""MCP tool definitions. Each tool enforces the schema allowlist, applies
automatic column masking to results, and writes an audit record. All
engine-specific SQL lives in ``engines``; these tools stay database-agnostic.
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
    columns_to_mask,
    is_pii_name,
    mask_rows,
    sensitive_column_names,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


def _ident(name: str, what: str) -> str:
    """Validate an identifier (charset only); engines fold/quote for their dialect."""
    if not name or not _IDENT.match(name):
        raise ValueError(f"Invalid {what}: {name!r}")
    return name


def _allowed_schemas(selector: str) -> list[str] | None:
    try:
        conn = db._resolve_conn(selector)
    except Exception:
        return None
    ov = get_override(conn.id)
    return ov.schemas if ov.schemas else None


def register(mcp) -> None:
    @mcp.tool
    def list_connections() -> list[dict[str, Any]]:
        """List database connections discovered from DBeaver / SQL Developer
        (Oracle) and from the server-config file (Oracle or PostgreSQL).

        Shows each connection's id, engine, host/port/database, source, whether a
        username is known, and whether a password is stored. Use an id or name as
        the `connection` argument of other tools.
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
    def list_schemas(connection: str = "") -> list[str]:
        """List schemas (owners) visible on a connection, limited to the allowlist
        if one is configured."""
        allow = _allowed_schemas(connection)
        if allow:
            result = list(allow)
        else:
            eng = db.engine_for(connection)
            rows = db.meta_query(connection, *eng.sql_list_schemas())
            result = [r["USERNAME"] for r in rows]
        audit(SETTINGS.audit_log_path, {"tool": "list_schemas", "connection": connection,
                                        "row_count": len(result)})
        return result

    @mcp.tool
    def list_tables(schema: str, connection: str = "") -> list[dict[str, str]]:
        """List tables and views in a schema."""
        _ident(schema, "schema")
        eng = db.engine_for(connection)
        check_schema(get_override(db.resolve(connection)[0].id), schema)
        rows = db.meta_query(connection, *eng.sql_list_tables(eng.fold(schema)))
        audit(SETTINGS.audit_log_path, {"tool": "list_tables", "connection": connection,
                                        "schema": schema, "row_count": len(rows)})
        return rows

    @mcp.tool
    def describe_table(table: str, schema: str, connection: str = "") -> dict[str, Any]:
        """Describe a table/view: columns, types, nullability, PK/FK, and comments.
        Columns that look like personal data are flagged `masked`."""
        _ident(schema, "schema")
        _ident(table, "table")
        eng = db.engine_for(connection)
        ov = get_override(db.resolve(connection)[0].id)
        check_schema(ov, schema)
        schema_f, table_f = eng.fold(schema), eng.fold(table)
        cols = db.meta_query(connection, *eng.sql_columns(schema_f, table_f))
        keys = db.meta_query(connection, *eng.sql_keys(schema_f, table_f))
        pk = {k["COLUMN_NAME"] for k in keys if k["CONSTRAINT_TYPE"] == "P"}
        fk = {k["COLUMN_NAME"] for k in keys if k["CONSTRAINT_TYPE"] == "R"}
        forced = {n.upper() for n in sensitive_column_names(ov)}
        unmask = {u.upper() for u in ov.unmask_columns}
        for c in cols:
            name = c["COLUMN_NAME"]
            c["is_pk"] = name in pk
            c["is_fk"] = name in fk
            c["masked"] = (name.upper() not in unmask) and (
                name.upper() in forced or (SETTINGS.auto_mask and is_pii_name(name))
            )
        audit(SETTINGS.audit_log_path, {"tool": "describe_table", "connection": connection,
                                        "schema": schema, "table": table})
        return {"schema": schema, "table": table, "columns": cols}

    @mcp.tool
    def get_object_ddl(object_name: str, schema: str, connection: str = "") -> str:
        """Return the CREATE DDL for a table/view (DBMS_METADATA on Oracle; a
        catalog-derived CREATE on PostgreSQL)."""
        _ident(schema, "schema")
        _ident(object_name, "object")
        check_schema(get_override(db.resolve(connection)[0].id), schema)
        ddl = db.get_ddl(connection, schema, object_name)
        audit(SETTINGS.audit_log_path, {"tool": "get_object_ddl", "connection": connection,
                                        "schema": schema, "object": object_name})
        return ddl

    @mcp.tool
    def search_objects(keyword: str, connection: str = "") -> dict[str, list[dict[str, str]]]:
        """Search for tables/views and columns whose name contains `keyword`."""
        eng = db.engine_for(connection)
        owners = _allowed_schemas(connection)
        owners_f = [eng.fold(o) for o in owners] if owners else None
        objs = db.meta_query(connection, *eng.sql_search_objects(keyword, owners_f))
        cols = db.meta_query(connection, *eng.sql_search_columns(keyword, owners_f))
        audit(SETTINGS.audit_log_path, {"tool": "search_objects", "connection": connection,
                                        "keyword": keyword})
        return {"objects": objs, "columns": cols}

    @mcp.tool
    def sample_table(table: str, schema: str, connection: str = "", limit: int = 20) -> dict[str, Any]:
        """Preview up to `limit` rows from a table (masked). Great for seeing shape."""
        _ident(schema, "schema")
        _ident(table, "table")
        eng = db.engine_for(connection)
        ov = get_override(db.resolve(connection)[0].id)
        check_schema(ov, schema)
        limit = max(1, min(int(limit), SETTINGS.max_rows))
        sql = f"SELECT * FROM {eng.quote(schema)}.{eng.quote(table)}"
        result = db.run_select(connection, sql, limit)
        result["rows"] = mask_rows(result["rows"], columns_to_mask(result["columns"], result["rows"], ov))
        audit(SETTINGS.audit_log_path, {"tool": "sample_table", "connection": connection,
                                        "schema": schema, "table": table,
                                        "row_count": result["row_count"]})
        return result

    @mcp.tool
    def run_query(sql: str, connection: str = "", max_rows: int = 200) -> dict[str, Any]:
        """Run a read-only SELECT query and return the rows.

        Only a single SELECT/WITH statement is allowed. The query is checked with
        EXPLAIN first (rejecting unknown tables/columns and very expensive plans),
        a row cap is always applied, and the session is read-only. Columns that
        look like personal data are masked in the result.
        """
        ov = get_override(db.resolve(connection)[0].id)
        try:
            result = db.run_select(connection, sql, max_rows)
        except (SqlNotAllowed, SchemaNotAllowed) as e:
            audit(SETTINGS.audit_log_path, {"tool": "run_query", "connection": connection,
                                            "sql": sql, "rejected": str(e)})
            raise
        result["rows"] = mask_rows(result["rows"], columns_to_mask(result["columns"], result["rows"], ov))
        audit(SETTINGS.audit_log_path, {"tool": "run_query", "connection": connection,
                                        "sql": sql, "row_count": result["row_count"],
                                        "explain_cost": result.get("explain_cost")})
        return result
