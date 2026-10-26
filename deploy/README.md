# Phase 1 deployment — on-prem chat over Ollama + MCP (Rocky Linux)

Multi-user web chat where people log in, talk to a **local** model, and the
model can query the database through db-mcp. No data leaves the server.

```
users ──TLS──> reverse proxy ──> Open WebUI (login, chat)
                                     │  OpenAI API          │ OpenAPI (tools)
                                     ▼                      ▼
                                  Ollama (models)         mcpo ──MCP(HTTP)──> db-mcp ──> DB
```

**Verified working on Rocky 10** with this exact layout:
- **Ollama** + **Open WebUI** as containers (compose below).
- **db-mcp** as a host service from a fixed uv venv (systemd).
- **mcpo** as the official container with `--network host` (consistent deps —
  avoids the `mcp`/`fastapi` version traps you hit installing it by hand).

## 1. Heavy services (containers)
```bash
cd deploy
docker compose up -d                                  # ollama + open-webui
docker exec -it ollama ollama pull qwen2.5:3b         # a small CPU-friendly tool-calling model
```
GPU strongly recommended for anything bigger (uncomment the `deploy.resources`
block + install the NVIDIA Container Toolkit). Open WebUI: reach it via an SSH
tunnel `ssh -L 3000:127.0.0.1:3000 user@server`, open http://localhost:3000, and
create the first (admin) account; sign-up is disabled for everyone else.

## 2. db-mcp (host service, fixed venv)
```bash
# uv available to the service user (system-wide)
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
# SELinux (Rocky): relabel so systemd may exec it, else 203/EXEC Permission denied
restorecon -v /usr/local/bin/uv /usr/local/bin/uvx

# service user WITH a home (uv writes its venv/cache there)
useradd -r -d /var/lib/db-mcp -s /sbin/nologin dbmcp 2>/dev/null || true
mkdir -p /var/lib/db-mcp/cache /var/log/db-mcp /etc/db-mcp
chown -R dbmcp:dbmcp /var/lib/db-mcp /var/log/db-mcp

# install db-mcp into a fixed venv (deterministic — no per-start uvx resolution)
sudo -u dbmcp env HOME=/var/lib/db-mcp UV_CACHE_DIR=/var/lib/db-mcp/cache \
  /usr/local/bin/uv tool install --force --from git+https://github.com/cxndizz/db-mcp oracle-mcp

# config + secrets
cp connections.example.json /etc/db-mcp/connections.json
cp mcpo.config.json         /etc/db-mcp/mcpo.config.json
cp env.example              /etc/db-mcp/env
TOKEN=$(openssl rand -hex 24); APIKEY=$(openssl rand -hex 24)
sed -i "s|^MCP_HTTP_TOKEN=.*|MCP_HTTP_TOKEN=$TOKEN|; s|^MCPO_API_KEY=.*|MCPO_API_KEY=$APIKEY|" /etc/db-mcp/env
# mcpo must send db-mcp's bearer token -> inject it into the mcpo config
sed -i "s|REPLACE_WITH_MCP_HTTP_TOKEN|$TOKEN|" /etc/db-mcp/mcpo.config.json
# set the real DB password(s): DB_MCP_PW_<id> (e.g. mcp_ro for the sandbox)
chmod 600 /etc/db-mcp/env /etc/db-mcp/mcpo.config.json
chown dbmcp /etc/db-mcp/env /etc/db-mcp/mcpo.config.json

# unit (execs the installed binary; stays on 127.0.0.1)
cp systemd/db-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now db-mcp
curl -s -o /dev/null -w 'db-mcp -> %{http_code} (expect 401)\n' localhost:8000/mcp/
```
Upgrade later with `uv tool install --force --from git+... oracle-mcp` + `systemctl restart db-mcp`.

