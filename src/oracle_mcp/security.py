"""Data-protection layer: schema allowlist, automatic column masking, and audit
logging.

Masking is automatic: the server inspects each returned column's name (and, as a
fallback, its values) and masks the ones that look like personal data, so no
per-column list has to be maintained. A connection may still force extra columns
to mask (``mask_columns``) or exempt false positives (``unmask_columns``).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import SETTINGS, ConnOverride

MASK_VALUE = "***"

# --- automatic PII detection by column name -------------------------------
# Strong identifiers: masked even when a "safe" word is also present.
_PII_NAME = re.compile(
    r"(CID|NATIONAL_?ID|CITIZEN_?ID|PASSPORT|TAX_?ID|"
    r"FIRST_?NAME|LAST_?NAME|FULL_?NAME|MIDDLE_?NAME|SURNAME|FNAME|LNAME|"
    r"DOB|BIRTH|"
    r"EMAIL|PHONE|MOBILE|FAX|"
    r"INCOME|SALARY|"
    r"BANK_?ACC|ACCOUNT_?NO|ACCT_?NO|IBAN|CARD_?NO|"
    r"ADDRESS|ZIPCODE|POSTAL)",
    re.IGNORECASE,
)
# Institution / category columns that merely contain a PII-ish word.
_SAFE_NAME = re.compile(
    r"(UNIV|INSTITUT|SCHOOL_?NAME|MAJOR_?NAME|PROG|FACULTY|"
    r"BANK_?NAME|BRANCH_?NAME|COMPANY_?NAME|ORG_?NAME|FILE_?NAME|"
    r"_TYPE|_CODE|_DESC|_FLAG|_STATUS|NATIONALITY)",
    re.IGNORECASE,
)
# Content fallback for oddly-named columns.
_THAI_CID = re.compile(r"^\d{13}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_pii_name(column: str) -> bool:
    """Heuristic: does this column name denote personal data?

    Masks names carrying a PII token (CID, EMAIL, ADDRESS, INCOME, …) but exempts
    category/lookup columns that merely contain such a word — e.g. ``INCOME_TYPE``,
    ``EMAIL_STATUS``, ``MAJOR_NAME``, ``NATIONALITY_TYPE``.
    """
    return bool(_PII_NAME.search(column)) and not bool(_SAFE_NAME.search(column))


def _looks_like_pii_values(values: Sequence[Any]) -> bool:
    """Content fallback: a column whose non-null values look like CIDs or emails."""
    seen = 0
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        seen += 1
        if _THAI_CID.match(s) or _EMAIL.match(s):
            return True
        if seen >= 25:  # sample enough, then stop
            break
    return False


def columns_to_mask(
    columns: Sequence[str],
    rows: Sequence[dict[str, Any]],
    override: ConnOverride,
) -> set[str]:
    """Decide which returned columns to mask: auto-detected ∪ forced − exempted."""
    unmask = {u.upper() for u in override.unmask_columns}
    forced = {n.upper() for n in sensitive_column_names(override)}
    result: set[str] = set()
    for col in columns:
        cu = col.upper()
        if cu in unmask:
            continue
        if cu in forced:
            result.add(col)
            continue
        if SETTINGS.auto_mask and is_pii_name(col):
            result.add(col)
            continue
        if SETTINGS.auto_mask and _looks_like_pii_values([r.get(col) for r in rows]):
            result.add(col)
    return result


class SchemaNotAllowed(PermissionError):
    """Raised when a request targets a schema outside the allowlist."""


def check_schema(override: ConnOverride, schema: str) -> None:
    """Allow all schemas when the allowlist is empty; otherwise enforce it."""
    if not override.schemas:
        return
    if schema.upper() not in override.schemas:
        raise SchemaNotAllowed(
            f"Schema '{schema}' is not in the allowlist for this connection "
            f"({', '.join(override.schemas)})."
        )


def masked_names_for_table(override: ConnOverride, schema: str, table: str) -> set[str]:
    """Columns to mask for a specific SCHEMA.TABLE."""
    prefix = f"{schema.upper()}.{table.upper()}."
    return {entry.split(".")[-1] for entry in override.mask_columns if entry.startswith(prefix)}


def sensitive_column_names(override: ConnOverride) -> set[str]:
    """All masked column names regardless of table.

    Used for ad-hoc ``run_query`` results where a column's origin table is not
    reliably known: we err on the side of over-masking.
    """
    return {entry.split(".")[-1] for entry in override.mask_columns if "." in entry}


def mask_rows(
    rows: Sequence[dict[str, Any]],
    mask_names: set[str],
) -> list[dict[str, Any]]:
    """Return rows with any column whose name is in ``mask_names`` replaced."""
    if not mask_names:
        return list(rows)
    upper = {n.upper() for n in mask_names}
    out = []
    for row in rows:
        out.append({k: (MASK_VALUE if k.upper() in upper else v) for k, v in row.items()})
    return out


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()


def audit(path: str, event: dict[str, Any]) -> None:
    """Append one JSON line describing a query/tool call. Never raises."""
    try:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _audit_lock:
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass
