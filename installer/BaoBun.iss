; BaoBun Control Panel - Windows installer (Inno Setup 6, https://jrsoftware.org/isinfo.php)
; Build with build_installer.bat, which builds the exe first and stamps the version.
;
; * Installs for the current user (no admin needed) into %LOCALAPPDATA%\Programs\BaoBun,
;   or for everyone into Program Files if chosen (BaoBun then keeps its data in
;   %LOCALAPPDATA%\BaoBun). Servers, settings and backups are never touched by
;   installing, updating or uninstalling.
; * Updating while BaoBun runs is fine: the running program file is renamed out of
;   the way and the Watchdog moves to the new version by itself when nobody plays.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "BaoBun Control Panel"
#define MyAppExe "BaoBunCP.exe"

[Setup]
AppId={{94E12732-58B1-46D3-8C06-6E813478A292}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=BaoBun
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\BaoBun
DefaultGroupName=BaoBun
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\build\installer
OutputBaseFilename=BaoBun-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\out\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; also after a silent update started from inside BaoBun, so it comes back by itself
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,BaoBun}"; Flags: nowait postinstall

[UninstallDelete]
Type: files; Name: "{app}\BaoBunCP.old-*.exe"

[Code]
procedure DeleteOldCopies();
var
  F: TFindRec;
begin
  if FindFirst(ExpandConstant('{app}\BaoBunCP.old-*.exe'), F) then
  try
    repeat
      DeleteFile(ExpandConstant('{app}\') + F.Name);   { still-running ones just stay }
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Exe, Old: String;
begin
  Result := '';
  DeleteOldCopies();
  Exe := ExpandConstant('{app}\{#MyAppExe}');
  if FileExists(Exe) and not DeleteFile(Exe) then
  begin
    { BaoBun (or its Watchdog) is running: Windows lets a running program be renamed }
    Old := ExpandConstant('{app}\BaoBunCP.old-') + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '.exe';
    if not RenameFile(Exe, Old) then
      Result := 'BaoBun is running and its program file could not be replaced. Close BaoBun and try again.';
  end;
end;
