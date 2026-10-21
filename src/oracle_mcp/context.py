"""Per-request identity, carried in a context variable.

The HTTP gateway sets this from an identity header (set by the trusted proxy /
mcpo in front of db-mcp). Audit records and quota read it. In stdio mode there
is no HTTP request, so it stays at the default.
"""

from __future__ import annotations

from contextvars import ContextVar

current_user: ContextVar[str] = ContextVar("current_user", default="local")


def get_user() -> str:
    try:
        return current_user.get()
    except LookupError:
        return "local"
