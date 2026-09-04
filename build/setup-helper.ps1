# Runs in the [Run] section of the Inno installer, post-extract, elevated.
# Args:
#   -InstallDir <path>         where windows-login-monitor-mcp.exe lives
#   -PublicHost <host> | empty (e.g. blackreach.dhanjit.me); empty means local-only

param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [string]$PublicHost = ""
)

$ErrorActionPreference = "Stop"
$exe      = Join-Path $InstallDir "windows-login-monitor-mcp.exe"
$envFile  = Join-Path $InstallDir ".env"
$taskName = "LoginMonitorMcp"
$me       = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "Configuring Windows Login Monitor MCP for $me"

# Lock a secrets file to SYSTEM + Administrators + the installing user.
# Anything under Program Files inherits an ACE granting BUILTIN\Users read, so
# the owner key would otherwise be readable by every local account. Creates the
# file first, so the ACL is in place *before* a secret is written into it.
# The user needs an ACE of their own: the scheduled task runs non-elevated and
# reads .env with the limited token, where Administrators does not apply.
function Protect-SecretFile {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$OwnerAccount
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }
    # Well-known SIDs, not names: "BUILTIN\Administrators" is localised.
    $ids = @(
        (New-Object System.Security.Principal.SecurityIdentifier "S-1-5-18"),     # LOCAL SYSTEM
        (New-Object System.Security.Principal.SecurityIdentifier "S-1-5-32-544")  # Administrators
    )
    try {
        $ids += (New-Object System.Security.Principal.NTAccount $OwnerAccount).Translate(
            [System.Security.Principal.SecurityIdentifier])
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)   # stop inheriting; drop inherited ACEs
        foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRule($rule) }
        foreach ($id in $ids) {
            $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                $id,
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                [System.Security.AccessControl.AccessControlType]::Allow)))
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    } catch {
        # Never fall through to writing a secret into a world-readable file.
        throw "Could not restrict permissions on $Path ($($_.Exception.Message)). Aborting so the owner key is not left readable by all local users."
    }
}

# --- 1. Reuse or generate owner key (accept legacy WLM_MCP_TOKEN too) ---
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
}

# --- 2. Write .env (no BOM; python-dotenv chokes on it) ---
# ACL first, contents second: the key must never exist in a world-readable file,
# not even for the moment between creation and write.
Protect-SecretFile -Path $envFile -OwnerAccount $me
$publicUrl = if ($PublicHost) { "https://$PublicHost" } else { "http://127.0.0.1:8765" }
$envLines = @(
    "WLM_MCP_OWNER_KEY=$ownerKey",
    "WLM_MCP_PUBLIC_URL=$publicUrl"
)
if ($PublicHost) {
    $envLines += "WLM_MCP_HOST=0.0.0.0"
    $envLines += "WLM_MCP_ALLOWED_HOSTS=$PublicHost"
}
[System.IO.File]::WriteAllText($envFile, ($envLines -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))

# --- 3. Add the installing user to Event Log Readers (Security log access) ---
try {
    if (-not (Get-LocalGroupMember -Group "Event Log Readers" -Member $me -ErrorAction SilentlyContinue)) {
        Add-LocalGroupMember -Group "Event Log Readers" -Member $me
    }
} catch {
    Write-Host "WARN: could not add $me to Event Log Readers: $($_.Exception.Message)"
}

# --- 4. (Re)register the scheduled task as that user, autostart at logon ---
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action    = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $InstallDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $me
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null

# --- 5. Start it now ---
Start-ScheduledTask -TaskName $taskName

# --- 5b. Post-install probe: confirm the server actually came up and speaks MCP+OAuth ---
$port  = 8765
$probes = [ordered]@{
    "Health probe"             = $false
    "OAuth resource metadata"  = $false
    "401 with WWW-Authenticate"= $false
}
$probeError = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$port/healthz" -TimeoutSec 1
        if ($h.ok) { $probes["Health probe"] = $true; break }
    } catch { $probeError = $_.Exception.Message }
}
if ($probes["Health probe"]) {
    try {
        $rm = Invoke-RestMethod "http://127.0.0.1:$port/.well-known/oauth-protected-resource/mcp" -TimeoutSec 5
        if ($rm.resource -and $rm.authorization_servers) { $probes["OAuth resource metadata"] = $true }
    } catch { $probeError = $_.Exception.Message }
    try {
        Invoke-WebRequest "http://127.0.0.1:$port/mcp" -Method Post `
            -Body '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup-probe","version":"0"}}}' `
            -Headers @{ "Content-Type"="application/json"; "Accept"="application/json, text/event-stream" } `
            -UseBasicParsing -TimeoutSec 5 | Out-Null
    } catch [System.Net.WebException] {
        $sc = [int]$_.Exception.Response.StatusCode
        $wa = $_.Exception.Response.Headers["WWW-Authenticate"]
        if ($sc -eq 401 -and $wa -and $wa -match 'resource_metadata=') { $probes["401 with WWW-Authenticate"] = $true }
    }
}

Write-Host ""
Write-Host "=== Post-install verification ===" -ForegroundColor Cyan
$allOk = $true
foreach ($k in $probes.Keys) {
    if ($probes[$k]) { Write-Host "  [OK]   $k" -ForegroundColor Green }
    else { Write-Host "  [FAIL] $k" -ForegroundColor Red; $allOk = $false }
}
if (-not $allOk) {
    Write-Host ""
    Write-Host "Server didn't pass all probes. Check:" -ForegroundColor Yellow
    Write-Host "  - Task state:  Get-ScheduledTask -TaskName $taskName | Select-Object State, @{n='LastResult';e={(`$_ | Get-ScheduledTaskInfo).LastTaskResult}}"
    Write-Host "  - Port 8765:   Get-NetTCPConnection -LocalPort 8765"
    Write-Host "  - Last error:  $probeError"
}

# --- 6. Remove FIRST-RUN.txt from installs of 0.1.0 and earlier ---
# It held a second cleartext copy of the owner key, nothing ever read it back,
# and it inherited Program Files' ACL - readable by every local account.
# The key now lives in .env only; Show-OwnerKey.ps1 displays it on demand.
$legacyInfoFile = Join-Path $InstallDir "FIRST-RUN.txt"
if (Test-Path -LiteralPath $legacyInfoFile) {
    Remove-Item -LiteralPath $legacyInfoFile -Force -ErrorAction SilentlyContinue
    Write-Host "Removed legacy $legacyInfoFile (it stored the owner key in cleartext)."
}

# Console only - this output is not captured to a file anywhere.
Write-Host "Owner key: $ownerKey"
Write-Host "Show it again: powershell -File `"$InstallDir\Show-OwnerKey.ps1`""
