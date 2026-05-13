# =====================================================================
# Windows Login Monitor MCP Server - installer
# Run as Administrator from this directory:
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\Install-McpServer.ps1
# =====================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Windows Login Monitor MCP Server Installer ===" -ForegroundColor Cyan
Write-Host ""

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}

# --- Locate Python ---
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $python) {
    Write-Host "ERROR: Python not found on PATH. Install Python 3.10+ (winget install Python.Python.3.13) and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "Found Python: $python" -ForegroundColor Green

# --- Decide install location ---
$installDir = "C:\Scripts\mcp-server"
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir | Out-Null
    Write-Host "Created $installDir" -ForegroundColor Green
}

# --- Copy server files from this script's directory ---
$srcDir = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($f in @("server.py", "auth_provider.py", "requirements.txt")) {
    Copy-Item -Path (Join-Path $srcDir $f) -Destination (Join-Path $installDir $f) -Force
}
Write-Host "Copied server.py, auth_provider.py, requirements.txt to $installDir" -ForegroundColor Green

# --- Create / reuse venv ---
$venvDir = Join-Path $installDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    & $python -m venv $venvDir
}
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $installDir "requirements.txt") --quiet
Write-Host "Dependencies installed." -ForegroundColor Green

# --- Prompt for public hostname (optional) ---
$publicHost = Read-Host "Public hostname your tunnel/proxy will expose this on (blank = local-only)"

# --- Generate (or reuse) owner key ---
$envFile = Join-Path $installDir ".env"
$ownerKey = $null
if (Test-Path $envFile) {
    $raw = Get-Content $envFile -Raw
    $m = [regex]::Match($raw, 'WLM_MCP_OWNER_KEY\s*=\s*(\S+)')
    if ($m.Success) { $ownerKey = $m.Groups[1].Value }
    if (-not $ownerKey) {
        $m = [regex]::Match($raw, 'WLM_MCP_TOKEN\s*=\s*(\S+)')
        if ($m.Success) { $ownerKey = $m.Groups[1].Value }
    }
}
if (-not $ownerKey) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $ownerKey = ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+','-').Replace('/','_')
    Write-Host "Generated new owner key" -ForegroundColor Green
} else {
    Write-Host "Reusing existing owner key from $envFile" -ForegroundColor Yellow
}

# Compose .env. Write WITHOUT BOM - python-dotenv treats the BOM as part of
# the variable name.
$publicUrl = if ($publicHost) { "https://$publicHost" } else { "http://127.0.0.1:8765" }
$envLines = @(
    "WLM_MCP_OWNER_KEY=$ownerKey",
    "WLM_MCP_PUBLIC_URL=$publicUrl"
)
if ($publicHost) {
    $envLines += "WLM_MCP_HOST=0.0.0.0"
    $envLines += "WLM_MCP_ALLOWED_HOSTS=$publicHost"
}
[System.IO.File]::WriteAllText($envFile, ($envLines -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
Write-Host "Wrote $envFile" -ForegroundColor Green

# --- Add current user to Event Log Readers (so Get-WinEvent on Security works without admin) ---
$me = "$env:USERDOMAIN\$env:USERNAME"
$grp = "Event Log Readers"
try {
    $member = Get-LocalGroupMember -Group $grp -Member $me -ErrorAction SilentlyContinue
    if (-not $member) {
        Add-LocalGroupMember -Group $grp -Member $me
        Write-Host "Added $me to '$grp' (Security event log read access)" -ForegroundColor Green
    } else {
        Write-Host "$me already in '$grp'" -ForegroundColor Yellow
    }
} catch {
    Write-Host "WARN: could not add user to '$grp': $($_.Exception.Message)" -ForegroundColor Yellow
}

# --- Register scheduled task (autostart at logon) ---
$taskName = "LoginMonitorMcp"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing $taskName task" -ForegroundColor Yellow
}

# Wrap python in pythonw to run windowless; redirect stdout/stderr to a log file
$pythonw = Join-Path $venvDir "Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $venvPython }  # fallback

$logFile = Join-Path $installDir "server.log"
$serverPy = Join-Path $installDir "server.py"

# Wrapper that captures stdout/stderr (Start-Process can't easily redirect both)
$wrapperPath = Join-Path $installDir "run-server.ps1"
@"
Set-Location -Path '$installDir'
& '$venvPython' '$serverPy' *>> '$logFile'
"@ | Set-Content -Path $wrapperPath -Encoding UTF8

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapperPath`"" `
    -WorkingDirectory $installDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null
Write-Host "Registered scheduled task: $taskName (autostart on logon as $me)" -ForegroundColor Green

# --- Start it now ---
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

# --- Smoke test: hit /healthz (no auth required) ---
$port = 8765
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2
        if ($r.ok) { $ok = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if ($ok) {
    Write-Host "Health check OK: server is listening on http://127.0.0.1:$port" -ForegroundColor Green
} else {
    Write-Host "WARN: server did not respond to /healthz within 5s. Check $logFile" -ForegroundColor Yellow
}

# --- Final summary ---
Write-Host ""
Write-Host "=== INSTALLED ===" -ForegroundColor Cyan
Write-Host "Server dir:       $installDir"
Write-Host "Owner key:        $ownerKey"
Write-Host "Local endpoint:   http://127.0.0.1:$port/mcp"
Write-Host "Health check:     http://127.0.0.1:$port/healthz"
Write-Host "Log file:         $logFile"
Write-Host "Scheduled task:   $taskName"
if ($publicHost) {
    Write-Host "Public endpoint:  https://$publicHost/mcp"
    Write-Host "Issuer URL:       https://$publicHost"
    Write-Host "Bind:             0.0.0.0 (so a Docker-based cloudflared can reach via host.docker.internal)"
}
Write-Host ""
Write-Host "=== NEXT: expose http://127.0.0.1:$port to your agent ===" -ForegroundColor Cyan
if ($publicHost) {
    Write-Host "You said the public hostname is: $publicHost"
    Write-Host "Point any of the following at http://127.0.0.1:$port (or http://host.docker.internal:$port"
    Write-Host "if your tunnel runs in Docker):"
    Write-Host "  - Cloudflare Tunnel:  ingress rule -> $publicHost"
    Write-Host "  - Tailscale Funnel:   tailscale funnel $port"
    Write-Host "  - ngrok:              ngrok http $port"
    Write-Host "  - Caddy / nginx + DDNS / static IP"
    Write-Host ""
    Write-Host "Then in any MCP client (Claude.ai, Claude Code, Cursor, ...):"
    Write-Host "  URL: https://$publicHost/mcp"
    Write-Host "  Tap Connect; the browser opens the authorize page."
    Write-Host "  Paste the OWNER KEY above. Each client keeps its own OAuth"
    Write-Host "  token after that; refreshes silently."
} else {
    Write-Host "(No public hostname configured. Local-only mode.)"
    Write-Host "Re-run with a hostname to expose this MCP to remote agents."
}
Write-Host ""
Write-Host "Uninstall:"
Write-Host "  Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false"
Write-Host "  Remove-Item $installDir -Recurse -Force"
Write-Host ""
