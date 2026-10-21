"""Discover Oracle connections saved in DBeaver and SQL Developer.

We read ONLY the non-secret connection definition (host / port / SID or service
/ username when present). We never read or decrypt the password that either app
stores — the password is supplied once by the user and kept in the OS keyring
(see ``keyvault``).

DBeaver stores its definitions in ``data-sources.json`` but keeps the username
in a separate encrypted file, so DBeaver connections usually have no username
here; the user provides it at ``set-password`` time. SQL Developer keeps the
username in ``connections.json`` so it is available directly.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Connection:
    """A connection target (no secrets). Discovered from DBeaver/SQL Developer
    (Oracle) or declared in a server-config file (Oracle or PostgreSQL)."""

    host: str
    port: int
    db_name: str          # SID or service name (Oracle) / database name (Postgres)
    is_service: bool      # Oracle: service name vs SID. Postgres: always True.
    username: str | None = None
    engine: str = "oracle"  # "oracle" | "postgres"
    sources: set[str] = field(default_factory=set)   # {"dbeaver", "sqldev", "config"}
    names: set[str] = field(default_factory=set)      # friendly names

    @property
    def id(self) -> str:
        """Stable, human-readable id shared across apps that point at the same DB.

        Mirrors Oracle easy-connect/SID syntax: ``host:port/service`` or
        ``host:port:sid``. Two apps pointing at the same target dedupe to one id,
        so one keyring password entry covers both.
        """
        sep = "/" if self.is_service else ":"
        return f"{self.host}:{self.port}{sep}{self.db_name}"

    def matches(self, selector: str) -> bool:
        """True if ``selector`` is this connection's id or one of its app names."""
        sel = selector.strip().lower()
        if sel == self.id.lower():
            return True
        return any(sel == n.lower() for n in self.names)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "names": sorted(self.names),
            "engine": self.engine,
            "host": self.host,
            "port": self.port,
            "db_name": self.db_name,
            "db_kind": "service" if self.is_service else "sid",
            "username": self.username,
            "sources": sorted(self.sources),
        }


# ---------------------------------------------------------------------------
# Store locations
# ---------------------------------------------------------------------------

def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))


def dbeaver_store_paths() -> list[Path]:
    """All DBeaver ``data-sources.json`` files (supports multiple workspaces)."""
    override = os.environ.get("DBEAVER_WORKSPACE")
    if override:
        root = Path(override)
        hits = [root / "General" / ".dbeaver" / "data-sources.json",
                root / ".dbeaver" / "data-sources.json",
                root / "data-sources.json"]
        return [p for p in hits if p.is_file()]
    base = _appdata() / "DBeaverData"
    pattern = str(base / "**" / ".dbeaver" / "data-sources.json")
    return [Path(p) for p in glob.glob(pattern, recursive=True)]


def sqldev_store_paths() -> list[Path]:
    """All SQL Developer ``connections.json`` files (newest system dir wins)."""
    override = os.environ.get("SQLDEV_SYSTEM_DIR")
    if override:
        p = Path(override) / "o.jdeveloper.db.connection" / "connections.json"
        return [p] if p.is_file() else []
    base = _appdata() / "SQL Developer"
    pattern = str(base / "system*" / "o.jdeveloper.db.connection" / "connections.json")
    paths = [Path(p) for p in glob.glob(pattern)]
    # Newest system version last so it overrides older duplicates on merge.
    paths.sort(key=lambda p: p.parent.parent.name)
    return paths


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_URL_SID = re.compile(r"@(?://)?(?P<host>[^:/]+):(?P<port>\d+):(?P<name>[\w$.]+)")
_URL_SERVICE = re.compile(r"@//(?P<host>[^:/]+):(?P<port>\d+)/(?P<name>[\w$.]+)")


def _parse_jdbc_url(url: str) -> tuple[str, int, str, bool] | None:
    """Return (host, port, name, is_service) from an Oracle thin JDBC url."""
    if not url:
        return None
    m = _URL_SERVICE.search(url)
    if m:
        return m["host"], int(m["port"]), m["name"], True
    m = _URL_SID.search(url)
    if m:
        return m["host"], int(m["port"]), m["name"], False
    return None