## 3. mcpo (official container, host network)
Installing mcpo by hand is a version minefield (`mcp`/`fastapi` mismatches). Use
the image — its deps are consistent.
```bash
APIKEY=$(grep '^MCPO_API_KEY=' /etc/db-mcp/env | cut -d= -f2-)
docker run -d --name mcpo --restart unless-stopped --network host \
  -v /etc/db-mcp/mcpo.config.json:/config.json:ro \
  ghcr.io/open-webui/mcpo:main \
  --host 0.0.0.0 --port 8001 --api-key "$APIKEY" --config /config.json
curl -s localhost:8001/oracle/openapi.json | head -c 200; echo   # JSON with /run_query etc.
```
`--network host` lets mcpo reach db-mcp at `127.0.0.1:8000` (the config URL) just
like a host process, and listens on `0.0.0.0:8001` so the Open WebUI container
can reach it. Keep 8000/8001 **closed in firewalld** (internal only).

## 4. Wire the tools into Open WebUI
Admin Settings → Tools → **External Tool Servers** → **+**:
- Type **OpenAPI**, URL `http://host.docker.internal:8001/oracle`
- Auth **Bearer**, key = `MCPO_API_KEY`
Save. In a chat, pick the model, enable the tool (🔧), and ask e.g. *"what tables
are in schema loan"*.

## 5. Reverse proxy + TLS
Only the proxy is public (e.g. Caddy: `chat.example.com { reverse_proxy
127.0.0.1:3000 }`). Everything else stays on `127.0.0.1` / behind firewalld.

## Security checklist (v1 = single service account)
- [ ] db-mcp on `127.0.0.1`; mcpo/db-mcp ports closed in firewalld.
- [ ] TLS on the proxy; Open WebUI sign-up disabled after the admin account.
- [ ] `/etc/db-mcp/env` + `mcpo.config.json` are `chmod 600`; no secrets in git.
- [ ] **DB role is the real PII boundary:** `mcp_ro` has `GRANT SELECT` only on
      PII-redacted views (not base tables) — app masking is defense-in-depth.
- [ ] Connect real Oracle via a **read replica**, not prod primary.
- [ ] Ollama only — no OpenRouter/hosted model sees DB rows.
- [ ] Review `/var/log/db-mcp/audit.jsonl` (each line has the acting user).

## Phase 2 (done — in the HTTP gateway)
- `--http` enforces `MCP_HTTP_TOKEN` (401 without it).
- Identity passthrough: `MCP_IDENTITY_HEADER` (default `x-dbmcp-user`) → per-user
  audit + rate limit (`MCP_RATE_LIMIT`/`MCP_RATE_WINDOW`, default 60/60 → 429).
  Configure the proxy/mcpo to forward the logged-in user as that header.

## Gotchas we hit (so you don't)
- **SELinux 203/EXEC** on `/usr/local/bin/uvx`: `restorecon -v /usr/local/bin/uv*`.
- **mcpo `ImportError: streamablehttp_client` / `ssl_certfile` TypeError**: don't
  hand-install mcpo — use the container (step 3).
- **db-mcp `No module named 'key_value'`**: FastMCP 4.x needs `py-key-value-aio`
  (now a dependency) for HTTP sessions — a fresh `uv tool install` pulls it.

## Adding the real Oracle later (Phase 3)
Add an entry to `/etc/db-mcp/connections.json` — **valid JSON, no comments**
(JSON comments break the loader). Prefer a READ REPLICA, and grant its read-only
user SELECT only on PII-redacted views:
```json
[
  { "name": "pg-sandbox", "engine": "postgres", "host": "127.0.0.1", "port": 15432, "db_name": "sandbox", "username": "mcp_ro" },
  { "name": "oracle-prod-ro", "engine": "oracle", "host": "oradb-replica.internal", "port": 1521, "db_name": "ORCLPDB", "is_service": true, "username": "mcp_ro" }
]
```
Then set `DB_MCP_PW_<id>` for it in `/etc/db-mcp/env` (id = `host:port/service`
or `host:port:sid`, non-alphanumerics → `_`, upper-cased).

## Later
- Per-user DB identity (distinct DB roles per person), shared-store rate limiting
  for multi-worker, row-level access policies.
