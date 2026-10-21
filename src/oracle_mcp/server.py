"""Entry point: builds the FastMCP server and provides a small CLI.

    oracle-mcp                     run the MCP server over stdio (default)
    oracle-mcp --http [--port N]   run over streamable-HTTP (for ChatGPT etc.)
    oracle-mcp list-connections    show discovered connections + status
    oracle-mcp set-password ID     store a password (and optional --user) in keyring
"""

from __future__ import annotations

import argparse
import getpass
import sys

from fastmcp import FastMCP

from . import __version__, db, keyvault
from .config import ConnOverride, SETTINGS, get_override, save_override
from .discovery import discover, find
from .tools import register

mcp = FastMCP(name="oracle-mcp", version=__version__)
register(mcp)


# ---------------------------------------------------------------------------
# CLI sub-commands
# ---------------------------------------------------------------------------

def _cmd_list_connections() -> int:
    conns = discover()
    if not conns:
        print("No Oracle connections found in DBeaver or SQL Developer stores.")
        print("Open one of those tools and save an Oracle connection, then retry.")
        return 1
    for c in conns:
        ov = get_override(c.id)
        user = c.username or ov.username or "(unknown)"
        pw = "yes" if keyvault.has_password(c.id) else "NO"
        ready = "ready" if (user != "(unknown)" and pw == "yes") else "needs set-password"
        print(f"  {c.id}")
        print(f"      names={sorted(c.names)} sources={sorted(c.sources)}")
        print(f"      user={user}  password={pw}  -> {ready}")
    return 0


def _cmd_set_password(conn_id: str, user: str | None) -> int:
    conn = find(conn_id)
    if conn is None:
        print(f"Unknown connection '{conn_id}'. Run 'oracle-mcp list-connections'.", file=sys.stderr)
        return 1
    # Persist a username override when the store did not provide one.
    if user or (not conn.username and not get_override(conn.id).username):
        chosen = user or input(f"Username for {conn.id}: ").strip()
        if chosen:
            ov = get_override(conn.id)
            ov.username = chosen
            save_override(conn.id, ov)
    password = getpass.getpass(f"Password for {conn.id}: ")
    if not password:
        print("No password entered; aborted.", file=sys.stderr)
        return 1
    keyvault.set_password(conn.id, password)
    ok = db.test_connection(conn.id)
    print(f"Saved. Connection test: {'OK' if ok else 'FAILED (check credentials/host reachability)'}")
    return 0 if ok else 2


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oracle-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-connections", help="List discovered connections and status")

    sp = sub.add_parser("set-password", help="Store a password in the OS keyring")
    sp.add_argument("conn_id", help="Connection id or name (see list-connections)")
    sp.add_argument("--user", help="Username (needed for DBeaver connections)")

    # Server options (no sub-command).
    parser.add_argument("--http", action="store_true", help="Serve over streamable-HTTP")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")

    args = parser.parse_args(argv)

    if args.command == "list-connections":
        return _cmd_list_connections()
    if args.command == "set-password":
        return _cmd_set_password(args.conn_id, args.user)

    # Default: run the MCP server.
    if args.http:
        import uvicorn
        from .httpgateway import Gateway
        if not SETTINGS.http_token:
            print("WARNING: MCP_HTTP_TOKEN is not set — the HTTP endpoint will be unauthenticated.",
                  file=sys.stderr)
        # Wrap the FastMCP ASGI app with the gateway (token + identity + quota).
        app = Gateway(mcp.http_app(path="/mcp"))
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp.run()  # stdio
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
