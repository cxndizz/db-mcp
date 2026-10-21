# Install guide for an agent

You are helping a teammate set up **oracle-mcp**, a read-only Oracle MCP server.
Follow these steps in order. Do not ask the user for a DSN or connection string
— the server discovers connections from their DBeaver and SQL Developer. The
only secret you need from the user is a database password, entered once.

## 1. Ensure `uv` is installed
```bash
uv --version
```
If missing, install it (Windows PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
or macOS/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Get the code and dependencies
```bash
git clone <GIT_URL> db-mcp && cd db-mcp
uv sync
```

## 3. See what connections were discovered
```bash
uv run oracle-mcp list-connections
```
Each line shows an `id` like `host:port:sid` (SID) or `host:port/service`
(service), the source app(s), the username, and whether a password is stored.

## 4. Store a password once per connection the user wants
- If the line shows `user=(unknown)` (common for DBeaver), pass `--user`:
  ```bash
  uv run oracle-mcp set-password "<id>" --user <USERNAME>
  ```
- Otherwise:
  ```bash
  uv run oracle-mcp set-password "<id>"
  ```
The command prompts for the password (hidden) and runs a connection test.
**Never put the password in a file, a command flag, or chat.** It goes to the
OS keyring only.

> Best practice to suggest: use an Oracle account that only has `GRANT SELECT`.

## 5. Register the server with the user's MCP client
Pick the client(s) they use (see README for the exact snippet):
- Claude Code: `claude mcp add oracle -- uvx --from git+<GIT_URL> oracle-mcp`
- Codex / Gemini CLI / Antigravity: add a `command: uvx` MCP entry.
- ChatGPT: remote HTTP only — do this last and only for non-PII databases.

## 6. Verify
Ask the client something like: *"List the schemas on connection `<id>`"* or
*"Show 10 rows from table X in schema Y"*. The agent should call the tools and
return data (with sensitive columns masked).

## Troubleshooting
- **No connections found:** the user has no saved Oracle connection, or the
  store is in a non-default location — set `DBEAVER_WORKSPACE` or
  `SQLDEV_SYSTEM_DIR`.
- **Connection test FAILED:** wrong password, host not reachable from this
  machine, or the DB uses a service name where a SID was saved (or vice versa).
- **Query rejected (cost/unknown object):** the query references a missing
  table/column or is too expensive — refine it with a `WHERE` filter.
