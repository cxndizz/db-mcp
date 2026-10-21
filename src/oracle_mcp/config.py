"""Runtime configuration and per-connection overrides.

Two kinds of configuration:

* Global runtime settings (row caps, timeout, audit path) come from environment
  variables / ``.env`` with safe defaults.
* Per-connection overrides (username for DBeaver connections, schema allowlist,
  masked columns) are stored in a small local JSON file that holds no secrets.
  Passwords never live here — they are in the keyring.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import APP_NAME

load_dotenv()


# ---------------------------------------------------------------------------
# Global runtime settings
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    max_rows: int = int(os.environ.get("ORACLE_MAX_ROWS", "200"))
    hard_max_rows: int = int(os.environ.get("ORACLE_HARD_MAX_ROWS", "5000"))
    query_timeout: int = int(os.environ.get("ORACLE_QUERY_TIMEOUT", "30"))  # seconds
    explain_cost_limit: int = int(os.environ.get("ORACLE_EXPLAIN_COST_LIMIT", "1000000"))
    max_concurrency: int = int(os.environ.get("ORACLE_MAX_CONCURRENCY", "4"))
    auto_mask: bool = os.environ.get("ORACLE_AUTO_MASK", "1").lower() not in ("0", "false", "no")
    audit_log_path: str = os.environ.get("AUDIT_LOG_PATH", str(Path.cwd() / "logs" / "audit.jsonl"))
    http_token: str | None = os.environ.get("MCP_HTTP_TOKEN") or None


SETTINGS = Settings()


# ---------------------------------------------------------------------------
# Per-connection overrides (no secrets)
# ---------------------------------------------------------------------------

def _overrides_path() -> Path:
    override = os.environ.get("ORACLE_MCP_CONFIG")
    if override:
        return Path(override)
    base = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / APP_NAME
    return base / "overrides.json"


@dataclass
class ConnOverride:
    username: str | None = None
    schemas: list[str] = field(default_factory=list)       # allowlist; empty => all visible
    mask_columns: list[str] = field(default_factory=list)  # force-mask: "SCHEMA.TABLE.COLUMN" or "COLUMN"
    unmask_columns: list[str] = field(default_factory=list)  # exempt a column name from auto-masking

    @classmethod
    def from_dict(cls, d: dict) -> "ConnOverride":
        return cls(
            username=d.get("username"),
            schemas=[s.upper() for s in d.get("schemas", [])],
            mask_columns=[c.upper() for c in d.get("mask_columns", [])],
            unmask_columns=[c.upper() for c in d.get("unmask_columns", [])],
        )

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "schemas": self.schemas,
            "mask_columns": self.mask_columns,
            "unmask_columns": self.unmask_columns,
        }


def _load_all() -> dict:
    path = _overrides_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_override(conn_id: str) -> ConnOverride:
    return ConnOverride.from_dict(_load_all().get(conn_id, {}))


def save_override(conn_id: str, override: ConnOverride) -> None:
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_all()
    data[conn_id] = override.to_dict()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
