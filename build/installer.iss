; Inno Setup script for Windows Login Monitor MCP
; Build:  iscc.exe /DAppVersion=0.1.0 build\installer.iss
; Output: dist\WindowsLoginMonitorMcp-Setup-<version>.exe

#define MyAppName       "Windows Login Monitor MCP"
#define MyAppPublisher  "Dhanjit"
#define MyAppId         "{{B85D5C03-0F1D-4BF6-9C0F-C9D7E04F86B6}"
#define MyAppExeName    "windows-login-monitor-mcp.exe"
#define MyAppURL        "https://github.com/dhanjit/windows-login-monitor"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\WindowsLoginMonitorMcp
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=WindowsLoginMonitorMcp-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Require Windows 10 build 1809 (October 2018) or newer
MinVersion=10.0.17763
WizardStyle=modern
DisableDirPage=auto
DisableProgramGroupPage=yes
; The server is windowless, so Restart Manager has no window or console to
; close gracefully and gives up ("Some applications could not be shut down").
; Under /SUPPRESSMSGBOXES that box defaults to Abort, so every silent upgrade
; over a running install failed. Terminate instead, and do not let Setup
; relaunch the exe directly - setup-helper.ps1 starts it via the task, as the
; right user at the right run level.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\windows-login-monitor-mcp.exe";        DestDir: "{app}"; Flags: ignoreversion
Source: "setup-helper.ps1";                  DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall-helper.ps1";              DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-helper.ps1"" -InstallDir ""{app}"""; \
    StatusMsg: "Granting event log access, removing any old service..."; \
    Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall-helper.ps1"" -InstallDir ""{app}"""; \
    Flags: runhidden waituntilterminated

[UninstallDelete]
Type: files; Name: "{app}\.env"
Type: files; Name: "{app}\server.log"
Type: files; Name: "{app}\server.err.log"
; No longer written; still deleted, to clean up installs of 0.1.0 and earlier.
Type: files; Name: "{app}\FIRST-RUN.txt"

[Code]

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // Versions <= 0.1.3 ran the exe as a background service, which would hold
  // the file we are about to replace. Restart Manager cannot close it - it is
  // windowless - so stop the task first, then the process. Both are no-ops on
  // a clean install, and failures are ignored: if the exe really is locked,
  // Setup reports that itself rather than us guessing here.
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "LoginMonitorMcp"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM windows-login-monitor-mcp.exe /F',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
