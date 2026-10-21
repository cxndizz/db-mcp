import pytest

from oracle_mcp.engines import OracleEngine, PostgresEngine, get_engine


def test_get_engine():
    assert isinstance(get_engine("oracle"), OracleEngine)
    assert isinstance(get_engine("postgres"), PostgresEngine)
    assert isinstance(get_engine("postgresql"), PostgresEngine)
    assert isinstance(get_engine(""), OracleEngine)  # default
    with pytest.raises(ValueError):
        get_engine("mysql")


def test_dialects():
    assert get_engine("oracle").dialect == "oracle"
    assert get_engine("postgres").dialect == "postgres"


def test_row_cap_oracle():
    capped = OracleEngine().row_cap("SELECT * FROM emp", 50)
    assert "ROWNUM <= 50" in capped
    assert capped.strip().upper().startswith("SELECT * FROM (")


def test_row_cap_postgres():
    capped = PostgresEngine().row_cap("SELECT * FROM emp", 50)
    assert capped.strip().endswith("LIMIT 50")
    assert "ROWNUM" not in capped


def test_fold_and_quote():
    o, p = OracleEngine(), PostgresEngine()
    assert o.fold("emp") == "EMP"
    assert p.fold("EMP") == "emp"
    assert o.quote("emp") == '"EMP"'
    assert p.quote("EMP") == '"emp"'


def test_metadata_sql_aliases():
    # Column aliases must match the keys the tools read (upper-cased in db layer).
    o = OracleEngine()
    assert "AS name" in o.sql_list_tables("APP")[0]
    assert "AS kind" in o.sql_list_tables("APP")[0]
    p = PostgresEngine()
    pcols, pbinds = p.sql_columns("public", "emp")
    assert "AS nullable" in pcols and "AS data_length" in pcols
    assert pbinds == {"o": "public", "t": "emp"}


def test_postgres_bad_sqlstate():
    p = PostgresEngine()

    class E(Exception):
        sqlstate = "42P01"  # undefined_table

    assert p._is_bad(E()) is True

    class Ok(Exception):
        sqlstate = "53300"  # too_many_connections — not a query error

    assert p._is_bad(Ok()) is False
