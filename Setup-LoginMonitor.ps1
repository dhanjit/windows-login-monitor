# =====================================================================
# Windows Login Monitor - One-shot setup
# Run as Administrator: right-click PowerShell -> Run as Administrator,
# then:  Set-ExecutionPolicy -Scope Process Bypass -Force
#        .\Setup-LoginMonitor.ps1
# =====================================================================

#Requires -RunAsAdministrator

Write-Host ""
Write-Host "=== Windows Login Monitor Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- Admin check (belt + suspenders, in case #Requires is bypassed) ---
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}

$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name

# --- Lock a path to SYSTEM + Administrators + the installing user ---
# C:\ hands every child an inherited "BUILTIN\Users: ReadAndExecute" ACE, so
# anything dropped in C:\Scripts is readable by every local account on the box.
# The ntfy topic is a password - anyone holding it can read your alerts - and
# it gets baked into LoginAlert.ps1, so that default is a disclosure. login.log
# is no better: it records who logged in and whether they were home. Break
# inheritance and name the three principals that actually need access. Same
# defect, same fix as #2, which was the MCP owner key in the install directory.
#
# The scheduled task runs as SYSTEM, which is why SYSTEM keeps FullControl -
# it has to read the script and append to the log.
function Set-RestrictiveAcl {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [switch]$Container
    )
    $item = Get-Item -LiteralPath $Path -Force
    # Build a fresh descriptor instead of editing the one Get-Acl hands back.
    # A new object carries no inherited ACEs, so there is nothing to strip:
    # protection plus the three rules below is the whole DACL.
    $acl = if ($Container) {
        New-Object System.Security.AccessControl.DirectorySecurity
    } else {
        New-Object System.Security.AccessControl.FileSecurity
    }
    # $true  = protect from inheritance.
    # $false = do NOT copy the inherited rules down first. They must disappear,
    #          not be preserved as explicit ACEs, or BUILTIN\Users survives.
    $acl.SetAccessRuleProtection($true, $false)

    $inherit = if ($Container) { "ContainerInherit, ObjectInherit" } else { "None" }
    foreach ($id in @("NT AUTHORITY\SYSTEM", "BUILTIN\Administrators", $me)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $id, "FullControl", $inherit, "None", "Allow")))
    }
    # Persisted through the object's own method rather than Set-Acl. The cmdlet
    # writes every section of the descriptor, the audit SACL included, and that
    # needs SeSecurityPrivilege - it fails with PrivilegeNotHeldException even
    # for a file you own. SetAccessControl writes only the DACL, which is all
    # this changes.
    $item.SetAccessControl($acl)
}

# --- Prompt: phone IP ---
do {
    $phoneIP = Read-Host "Your phone's local IP (e.g. 192.168.1.50)"
} while (-not ($phoneIP -match '^(\d{1,3}\.){3}\d{1,3}$'))

# --- Prompt: ntfy topic ---
$defaultTopic = "pc-alert-" + ([guid]::NewGuid().ToString().Substring(0,8))
$topic = Read-Host "ntfy topic name [Enter for: $defaultTopic]"
if ([string]::IsNullOrWhiteSpace($topic)) { $topic = $defaultTopic }

# --- Create C:\Scripts ---
$scriptDir = "C:\Scripts"
if (-not (Test-Path $scriptDir)) {
    New-Item -ItemType Directory -Path $scriptDir | Out-Null
    Write-Host "Created $scriptDir" -ForegroundColor Green
}
# Unconditionally, not just on create: a folder left by an earlier run still
# carries the inherited ACL, and re-running setup is how you'd expect to fix it.
Set-RestrictiveAcl -Path $scriptDir -Container
Write-Host "Restricted $scriptDir to SYSTEM, Administrators and $me" -ForegroundColor Green

# --- Write the monitor script (config baked in) ---
$monitorScript = @"
`$PhoneIP   = "$phoneIP"
`$NtfyTopic = "$topic"
`$LogFile   = "C:\Scripts\login.log"

# Ping phone twice; if it responds, assume the user is home
`$phoneHome = Test-Connection -ComputerName `$PhoneIP -Count 2 -Quiet ``
             -ErrorAction SilentlyContinue

# Task runs as SYSTEM, so `$env:USERNAME would be "SYSTEM" — query the actual interactive user instead.
`$ciUser = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction SilentlyContinue).UserName
if (`$ciUser) { `$user = (`$ciUser -split '\\')[-1] } else { `$user = `$env:USERNAME }
`$pc   = `$env:COMPUTERNAME
`$time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

"`$time | user=`$user | phoneHome=`$phoneHome" | Add-Content `$LogFile

if (-not `$phoneHome) {
    `$body = "Login on `$pc by `$user at `$time"
    try {
        Invoke-RestMethod -Uri "https://ntfy.sh/`$NtfyTopic" -Method Post ``
            -Body `$body -Headers @{
                "Title"    = "PC Login Alert"
                "Priority" = "high"
                "Tags"     = "warning,computer"
            } | Out-Null
    } catch {
        "`$time | NTFY FAILED: `$_" | Add-Content `$LogFile
    }
}
"@

