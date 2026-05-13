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
Write-Host "Created $monitorPath" -ForegroundColor Green

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
$configPath = Join-Path $scriptDir "login-monitor-config.txt"
@"
=== Windows Login Monitor ===
Phone IP:       $phoneIP
ntfy topic:     $topic
ntfy URL:       https://ntfy.sh/$topic
Monitor script: $monitorPath
Log file:       $scriptDir\login.log
Task name:      $taskName
Triggers:       logon + workstation unlock (Security event 4801)
Audit policy:   "Other Logon/Logoff Events" was enabled by setup

To receive alerts on your phone:
  1. Install the 'ntfy' app (Play Store / App Store)
  2. Add subscription with topic name: $topic

To uninstall:
  Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false
  Remove-Item C:\Scripts\LoginAlert.ps1
"@ | Set-Content -Path $configPath -Encoding UTF8
Write-Host "Saved config to $configPath" -ForegroundColor Green

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
Write-Host "=== NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "1. Install 'ntfy' on your phone, subscribe to topic: $topic"
Write-Host "2. In your router, set a DHCP reservation so your phone always gets $phoneIP"
Write-Host "3. Test: lock PC (Win+L), unlock - no alert (phone is home)"
Write-Host "4. Test: turn off phone WiFi, unlock - you should get an alert"
Write-Host ""
Write-Host "Logs:   C:\Scripts\login.log"
Write-Host "Config: $configPath"
Write-Host ""
