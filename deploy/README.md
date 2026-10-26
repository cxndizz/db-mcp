# Phase 1 deployment — on-prem chat over Ollama + MCP (Rocky Linux)

Multi-user web chat where people log in, talk to a **local** model, and the
model can query the database through db-mcp. No data leaves the server.

```
users ──TLS──> reverse proxy ──> Open WebUI (login, chat)
                                     │  OpenAI API          │ OpenAPI (tools)
                                     ▼                      ▼
                                  Ollama (models)         mcpo ──MCP(HTTP)──> db-mcp ──> DB
```

## Why each piece
- **Ollama** runs the models locally (data residency). **Ollama does not speak
  MCP** — it only serves models + tool-calling.
- **Open WebUI** is the chat UI + accounts + login.
- **mcpo** bridges MCP → OpenAPI so Open WebUI/the model can call db-mcp's tools.
- **db-mcp** is the read-only gateway to the DB (this repo), run as an HTTP
  service bound to localhost.

## Install

### 1. Heavy services (containers)
```bash
cd deploy
docker compose up -d        # ollama + open-webui
docker exec -it ollama ollama pull qwen2.5:14b   # a tool-calling model
```
GPU strongly recommended (uncomment the `deploy.resources` block; install the
NVIDIA Container Toolkit). Open WebUI: `http://127.0.0.1:3000` — create the
first (admin) account; sign-up is disabled for everyone else.

### 2. db-mcp + mcpo (host services)
```bash
# uv available to the service user (install system-wide)
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
/usr/local/bin/uvx --version

# service user WITH a home + a writable uvx cache
useradd -r -d /var/lib/db-mcp -s /sbin/nologin dbmcp 2>/dev/null || true
mkdir -p /var/lib/db-mcp/cache /var/log/db-mcp /etc/db-mcp
chown -R dbmcp:dbmcp /var/lib/db-mcp /var/log/db-mcp

# config
cp connections.example.json /etc/db-mcp/connections.json
cp mcpo.config.json         /etc/db-mcp/mcpo.config.json
cp env.example              /etc/db-mcp/env
# edit /etc/db-mcp/env: set real secrets, e.g.
#   MCP_HTTP_TOKEN=$(openssl rand -hex 24)
#   MCPO_API_KEY=$(openssl rand -hex 24)
#   DB_MCP_PW_127_0_0_1_15432_SANDBOX=mcp_ro
chmod 600 /etc/db-mcp/env && chown dbmcp /etc/db-mcp/env

# db-mcp enforces its bearer token, so mcpo must send it: inject MCP_HTTP_TOKEN
# into the copied mcpo config (replaces the REPLACE_WITH_MCP_HTTP_TOKEN placeholder)
TOKEN=$(grep '^MCP_HTTP_TOKEN=' /etc/db-mcp/env | cut -d= -f2-)
sed -i "s|REPLACE_WITH_MCP_HTTP_TOKEN|$TOKEN|" /etc/db-mcp/mcpo.config.json
chmod 600 /etc/db-mcp/mcpo.config.json && chown dbmcp /etc/db-mcp/mcpo.config.json

# units (first start is slow — uvx builds db-mcp from git)
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now db-mcp mcpo
systemctl status db-mcp mcpo --no-pager
journalctl -u db-mcp -n 40 --no-pager     # if it doesn't come up
```
mcpo binds `0.0.0.0:8001` (so the Open WebUI container can reach it); keep 8001
**closed in firewalld** — it is internal only. db-mcp stays on `127.0.0.1:8000`.
Check: `curl -s localhost:8000/mcp/ >/dev/null && echo ok` and
`systemctl status db-mcp mcpo`.

> **Known gotcha (verified on Rocky):** `mcpo` fails at startup with
> `ImportError: cannot import name 'streamablehttp_client'` when it resolves an
> incompatible `mcp` (a too-new `mcp`, or a bleeding-edge Python like 3.15).
> Fix: pin **Python 3.12** and let mcpo choose its own `mcp` — the unit uses
> `uvx --python 3.12 mcpo ...` (do NOT add `--with mcp...`). If a bad build is
> cached, clear it once: `sudo -u dbmcp env HOME=/var/lib/db-mcp
> UV_CACHE_DIR=/var/lib/db-mcp/cache uvx --refresh --python 3.12 mcpo --help`.
>
> **SELinux:** if a unit dies with `203/EXEC … Permission denied` on
> `/usr/local/bin/uvx`, relabel it: `restorecon -v /usr/local/bin/uv*`.

### 3. Wire the tools into Open WebUI
Settings → Tools → Add → URL `http://host.docker.internal:8001`, API key =
`MCPO_API_KEY`. The db-mcp tools (`list_tables`, `run_query`, …) now appear; the
model calls them during chat.

### 4. Reverse proxy + TLS
Only the proxy is public. Example (Caddy): `chat.example.com { reverse_proxy
127.0.0.1:3000 }`. Everything else stays on `127.0.0.1`.

## Verified so far (on the dev machine)
- db-mcp **HTTP mode** serves all 8 tools over streamable-http; a client did
  `list_tables` + `run_query` (cost gate) + a masked PII query against the
  Postgres sandbox. This is the exact server mcpo will proxy.
- The sandbox (`../sandbox`) is the DB used here until the real Oracle is wired.

## Security checklist (v1 = single service account)
- [ ] db-mcp and mcpo bound to `127.0.0.1`; only the reverse proxy is public.
- [ ] TLS on the proxy; Open WebUI sign-up disabled after the admin account.
- [ ] `/etc/db-mcp/env` is `chmod 600`; no secrets in git.
- [ ] **DB role is the real PII boundary:** `mcp_ro` has `GRANT SELECT` only on
      PII-redacted views (not base tables) — app masking is defense-in-depth.
- [ ] Connect real Oracle via a **read replica**, not prod primary.
- [ ] Ollama only — no OpenRouter/hosted model sees DB rows.
- [ ] Review `/var/log/db-mcp/audit.jsonl`.

## Phase 2 (done — in the HTTP gateway)
- `--http` now **enforces** `MCP_HTTP_TOKEN` (401 without a valid bearer).
- Per-user **identity passthrough**: the trusted proxy/mcpo sets
  `MCP_IDENTITY_HEADER` (default `x-dbmcp-user`); db-mcp records it on every
  audit line and keys the rate limit on it.
- Per-user **rate limit**: `MCP_RATE_LIMIT` requests per `MCP_RATE_WINDOW`
  seconds (default 60/60) → 429 when exceeded.

Configure Open WebUI → mcpo to forward the logged-in user as that header so
audit/quotas are per person rather than per service account.

## Later
- Per-user DB identity (distinct DB roles per person), shared-store rate limiting
  for multi-worker, and row-level access policies.
