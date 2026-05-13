# Build pipeline: PyInstaller bundle + Inno Setup installer
# From repo root:  .\build\build.ps1 [-Version 0.1.0]

[CmdletBinding()]
param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$srv  = Join-Path $repo "mcp-server"
$build = $PSScriptRoot
$dist = Join-Path $repo "dist"

Write-Host "Repo:    $repo"
Write-Host "Version: $Version"

# --- 1. PyInstaller bundle ---
Write-Host "`n[1/3] Bundling server.py via PyInstaller..." -ForegroundColor Cyan
$python = Join-Path $srv ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "venv not found at $python -- run 'python -m venv .venv' inside mcp-server and pip install -r requirements.txt + pyinstaller"
}
# PyInstaller writes info logs to stderr; PS treats them as errors. Suppress that.
$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = "Continue"
& $python -m PyInstaller (Join-Path $build "server.spec") --clean --distpath $dist --workpath (Join-Path $build "work") 2>&1 |
    ForEach-Object {
        $line = "$_"
        if ($line -match "^\d+\s+(ERROR|WARNING):" -or $line -match "^Traceback") {
            Write-Host $line -ForegroundColor Yellow
        }
    }
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller exited $LASTEXITCODE" }
$exePath = Join-Path $dist "windows-login-monitor-mcp.exe"
if (-not (Test-Path $exePath)) { throw "PyInstaller did not produce $exePath" }
$exeSize = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "  -> $exePath  ($exeSize MB)" -ForegroundColor Green

# --- 2. Inno Setup installer ---
Write-Host "`n[2/3] Building Inno Setup installer..." -ForegroundColor Cyan
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "ISCC.exe not found. Install Inno Setup 6 (winget install JRSoftware.InnoSetup)." }
$ErrorActionPreference = "Continue"
& $iscc "/DAppVersion=$Version" (Join-Path $build "installer.iss") 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { throw "ISCC exited $LASTEXITCODE" }
$installer = Join-Path $dist "WindowsLoginMonitorMcp-Setup-$Version.exe"
if (-not (Test-Path $installer)) { throw "Inno Setup did not produce $installer" }
$installerSize = [math]::Round((Get-Item $installer).Length / 1MB, 1)
Write-Host "  -> $installer  ($installerSize MB)" -ForegroundColor Green

# --- 3. SHA256 hash for winget manifest ---
Write-Host "`n[3/3] SHA256 (for winget manifest)..." -ForegroundColor Cyan
$hash = (Get-FileHash $installer -Algorithm SHA256).Hash
Write-Host "  $hash"

Write-Host "`nDONE." -ForegroundColor Green
Write-Host "Install with:    $installer"
Write-Host "Uninstall via:   'Apps & features' or 'winget uninstall ...'"