$monitorPath = Join-Path $scriptDir "LoginAlert.ps1"
Set-Content -Path $monitorPath -Value $monitorScript -Encoding UTF8
# This is the one file that has to keep the topic - the task reads it every
# logon - so it gets the restricted ACL in its own right, not just by
# inheritance from the folder.
Set-RestrictiveAcl -Path $monitorPath
Write-Host "Created $monitorPath (access-restricted)" -ForegroundColor Green

# --- Register scheduled task (replace if present) ---
$taskName = "LoginAlert"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing $taskName task" -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$monitorPath`""

# Event 4801 (workstation unlock) is only logged when this audit subcategory is enabled.
# Off by default on most Windows installs — turn it on so the unlock trigger has something to fire on.
auditpol /set /subcategory:"Other Logon/Logoff Events" /success:enable | Out-Null
Write-Host "Enabled audit policy: Other Logon/Logoff Events (Success)" -ForegroundColor Green

# Trigger 1: logon (catches sign-in after reboot / sign-out / fast-user-switch)
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: workstation unlock (Security event 4801) — built via CIM since
# New-ScheduledTaskTrigger doesn't expose event-based triggers directly.
$cimClass = Get-CimClass -ClassName MSFT_TaskEventTrigger `
    -Namespace Root/Microsoft/Windows/TaskScheduler
$unlockTrigger = New-CimInstance -CimClass $cimClass -ClientOnly
$unlockTrigger.Enabled = $true
$unlockTrigger.Subscription =
    "<QueryList><Query Id='0' Path='Security'><Select Path='Security'>*[System[EventID=4801]]</Select></Query></QueryList>"

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger @($logonTrigger, $unlockTrigger) `
    -Principal $principal -Settings $settings | Out-Null
Write-Host "Registered scheduled task: $taskName (logon + unlock triggers)" -ForegroundColor Green

# --- Save config summary ---
# Deliberately without the topic. This file is written once and read by
# nothing, so a copy of the topic here would be a second cleartext credential
# sitting around forever with no reader - exactly the defect in #2. The topic
# is printed below and lives in LoginAlert.ps1, which needs it; one copy in a
# file that has a reason to hold it beats two.
$configPath = Join-Path $scriptDir "login-monitor-config.txt"
@"
=== Windows Login Monitor ===
Phone IP:       $phoneIP
Monitor script: $monitorPath
Log file:       $scriptDir\login.log
Task name:      $taskName
Triggers:       logon + workstation unlock (Security event 4801)
Audit policy:   "Other Logon/Logoff Events" was enabled by setup
Permissions:    $scriptDir is restricted to SYSTEM, Administrators and $me

The ntfy topic is a password - anyone who knows it can read your alerts - so
it is not recorded here. Setup printed it once when it ran, and it is the
`$NtfyTopic line in $monitorPath.

To receive alerts on your phone:
  1. Install the 'ntfy' app (Play Store / App Store)
  2. Subscribe to the topic printed at the end of setup

To uninstall:
  Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false
  Remove-Item C:\Scripts\LoginAlert.ps1
"@ | Set-Content -Path $configPath -Encoding UTF8
# No topic in here, but it still gives up the phone IP and the layout.
Set-RestrictiveAcl -Path $configPath
Write-Host "Saved config to $configPath (access-restricted)" -ForegroundColor Green

# --- Test notification ---
Write-Host ""
Write-Host "=== Sending test notification ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "https://ntfy.sh/$topic" -Method Post `
        -Body "Setup complete on $env:COMPUTERNAME. If you see this, ntfy is working." `
        -Headers @{
            "Title"    = "Setup Test"
            "Priority" = "default"
            "Tags"     = "white_check_mark"
        } | Out-Null
    Write-Host "Test sent. Check your phone." -ForegroundColor Green
} catch {
    Write-Host "Test failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== YOUR NTFY TOPIC ===" -ForegroundColor Cyan
Write-Host "  $topic" -ForegroundColor Green
Write-Host ""
Write-Host "Treat it as a password: anyone who knows it can read your alerts."
Write-Host "This is the only place setup shows it - it is not written to the"
Write-Host "config file. Store it somewhere real (a password manager) now. If"
Write-Host "you lose it, it is the `$NtfyTopic line in $monitorPath."
Write-Host ""
Write-Host "=== NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "1. Install 'ntfy' on your phone, subscribe to the topic above"
Write-Host "2. In your router, set a DHCP reservation so your phone always gets $phoneIP"
Write-Host "3. Test: lock PC (Win+L), unlock - no alert (phone is home)"
Write-Host "4. Test: turn off phone WiFi, unlock - you should get an alert"
Write-Host ""
Write-Host "Logs:   C:\Scripts\login.log"
Write-Host "Config: $configPath"
Write-Host ""
