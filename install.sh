#!/usr/bin/env bash
# oracle-mcp installer (macOS / Linux)
set -euo pipefail
cd "$(dirname "$0")"

echo "== oracle-mcp installer =="

# 1. Ensure uv
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Dependencies
echo "Installing dependencies..."
uv sync

# 3. Show discovered connections
echo
echo "Discovered connections:"
uv run oracle-mcp list-connections || true

# 4. Offer to set a password
echo
read -r -p "Enter a connection id to set a password for (or press Enter to skip): " id
if [ -n "${id:-}" ]; then
    read -r -p "Username (press Enter if the connection already has one): " user
    if [ -n "${user:-}" ]; then
        uv run oracle-mcp set-password "$id" --user "$user"
    else
        uv run oracle-mcp set-password "$id"
    fi
fi

echo
echo "Done."
echo "Register with Claude Code:  claude mcp add oracle -- uvx --from git+<GIT_URL> oracle-mcp"
