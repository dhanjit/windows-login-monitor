# Runs in the [Run] section of the Inno installer, post-extract, elevated.
#
# There is very little to do. The server speaks stdio: the MCP client starts
# the exe and owns both ends of the pipe. Nothing to bind, nothing to
# authenticate, nothing left running afterwards. All this needs to do is grant
# the Security-log access the tools need, clean up anything earlier versions
# left behind, and say how to register it.
#
# Args:
#   -InstallDir <path>   where windows-login-monitor-mcp.exe lives

param(
    [Parameter(Mandatory=$true)][string]$InstallDir
)

$ErrorActionPreference = "Stop"
$exe      = Join-Path $InstallDir "windows-login-monitor-mcp.exe"
$taskName = "LoginMonitorMcp"
$me       = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "Configuring Windows Login Monitor MCP for $me"

# --- 1. Security event log access for the tools ---
try {
    if (-not (Get-LocalGroupMember -Group "Event Log Readers" -Member $me -ErrorAction SilentlyContinue)) {
        Add-LocalGroupMember -Group "Event Log Readers" -Member $me
    }
} catch {
    Write-Host "WARN: could not add $me to Event Log Readers: $($_.Exception.Message)"
}

# --- 2. Tear down what versions <= 0.1.3 installed ---
# Those shipped an HTTP server with its own OAuth: a background service, a
# listening port, and an owner key in .env. None of it has anything to serve
# now, and a credential nothing reads is exactly the defect from #2.
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the '$taskName' background service (no longer used)."
}
Get-Process windows-login-monitor-mcp -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($stale in @(".env", "FIRST-RUN.txt", "server.log", "server.err.log")) {
    $p = Join-Path $InstallDir $stale
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
        Write-Host "Removed $p (left by an earlier version)."
    }
}

# --- 3. Say how to use it ---
Write-Host ""
Write-Host "=== Installed ===" -ForegroundColor Cyan
Write-Host "No key, no service, no listening port. Register it with your MCP client:"
Write-Host ""
Write-Host "  claude mcp add windows-login-monitor -- `"$exe`"" -ForegroundColor Green
Write-Host ""
Write-Host "Any MCP client works: run that exe as the command, and speak stdio to it."
Write-Host "For remote access, front it with a server of your own behind an"
Write-Host "authenticated edge - this program has no network listener."
