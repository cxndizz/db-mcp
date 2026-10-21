# Phase 0 sandbox — db-mcp over PostgreSQL

A throwaway local PostgreSQL with **synthetic** data (no real PII) to build and
test the whole flow before touching real Oracle.

## 1. Start Postgres
```bash
cd sandbox
docker compose up -d
docker compose ps        # wait until healthy
```
Postgres is now on `127.0.0.1:15432`, database `sandbox`, with schema `loan`,
500 fake rows in `loan.student_loan_register`, a PII-free view
`loan.v_register_safe`, and a read-only role `mcp_ro` / `mcp_ro`.

## 2. Point db-mcp at it
db-mcp reads connections from `DB_MCP_CONNECTIONS` and the password from the
keyring or `DB_MCP_PW_<id>` (id = connection id with non-alphanumerics → `_`,
upper-cased). The sandbox id is `127.0.0.1:15432/sandbox`.

**bash / Linux (the eventual Rocky server):**
```bash
export DB_MCP_CONNECTIONS="$PWD/connections.example.json"
export DB_MCP_PW_127_0_0_1_15432_SANDBOX="mcp_ro"
uvx --from git+https://github.com/cxndizz/db-mcp oracle-mcp list-connections
```

**PowerShell (Windows):**
```powershell
$env:DB_MCP_CONNECTIONS = "$PWD\connections.example.json"
$env:DB_MCP_PW_127_0_0_1_15432_SANDBOX = "mcp_ro"
uvx --from git+https://github.com/cxndizz/db-mcp oracle-mcp list-connections
```
You should see `pg-sandbox` (engine `postgres`) as **ready**.

## 3. Run db-mcp over HTTP (how a server exposes it)
```bash
export MCP_HTTP_TOKEN="dev-sandbox-token"      # required for --http
uvx --from git+https://github.com/cxndizz/db-mcp oracle-mcp --http --port 8000
```
MCP endpoint: `http://127.0.0.1:8000/mcp/`. Bind stays on localhost — in the real
deployment only the reverse proxy is public and the web UI reaches this.

## 4. Verify
Any MCP client, or the Inspector:
```bash
npx @modelcontextprotocol/inspector
```
Try:
- `list_connections` → `pg-sandbox` ready
- `list_schemas` → includes `loan`
- `list_tables` (schema `loan`) → `student_loan_register`, `v_register_safe`
- `run_query` `SELECT status, COUNT(*) FROM loan.student_loan_register GROUP BY status`
- `run_query` `SELECT borrower_cid, borrower_first_name, status FROM loan.student_loan_register LIMIT 3`
  → `borrower_cid` / `borrower_first_name` come back **masked** (auto-detect); `status` real
- a bad query (`SELECT * FROM loan.nope`) → rejected by the EXPLAIN gate

## The security lesson baked in
`mcp_ro` is granted the **base table** here only so app-layer masking is visible.
In production, grant `mcp_ro` **only** `loan.v_register_safe` (no base table): then
PII is unreachable even if app masking is bypassed by aliasing
(`SELECT borrower_cid AS x ...`). The database role — not app masking — is the
real boundary for untrusted multi-user access.

## Tear down
```bash
docker compose down -v      # -v also drops the data volume
```
