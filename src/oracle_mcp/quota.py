"""Per-user fixed-window rate limiting (in-process).

Caps how many MCP requests one identity may make per window. In-process is fine
for a single db-mcp instance; a multi-worker deployment would move this to a
shared store (e.g. Redis).
"""

from __future__ import annotations

import threading
import time

from .config import SETTINGS

_lock = threading.Lock()
_buckets: dict[str, tuple[float, int]] = {}  # user -> (window_start, count)


def allow(user: str) -> bool:
    """Count one request for ``user``; return False if over the limit."""
    limit, window = SETTINGS.rate_limit, SETTINGS.rate_window
    if limit <= 0:
        return True
    now = time.monotonic()
    with _lock:
        start, count = _buckets.get(user, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        _buckets[user] = (start, count)
        return count <= limit


def reset() -> None:
    """Clear all buckets (used by tests)."""
    with _lock:
        _buckets.clear()
