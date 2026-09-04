# Runs from Inno's [UninstallRun].
# The server has no service and no port, so this only undoes the group
# membership and clears out anything a pre-0.1.4 install left behind.
param([string]$InstallDir)
$ErrorActionPreference = "SilentlyContinue"
$taskName = "LoginMonitorMcp"
$me       = "$env:USERDOMAIN\$env:USERNAME"

# Versions <= 0.1.3 registered a background service.
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Get-Process windows-login-monitor-mcp -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# Best-effort: remove from Event Log Readers (leave there if other tools rely on it)
try { Remove-LocalGroupMember -Group "Event Log Readers" -Member $me -ErrorAction SilentlyContinue } catch {}
