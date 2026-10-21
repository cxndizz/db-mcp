import asyncio
import json

from oracle_mcp import quota
from oracle_mcp.config import SETTINGS
from oracle_mcp.context import current_user, get_user
from oracle_mcp.httpgateway import Gateway


class Downstream:
    def __init__(self):
        self.called = False
        self.seen_user = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.seen_user = get_user()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive(app, headers):
    scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    await app(scope, receive, send)
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def test_token_enforced(monkeypatch):
    quota.reset()
    monkeypatch.setattr(SETTINGS, "http_token", "secret")
    down = Downstream()
    gw = Gateway(down)
    assert asyncio.run(_drive(gw, {})) == 401
    assert down.called is False
    assert asyncio.run(_drive(gw, {"authorization": "Bearer wrong"})) == 401
    assert asyncio.run(_drive(gw, {"authorization": "Bearer secret"})) == 200
    assert down.called is True


def test_no_token_allows(monkeypatch):
    quota.reset()
    monkeypatch.setattr(SETTINGS, "http_token", None)
    down = Downstream()
    assert asyncio.run(_drive(Gateway(down), {})) == 200


def test_identity_passthrough(monkeypatch):
    quota.reset()
    monkeypatch.setattr(SETTINGS, "http_token", None)
    monkeypatch.setattr(SETTINGS, "identity_header", "x-dbmcp-user")
    down = Downstream()
    asyncio.run(_drive(Gateway(down), {"x-dbmcp-user": "alice"}))
    assert down.seen_user == "alice"
    # context var does not leak after the request
    assert get_user() == "local"


def test_identity_defaults_to_service(monkeypatch):
    quota.reset()
    monkeypatch.setattr(SETTINGS, "http_token", None)
    down = Downstream()
    asyncio.run(_drive(Gateway(down), {}))
    assert down.seen_user == "service"


def test_rate_limit(monkeypatch):
    quota.reset()
    monkeypatch.setattr(SETTINGS, "http_token", None)
    monkeypatch.setattr(SETTINGS, "rate_limit", 2)
    monkeypatch.setattr(SETTINGS, "rate_window", 60)
    gw = Gateway(Downstream())
    assert asyncio.run(_drive(gw, {"x-dbmcp-user": "bob"})) == 200
    assert asyncio.run(_drive(gw, {"x-dbmcp-user": "bob"})) == 200
    assert asyncio.run(_drive(gw, {"x-dbmcp-user": "bob"})) == 429
    # a different user has its own bucket
    assert asyncio.run(_drive(gw, {"x-dbmcp-user": "carol"})) == 200


def test_lifespan_passes_through():
    down = Downstream()
    scope = {"type": "lifespan"}

    async def go():
        hit = {"v": False}

        async def app(s, r, snd):
            hit["v"] = True
        await Gateway(app)(scope, None, None)
        return hit["v"]

    assert asyncio.run(go()) is True


def test_audit_includes_user(tmp_path, monkeypatch):
    from oracle_mcp.security import audit
    log = tmp_path / "audit.jsonl"
    tok = current_user.set("dave")
    try:
        audit(str(log), {"tool": "run_query", "sql": "SELECT 1"})
    finally:
        current_user.reset(tok)
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["user"] == "dave"
    assert rec["tool"] == "run_query"
