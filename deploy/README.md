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
# one-time
sudo useradd -r -s /usr/sbin/nologin dbmcp
sudo mkdir -p /etc/db-mcp /var/log/db-mcp && sudo chown dbmcp /var/log/db-mcp
sudo cp connections.example.json /etc/db-mcp/connections.json
sudo cp mcpo.config.json         /etc/db-mcp/mcpo.config.json
sudo cp env.example              /etc/db-mcp/env      # edit secrets, chmod 600
sudo chmod 600 /etc/db-mcp/env
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now db-mcp mcpo
```
Check: `curl -s localhost:8000/mcp/ >/dev/null && echo ok` and
`systemctl status db-mcp mcpo`.

> **Known gotcha (verified):** some `mcpo` resolutions fail at startup with
> `ImportError: cannot import name 'streamablehttp_client'`. That's an `mcp`
> version mismatch, not db-mcp. The unit pins `mcp>=1.12.0` to avoid it; if it
> still bites, `uvx --refresh --with "mcp>=1.12.0" mcpo ...`.

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
