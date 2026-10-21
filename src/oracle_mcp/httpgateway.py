"""ASGI gateway wrapped around the FastMCP HTTP app.

For every HTTP request it:
  1. enforces the bearer token (MCP_HTTP_TOKEN), if configured,
  2. reads the identity header into the `current_user` context var,
  3. applies the per-user rate limit.

Non-HTTP scopes (lifespan, websocket) pass straight through, so the wrapped
app's startup/shutdown still runs.
"""

from __future__ import annotations

import json

from . import quota
from .config import SETTINGS
from .context import current_user


class Gateway:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}

        if SETTINGS.http_token and headers.get("authorization", "") != f"Bearer {SETTINGS.http_token}":
            await _json(send, 401, {"error": "unauthorized"})
            return

        user = headers.get(SETTINGS.identity_header) or "service"
        tok = current_user.set(user)
        try:
            if not quota.allow(user):
                await _json(send, 429, {"error": "rate limit exceeded", "user": user})
                return
            await self.app(scope, receive, send)
        finally:
            current_user.reset(tok)


async def _json(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})
