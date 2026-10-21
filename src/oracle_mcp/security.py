"""Data-protection layer: schema allowlist, column masking, and audit logging."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import ConnOverride

MASK_VALUE = "***"


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
