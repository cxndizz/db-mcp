"""Read-only Oracle MCP server.

Auto-discovers Oracle connections saved in DBeaver and SQL Developer, keeps
credentials in the OS keyring, and exposes a small set of read-only tools that
agents (Claude Code, Codex, Gemini, Antigravity, ChatGPT) can call to inspect
schemas and run guarded SELECT queries.
"""

__version__ = "0.1.0"

APP_NAME = "oracle-mcp"
KEYRING_SERVICE = "oracle-mcp"
