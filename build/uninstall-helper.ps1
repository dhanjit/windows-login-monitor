# Runs from Inno's [UninstallRun]. Removes scheduled task + group membership.
param([string]$InstallDir)
$ErrorActionPreference = "SilentlyContinue"
$taskName = "LoginMonitorMcp"
$me       = "$env:USERDOMAIN\$env:USERNAME"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
# Best-effort: remove from Event Log Readers (leave there if other tools rely on it)
try { Remove-LocalGroupMember -Group "Event Log Readers" -Member $me -ErrorAction SilentlyContinue } catch {}
# Kill any lingering exe holding 8765
Get-Process windows-login-monitor-mcp -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
