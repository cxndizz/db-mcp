import pytest

from oracle_mcp.config import ConnOverride
from oracle_mcp.security import (
    SchemaNotAllowed,
    check_schema,
    columns_to_mask,
    is_pii_name,
    mask_rows,
    masked_names_for_table,
    sensitive_column_names,
)


@pytest.mark.parametrize("col", [
    "BORROWER_CID", "FATHER_CID", "SPOUSE_NATIONAL_ID", "BORROWER_PASSPORT",
    "BORROWER_FIRST_NAME", "MOTHER_LAST_NAME", "BORROWER_DOB", "BORROWER_EMAIL",
    "BORROWER_PHONE", "SPOUSE_MOBILE", "BORROWER_YEARLY_INCOME", "FATHER_INCOME",
    "BANK_ACCOUNT", "BORROWER_ADDRESS_FULL", "BORROWER_ADDRESS_ZIPCODE",
])
def test_pii_names_detected(col):
    assert is_pii_name(col) is True


@pytest.mark.parametrize("col", [
    "REG_ID", "STATUS", "MAJOR_NAME", "EEF_SCHOOL_NAME", "UNIV_ID",
    "FATHER_NATIONALITY_TYPE", "SPOUSE_INCOME_TYPE", "EMAIL_STATUS",
    "BORROWER_WORK_STATUS", "CREATED_DATE", "LOAN_TUITION_TERM1",
])
def test_non_pii_names_not_detected(col):
    assert is_pii_name(col) is False


def test_columns_to_mask_auto():
    cols = ["REG_ID", "BORROWER_CID", "STATUS", "BORROWER_EMAIL"]
    rows = [{"REG_ID": 1, "BORROWER_CID": "1100700000000", "STATUS": "APPROVED",
             "BORROWER_EMAIL": "a@b.com"}]
    masked = columns_to_mask(cols, rows, ConnOverride())
    assert masked == {"BORROWER_CID", "BORROWER_EMAIL"}


def test_columns_to_mask_content_fallback():
    # Oddly-named column whose values look like national IDs.
    cols = ["X_FIELD"]
    rows = [{"X_FIELD": "1100700000000"}, {"X_FIELD": "1209800000000"}]
    assert columns_to_mask(cols, rows, ConnOverride()) == {"X_FIELD"}


def test_unmask_exempts_false_positive():
    cols = ["BORROWER_ADDRESS_PROVINCE"]
    rows = [{"BORROWER_ADDRESS_PROVINCE": "Bangkok"}]
    ov = ConnOverride(unmask_columns=["BORROWER_ADDRESS_PROVINCE"])
    assert columns_to_mask(cols, rows, ov) == set()


def test_forced_mask_column():
    cols = ["SECRET_SCORE"]
    rows = [{"SECRET_SCORE": 42}]
    ov = ConnOverride(mask_columns=["APP.T.SECRET_SCORE"])
    assert columns_to_mask(cols, rows, ov) == {"SECRET_SCORE"}


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
