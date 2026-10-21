import pytest

from oracle_mcp.config import ConnOverride
from oracle_mcp.security import (
    SchemaNotAllowed,
    check_schema,
    mask_rows,
    masked_names_for_table,
    sensitive_column_names,
)


def test_allowlist_empty_allows_all():
    check_schema(ConnOverride(schemas=[]), "ANYTHING")  # no raise


def test_allowlist_enforced():
    ov = ConnOverride(schemas=["APP", "REPORT"])
    check_schema(ov, "app")  # case-insensitive, no raise
    with pytest.raises(SchemaNotAllowed):
        check_schema(ov, "SECRET")


def test_masked_names_for_table():
    ov = ConnOverride(mask_columns=["APP.CUSTOMER.NATIONAL_ID", "APP.CUSTOMER.BANK_ACCT",
                                    "APP.OTHER.X"])
    assert masked_names_for_table(ov, "APP", "CUSTOMER") == {"NATIONAL_ID", "BANK_ACCT"}
    assert masked_names_for_table(ov, "APP", "OTHER") == {"X"}
    assert masked_names_for_table(ov, "APP", "NONE") == set()


def test_sensitive_column_names():
    ov = ConnOverride(mask_columns=["APP.CUSTOMER.NATIONAL_ID", "APP.OTHER.NATIONAL_ID"])
    assert sensitive_column_names(ov) == {"NATIONAL_ID"}


def test_mask_rows():
    rows = [{"ID": 1, "NATIONAL_ID": "1234567890123"},
            {"ID": 2, "NATIONAL_ID": "9999999999999"}]
    out = mask_rows(rows, {"NATIONAL_ID"})
    assert out[0]["ID"] == 1
    assert out[0]["NATIONAL_ID"] == "***"
    assert out[1]["NATIONAL_ID"] == "***"


def test_mask_rows_noop_when_empty():
    rows = [{"ID": 1}]
    assert mask_rows(rows, set()) == rows