def parse_dbeaver(data: dict) -> list[Connection]:
    conns: list[Connection] = []
    for _cid, c in (data.get("connections") or {}).items():
        if (c.get("provider") or "").lower() != "oracle":
            continue
        cfg = c.get("configuration") or {}
        host = cfg.get("host")
        port = cfg.get("port")
        db_name = cfg.get("database")
        props = cfg.get("provider-properties") or {}
        sid_service = str(props.get("@dbeaver-sid-service@", "SID")).upper()
        is_service = sid_service == "SERVICE"

        if not (host and port and db_name):
            parsed = _parse_jdbc_url(cfg.get("url", ""))
            if not parsed:
                continue
            host, port, db_name, is_service = parsed

        conns.append(Connection(
            host=str(host),
            port=int(port),
            db_name=str(db_name),
            is_service=is_service,
            username=cfg.get("user") or None,   # usually absent for DBeaver
            sources={"dbeaver"},
            names={c.get("name") or f"dbeaver:{_cid}"},
        ))
    return conns


def parse_sqldev(data: dict) -> list[Connection]:
    conns: list[Connection] = []
    for c in (data.get("connections") or []):
        info = c.get("info") or {}
        if (info.get("subtype") or "").lower() != "orajdbc" and \
           (info.get("RaptorConnectionType") or "").lower() != "oracle":
            continue
        host = info.get("hostname")
        port = info.get("port")
        service_name = info.get("serviceName")
        sid = info.get("sid")

        if service_name:
            db_name, is_service = service_name, True
        elif sid:
            db_name, is_service = sid, False
        else:
            parsed = _parse_jdbc_url(info.get("customUrl", ""))
            if not (host and port) or not parsed:
                continue
            host, port, db_name, is_service = parsed

        if not (host and port and db_name):
            continue

        conns.append(Connection(
            host=str(host),
            port=int(port),
            db_name=str(db_name),
            is_service=is_service,
            username=info.get("user") or None,
            sources={"sqldev"},
            names={c.get("name") or f"sqldev:{host}"},
        ))
    return conns


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merge(conns: list[Connection]) -> list[Connection]:
    """Dedupe connections that point at the same target across both apps."""
    merged: dict[str, Connection] = {}
    for c in conns:
        key = c.id.lower()
        if key not in merged:
            merged[key] = c
            continue
        existing = merged[key]
        existing.sources |= c.sources
        existing.names |= c.names
        # Prefer any username we have (SQL Developer usually provides it).
        if not existing.username and c.username:
            existing.username = c.username
    return list(merged.values())


def server_connections() -> list[Connection]:
    """Connections declared in a server-config JSON file (``DB_MCP_CONNECTIONS``).

    For headless servers (no DBeaver/SQL Developer). Each entry:
    ``{"engine": "postgres", "host": "127.0.0.1", "port": 5432,
       "db_name": "sandbox", "username": "mcp_ro", "name": "pg-sandbox"}``
    Oracle entries may add ``"is_service": false`` for a SID. No passwords here.
    """
    path = os.environ.get("DB_MCP_CONNECTIONS")
    if not path or not Path(path).is_file():
        return []
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Connection] = []
    for e in entries if isinstance(entries, list) else []:
        engine = (e.get("engine") or "oracle").lower()
        host, port = e.get("host"), e.get("port")
        db_name = e.get("db_name") or e.get("database")
        if not (host and port and db_name):
            continue
        is_service = bool(e.get("is_service", engine != "oracle"))  # Postgres: always service-style
        name = e.get("name") or e.get("id") or f"{engine}:{host}"
        out.append(Connection(
            host=str(host), port=int(port), db_name=str(db_name), is_service=is_service,
            username=e.get("username") or None, engine=engine,
            sources={"config"}, names={name},
        ))
    return out


def discover() -> list[Connection]:
    """Discover and merge connections from desktop stores and server config."""
    found: list[Connection] = []
    for p in dbeaver_store_paths():
        found += parse_dbeaver(_load_json(p))
    for p in sqldev_store_paths():
        found += parse_sqldev(_load_json(p))
    found += server_connections()
    result = _merge(found)
    result.sort(key=lambda c: c.id.lower())
    return result


def find(selector: str) -> Connection | None:
    """Find a discovered connection by id or friendly name."""
    for c in discover():
        if c.matches(selector):
            return c
    return None
