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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\windows-login-monitor-mcp.exe";        DestDir: "{app}"; Flags: ignoreversion
Source: "setup-helper.ps1";                  DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall-helper.ps1";              DestDir: "{app}"; Flags: ignoreversion
Source: "Show-OwnerKey.ps1";                 DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-helper.ps1"" -InstallDir ""{app}"" -PublicHost ""{code:GetPublicHost}"""; \
    StatusMsg: "Generating token, registering scheduled task..."; \
    Flags: runhidden waituntilterminated

; Shows the key on screen, read from .env. Deliberately not a file: a second
; cleartext copy under {app} would be readable by every local account.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Show-OwnerKey.ps1"" -InstallDir ""{app}"""; \
    Description: "Show the owner key and connector setup info"; \
    Flags: postinstall skipifsilent nowait

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
var
  HostnamePage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  HostnamePage := CreateInputQueryPage(wpSelectDir,
    'Public hostname',
    'Where will this MCP be reachable?',
    'You host the server locally; you choose how to expose it - Cloudflare Tunnel, Tailscale Funnel, ngrok, Caddy + DDNS, anything that proxies HTTPS to 127.0.0.1:8765.' + #13#10 +
    'Enter the public hostname your tunnel/proxy will use, or leave blank for local-only (you can edit .env later).');
  HostnamePage.Add('Public hostname (e.g. mcp.example.com):', False);
  HostnamePage.Values[0] := '';
end;

function GetPublicHost(Param: string): string;
begin
  if Assigned(HostnamePage) then
    Result := Trim(HostnamePage.Values[0])
  else
    Result := '';
end;
