# Shows the owner key and connector setup hints on the console.
#
# The key exists in exactly one place on disk - the .env next to the exe, which
# the installer restricts to SYSTEM + Administrators + the installing user. This
# script reads it and prints it; it never writes a second copy anywhere.
#
# Run as the user who installed the MCP (or as Administrator):
#   powershell -ExecutionPolicy Bypass -File "Show-OwnerKey.ps1"

param([string]$InstallDir = $PSScriptRoot)

$envFile = Join-Path $InstallDir ".env"
$exe     = Join-Path $InstallDir "windows-login-monitor-mcp.exe"

# Local mode: the client spawns the exe over stdio, so there is no key to show.
# Show how to register it instead - that is what the user actually needs.
function Show-LocalMode {
    Write-Host ""
    Write-Host "=== Windows Login Monitor MCP - local mode ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "No owner key: there is no listener, so there is nothing to authenticate."
    Write-Host "Your MCP client starts the server on demand over stdio."
    Write-Host ""
    Write-Host "Register it:" -ForegroundColor Yellow
    Write-Host "  claude mcp add windows-login-monitor -- `"$exe`" --stdio" -ForegroundColor Green
    Write-Host ""
    Write-Host "Other clients: run the exe with --stdio as the command, no arguments beyond that."
    Write-Host "To expose it remotely instead, re-run the installer and give a public hostname;"
    Write-Host "that mode generates an owner key and registers a background service."
    Read-Host "`nPress Enter to close"
}

if (-not (Test-Path -LiteralPath $envFile)) {
    if (Test-Path -LiteralPath $exe) { Show-LocalMode; exit 0 }
    Write-Host "No .env and no exe in $InstallDir - the MCP does not look installed here." -ForegroundColor Red
    Write-Host "Re-run the installer, or point this script at the install directory:" -ForegroundColor Yellow
    Write-Host "  .\Show-OwnerKey.ps1 -InstallDir 'C:\Program Files\WindowsLoginMonitorMcp'"
    Read-Host "`nPress Enter to close"
    exit 1
}

try {
    $raw = Get-Content -LiteralPath $envFile -Raw
} catch [System.UnauthorizedAccessException] {
    Write-Host "Access denied reading $envFile." -ForegroundColor Red
    Write-Host "That file is restricted to SYSTEM, Administrators and the user who" -ForegroundColor Yellow
    Write-Host "installed the MCP. Re-run this as that user, or as Administrator." -ForegroundColor Yellow
    Read-Host "`nPress Enter to close"
    exit 1
}

$ownerKey = $null
foreach ($name in @("WLM_MCP_OWNER_KEY", "WLM_MCP_TOKEN")) {   # legacy name still accepted
    $m = [regex]::Match($raw, "$name\s*=\s*(\S+)")
    if ($m.Success) { $ownerKey = $m.Groups[1].Value; break }
}
if (-not $ownerKey) {
    # .env exists but carries only non-secret config - that is local mode.
    Show-LocalMode
    exit 0
}

$publicUrl  = ([regex]::Match($raw, 'WLM_MCP_PUBLIC_URL\s*=\s*(\S+)')).Groups[1].Value
$publicHost = ""
if ($publicUrl -and $publicUrl -notmatch '127\.0\.0\.1|localhost') {
    $publicHost = ([uri]$publicUrl).Host
}
$mcpUrl = if ($publicHost) { "https://$publicHost/mcp" } else { "https://<your-hostname>/mcp" }

Write-Host ""
Write-Host "=== Windows Login Monitor MCP ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "OWNER KEY (master password - keep secret):" -ForegroundColor Yellow
Write-Host "  $ownerKey"
Write-Host ""
Write-Host "The owner key only authenticates YOU at the OAuth /authorize step."
Write-Host "Each client (Claude.ai, Claude Code, Cursor, etc.) gets its own scoped"
Write-Host "access + refresh token after you approve."
Write-Host ""
Write-Host "Local endpoint:    http://127.0.0.1:8765/mcp"
Write-Host "Health probe:      http://127.0.0.1:8765/healthz"
if ($publicHost) { Write-Host "Public endpoint:   https://$publicHost/mcp" }
Write-Host ""
Write-Host "NEXT STEP: expose the local endpoint to your agent."
Write-Host "Pick whatever you already use:"
Write-Host "  - Cloudflare Tunnel: add ingress hostname -> http://host.docker.internal:8765"
Write-Host "                       (Docker cloudflared) or http://127.0.0.1:8765 (native)"
Write-Host "  - Tailscale Funnel:  tailscale funnel 8765"
Write-Host "  - ngrok:             ngrok http 8765"
Write-Host "  - Caddy / nginx + DDNS or static IP"
Write-Host "  - Nothing - works as-is for Claude Code on this PC."
Write-Host ""
Write-Host "Then in your client (e.g. Claude.ai Custom Connector):"
Write-Host "  URL: $mcpUrl"
Write-Host "  Tap Connect; the browser opens the authorize page."
Write-Host "  Paste the OWNER KEY above and submit. Done."
Write-Host ""
Write-Host "Runs as:        scheduled task 'LoginMonitorMcp', windowless, autostart at logon."
Write-Host "                No console window - nothing to close by mistake."
Write-Host "Log file:       %LOCALAPPDATA%\WindowsLoginMonitorMcp\server.log"
Write-Host "Key stored in:  $envFile (SYSTEM + Administrators + you)"
Write-Host "Show again:     powershell -File `"$InstallDir\Show-OwnerKey.ps1`""
Write-Host ""
Write-Host "Store the key in a password manager, then close this window - it is" -ForegroundColor Yellow
Write-Host "on screen, not in a file." -ForegroundColor Yellow

Read-Host "`nPress Enter to close"
