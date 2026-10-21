# oracle-mcp

A **read-only** Oracle MCP server. It auto-discovers the Oracle connections you
already saved in **DBeaver** and **SQL Developer**, keeps passwords in your OS
keyring, and lets any MCP client (Claude Code, Codex, Gemini CLI, Antigravity,
ChatGPT) inspect schemas and run guarded `SELECT` queries in plain language.

> You do **not** type DSNs or connection strings. The server reads only the
> non-secret parts of your saved connections (host / port / service or SID /
> username when the app stores it). It never reads or decrypts the passwords
> DBeaver or SQL Developer store — you set the password once and it goes into
> Windows Credential Manager (the OS keyring).

## What it does (and does not)

- **Read-only, two layers:** only a single `SELECT` / `WITH` statement is allowed
  (DML, DDL, PL/SQL and multi-statements are rejected by parser), and every
  session runs `SET TRANSACTION READ ONLY`.
  **Recommended:** also connect with an Oracle user that only has `GRANT SELECT`
  — that is the real wall if the code ever has a bug.
- **No runaway queries:** every query is checked with `EXPLAIN PLAN` first
  (rejecting unknown tables/columns and very expensive plans), a row cap is
  always injected, there is a query timeout, and **timed-out queries are not
  retried**.
- **Automatic PII masking:** the server inspects each returned column's name
  (and, as a fallback, its values) and masks the ones that look like personal
  data — national id, names, date of birth, email, phone, income, bank account,
  address — with no per-column list to maintain. Category columns like
  `INCOME_TYPE` or `MAJOR_NAME` are left alone. Tune per connection with
  `unmask_columns` (exempt a false positive) or `mask_columns` (force extra), or
  turn it off with `ORACLE_AUTO_MASK=0`.
- **Also:** optional schema allowlist and a JSONL audit log of every query.

## Requirements

- [uv](https://docs.astral.sh/uv/) (the installer handles this)
- At least one Oracle connection saved in DBeaver or SQL Developer

## Install

```bash
uv sync
```

Then see what was discovered and set a password once per connection:

```bash
uv run oracle-mcp list-connections
uv run oracle-mcp set-password "oradb.studentloan.or.th:1553:oraapps"
# DBeaver connections have no stored username -> provide one:
uv run oracle-mcp set-password "192.168.66.11:1521:oraappsdev" --user CHAKKRIA
```

## Run as an MCP server

- stdio (default, for local clients): `uv run oracle-mcp`
- HTTP (for ChatGPT / remote): `uv run oracle-mcp --http --port 8000`

### Client configuration

`uvx` runs it straight from the repo without cloning.

**Claude Code**
```bash
claude mcp add oracle -- uvx --from git+https://github.com/cxndizz/db-mcp oracle-mcp
```

**Codex CLI** — `~/.codex/config.toml`
```toml
[mcp_servers.oracle]
command = "uvx"
args = ["--from", "git+https://github.com/cxndizz/db-mcp", "oracle-mcp"]
```

**Gemini CLI** — `~/.gemini/settings.json`
```json
{ "mcpServers": { "oracle": { "command": "uvx",
  "args": ["--from", "git+https://github.com/cxndizz/db-mcp", "oracle-mcp"] } } }
```

**Antigravity** — `~/.gemini/config/mcp_config.json` (global) or
`.agents/mcp_config.json` (workspace)
```json
{ "mcpServers": { "oracle": { "command": "uvx",
  "args": ["--from", "git+https://github.com/cxndizz/db-mcp", "oracle-mcp"] } } }
```

**ChatGPT** (remote only, do this last) — run `--http`, expose it over HTTPS
with a tunnel (Cloudflare Tunnel / ngrok), set `MCP_HTTP_TOKEN`, then in ChatGPT
enable **Developer Mode → Apps & Connectors** and add the public
`https://…/mcp/` URL with the bearer token.
⚠️ Exposing to ChatGPT sends query results to OpenAI and makes the endpoint
reachable from the internet. **Do not expose databases with PII to ChatGPT.**

## Tools

| Tool | Purpose |
|------|---------|
| `list_connections` | Discovered connections + status (username / password / ready) |
| `list_schemas` | Owners/schemas (allowlist-limited) |
| `list_tables` | Tables + views in a schema |
| `describe_table` | Columns, types, nullability, PK/FK, comments |
| `get_object_ddl` | `DBMS_METADATA.GET_DDL` for an object |
| `search_objects` | Find tables/columns by name |
| `sample_table` | Preview rows (masked) |
| `run_query` | Run a guarded `SELECT` (masked) |

## Configuration (optional, via environment / `.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `ORACLE_MAX_ROWS` | 200 | default row cap |
| `ORACLE_HARD_MAX_ROWS` | 5000 | absolute ceiling |
| `ORACLE_QUERY_TIMEOUT` | 30 | seconds |
| `ORACLE_EXPLAIN_COST_LIMIT` | 1000000 | reject plans above this cost |
| `ORACLE_MAX_CONCURRENCY` | 4 | max concurrent queries |
| `ORACLE_AUTO_MASK` | 1 | auto-detect & mask PII columns (`0` to disable) |
| `AUDIT_LOG_PATH` | `./logs/audit.jsonl` | audit log location |
| `MCP_HTTP_TOKEN` | — | bearer token **enforced** in `--http` mode (401 without it) |
| `MCP_IDENTITY_HEADER` | `x-dbmcp-user` | request header carrying the user id (for audit + quota) |
| `MCP_RATE_LIMIT` / `MCP_RATE_WINDOW` | 60 / 60 | per-user requests allowed per window (seconds) |
| `DBEAVER_WORKSPACE` / `SQLDEV_SYSTEM_DIR` | — | override store locations |

Per-connection schema allowlist and masking overrides live in a local
`overrides.json` (no secrets) under `%APPDATA%\oracle-mcp\`:

```json
{
  "192.168.66.11:1521:oraappsdev": {
    "schemas": ["SLFAPPS"],
    "unmask_columns": ["BORROWER_ADDRESS_PROVINCE"],
    "mask_columns": ["SLFAPPS.STUDENT_LOAN_REGISTER.BORROWER_GPA"]
  }
}
```

See [AGENTS.md](AGENTS.md) for a step-by-step install an agent can follow.
