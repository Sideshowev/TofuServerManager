; Tofu Server Manager - Windows installer (Inno Setup 6, https://jrsoftware.org/isinfo.php)
; Build with build_installer.bat, which builds the exe first and stamps the version.
;
; * Installs for the current user (no admin needed) into %LOCALAPPDATA%\Programs\TofuSM,
;   or for everyone into Program Files if chosen (Tofu then keeps its data in
;   %LOCALAPPDATA%\TofuSM). Servers, settings and backups are never touched by
;   installing, updating or uninstalling.
; * Updating while Tofu runs is fine: the running program file is renamed out of
;   the way and the Watchdog moves to the new version by itself when nobody plays.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "Tofu Server Manager"
#define MyAppExe "TofuSM.exe"

[Setup]
AppId={{94E12732-58B1-46D3-8C06-6E813478A292}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Tofu Server Manager
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\TofuSM
DefaultGroupName=Tofu Server Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\build\installer
OutputBaseFilename=TofuSM-Setup-{#MyAppVersion}
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
; also after a silent update started from inside Tofu, so it comes back by itself
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,Tofu}"; Flags: nowait postinstall

[InstallDelete]
; The app used to be "BaoBun Control Panel" (same AppId, so an update lands in the same
; folder and keeps its servers and settings): remove the old program file and shortcuts.
Type: files; Name: "{app}\BaoBunCP.exe"
Type: files; Name: "{app}\BaoBunCP.old-*.exe"
Type: files; Name: "{autoprograms}\BaoBun Control Panel.lnk"
Type: files; Name: "{autodesktop}\BaoBun Control Panel.lnk"

[UninstallDelete]
Type: files; Name: "{app}\TofuSM.old-*.exe"

[Code]
procedure DeleteOldCopies();
var
  F: TFindRec;
begin
  if FindFirst(ExpandConstant('{app}\TofuSM.old-*.exe'), F) then
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
    { Tofu (or its Watchdog) is running: Windows lets a running program be renamed }
    Old := ExpandConstant('{app}\TofuSM.old-') + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '.exe';
    if not RenameFile(Exe, Old) then
      Result := 'Tofu is running and its program file could not be replaced. Close Tofu and try again.';
  end;
end;
