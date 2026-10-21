# oracle-mcp installer (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== oracle-mcp installer ==" -ForegroundColor Cyan

# 1. Ensure uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
uv sync

# 3. Show discovered connections
Write-Host "`nDiscovered connections:" -ForegroundColor Cyan
uv run oracle-mcp list-connections

# 4. Offer to set a password
Write-Host ""
$id = Read-Host "Enter a connection id to set a password for (or press Enter to skip)"
if ($id) {
    $user = Read-Host "Username (press Enter if the connection already has one)"
    if ($user) { uv run oracle-mcp set-password "$id" --user "$user" }
    else       { uv run oracle-mcp set-password "$id" }
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Register with Claude Code:  claude mcp add oracle -- uvx --from git+https://github.com/cxndizz/db-mcp oracle-mcp"
