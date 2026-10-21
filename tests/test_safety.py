import pytest

from oracle_mcp.safety import (
    SqlNotAllowed,
    parse_single_select,
    referenced_tables,
)


def test_plain_select_ok():
    parse_single_select("SELECT * FROM emp WHERE deptno = 10")


def test_with_cte_ok():
    parse_single_select("WITH x AS (SELECT 1 c FROM dual) SELECT c FROM x")


def test_union_ok():
    parse_single_select("SELECT 1 FROM dual UNION ALL SELECT 2 FROM dual")


@pytest.mark.parametrize("sql", [
    "UPDATE emp SET sal = 0",
    "DELETE FROM emp",
    "INSERT INTO emp (id) VALUES (1)",
    "DROP TABLE emp",
    "ALTER TABLE emp ADD x NUMBER",
    "CREATE TABLE t (x NUMBER)",
    "TRUNCATE TABLE emp",
    "MERGE INTO emp e USING dual ON (1=1) WHEN MATCHED THEN UPDATE SET sal=1",
    "GRANT SELECT ON emp TO scott",
])
def test_dml_ddl_rejected(sql):
    with pytest.raises(SqlNotAllowed):
        parse_single_select(sql)


@pytest.mark.parametrize("sql", [
    "BEGIN NULL; END;",
    "DECLARE x NUMBER; BEGIN x := 1; END;",
])
def test_plsql_rejected(sql):
    with pytest.raises(SqlNotAllowed):
        parse_single_select(sql)


def test_multi_statement_rejected():
    with pytest.raises(SqlNotAllowed):
        parse_single_select("SELECT 1 FROM dual; SELECT 2 FROM dual")


def test_empty_rejected():
    with pytest.raises(SqlNotAllowed):
        parse_single_select("   ")


def test_for_update_rejected():
    with pytest.raises(SqlNotAllowed):
        parse_single_select("SELECT * FROM emp FOR UPDATE")


def test_garbage_rejected():
    with pytest.raises(SqlNotAllowed):
        parse_single_select("SELEKT nonsense FRM")


def test_trailing_semicolon_ok():
    parse_single_select("SELECT 1 FROM dual;")


def test_referenced_tables_excludes_cte():
    stmt = parse_single_select(
        "WITH x AS (SELECT * FROM scott.emp) SELECT * FROM x JOIN dept ON x.d=dept.d"
    )
    tables = referenced_tables(stmt)
    names = {t for (_s, t) in tables}
    assert "EMP" in names
    assert "DEPT" in names
    assert "X" not in names


def test_postgres_dialect_parses():
    # LIMIT is valid in the postgres dialect.
    parse_single_select("SELECT * FROM emp LIMIT 10", dialect="postgres")
