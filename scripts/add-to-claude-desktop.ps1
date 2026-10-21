# Add the 'oracle' MCP server to the Claude desktop app at USER scope
# (available in every folder).
#
# WHY A SCRIPT: the desktop app owns ~/.claude.json and rewrites it from an
# in-memory snapshot taken at startup. Editing it while the app is running gets
# overwritten. So:
#
#   1. QUIT the Claude desktop app completely (also check the system tray).
#   2. Run this script in a NORMAL PowerShell window (NOT the app's terminal).
#   3. Relaunch the app — it reads the file at startup and keeps the entry.
#
# The merge runs Python from a temp file (not `-c`, which mangles quotes in
# PowerShell) for full JSON fidelity — PowerShell's own ConvertTo-Json would
# truncate the deeply-nested app state. Existing content is preserved and a
# .bak backup is written first.

$ErrorActionPreference = "Stop"

$claudeJson = Join-Path $env:USERPROFILE ".claude.json"
if (-not (Test-Path $claudeJson)) {
    throw "$claudeJson not found. Launch the Claude desktop app once, quit it, then retry."
}

# Absolute uvx path so the app can spawn it regardless of its PATH.
$uvx = Join-Path $env:USERPROFILE ".local\bin\uvx.exe"
if (-not (Test-Path $uvx)) {
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { $uvx = $cmd.Source } else { throw "uvx not found. Install uv first: https://docs.astral.sh/uv/" }
}

# Python (or 'uv run') for a lossless JSON merge.
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$pyPrefix = @()
if (-not $pyExe) {
    $uvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uvExe) { throw "python or uv is required to merge the config safely." }
    $pyExe = $uvExe
    $pyPrefix = @("run", "--no-project", "python")
}

Copy-Item $claudeJson "$claudeJson.bak" -Force
Write-Host "Backup written: $claudeJson.bak" -ForegroundColor DarkGray

$repo = "git+https://github.com/cxndizz/db-mcp"

# Write the merge code to a temp .py file to avoid PowerShell quote-mangling.
$tmp = Join-Path $env:TEMP ("oracle_mcp_merge_{0}.py" -f ([guid]::NewGuid().ToString('N')))
$pycode = @'
import json, pathlib, sys
path, uvx, repo = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
data = json.loads(path.read_text(encoding="utf-8"))
data.setdefault("mcpServers", {})["oracle"] = {
    "command": uvx,
    "args": ["--from", repo, "oracle-mcp"],
}
path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("Added mcpServers.oracle ->", uvx)
'@
Set-Content -LiteralPath $tmp -Value $pycode -Encoding ASCII

try {
    & $pyExe @pyPrefix $tmp $claudeJson $uvx $repo
    if ($LASTEXITCODE -ne 0) { throw "JSON merge failed (exit $LASTEXITCODE); ~/.claude.json was not changed." }
} finally {
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Relaunch the Claude desktop app." -ForegroundColor Green
Write-Host "Then open /mcp (or ask 'list connections') to confirm 'oracle' is connected."
Write-Host "If a password is not set yet:  uvx --from $repo oracle-mcp set-password `"<id>`""
