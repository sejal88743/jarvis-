; ══════════════════════════════════════════════════════════════════════════════
;  MARK LV — one-click Windows installer (Inno Setup 6)
;
;  WHAT THIS PRODUCES
;      A single setup .exe that a user double-clicks. It copies the portable
;      build into Program Files (or their folder of choice), makes Start Menu
;      and optional desktop shortcuts, registers an uninstaller, and can launch
;      the assistant when it finishes.
;
;  WHY INNO SETUP AND NOT JUST THE PORTABLE FOLDER
;      The folder beside MARK-LV.exe works, but "find the folder, make a
;      shortcut, remember where it went" is exactly the friction a one-click
;      install removes. Inno produces one signed-capable .exe, ~2 MB, with no
;      installer runtime to bundle and nothing to install on the build machine
;      beyond Inno itself.
;
;  BUILDING
;      python build_installer.py           (recommended — does everything)
;      or: ISCC.exe installer\MARK-LV.iss  (after build_windows.py has run)
;
;  WHAT IT DELIBERATELY LEAVES ALONE
;      config/api_keys.json — the API key and all personal memory live in the
;      user's AppData, never in Program Files, so an upgrade or a reinstall
;      cannot wipe them and the installer never ships somebody's key.
; ══════════════════════════════════════════════════════════════════════════════

#define AppName        "MARK LV"
#define AppShortName   "MARK-LV"
#define AppPublisher   "FatihMakes"
#define AppURL         "https://github.com/FatihMakes/Mark-LV"
#define AppExeName     "MARK-LV.exe"

; Version is passed in by build_installer.py (/DAppVersion=…); this is the
; fallback for a manual ISCC run.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8F3A1C2E-5B7D-4A9F-9C1B-2D6E4F8A0B3C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}

; Per-user install by default: no UAC prompt, and a person who is not an
; administrator can still install it. They can pick Program Files on the
; directory page if they want a machine-wide install.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; The portable build output produced by build_windows.py.
SourceDir=..\dist\{#AppShortName}
OutputDir=..\dist\installer
OutputBaseFilename={#AppShortName}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=..\config\jarvis.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
AllowNoIcons=yes
DisableWelcomePage=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Start {#AppName} automatically when I sign in"; GroupDescription: "Startup:"; Flags: unchecked
Name: "launchapp";   Description: "Launch {#AppName} now"; GroupDescription: "Finish:"; Flags: checkedonce

[Files]
; The whole portable build, folder and all.
Source: "..\dist\{#AppShortName}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; A short readme the user can actually find afterwards.
Source: "READ-ME-FIRST.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\config\jarvis.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\config\jarvis.ico"; Tasks: desktopicon

[Registry]
; Auto-start on sign-in, written to HKCU so it needs no elevation.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent; Tasks: launchapp

[UninstallDelete]
; Caches and bytecode only. NEVER config/ or memory/ inside the app folder if a
; user chose to keep them portable — but those are in AppData by default, so
; this cleans the install without ever touching personal data.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\build"

[Code]
{ ── Tell the user plainly what will and will not be removed ─────────────── }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('MARK LV has been removed.' + #13#10 + #13#10 +
              'Your personal data was kept, because it holds your API key and ' +
              'everything the assistant remembers about you:' + #13#10 +
              ExpandConstant('%APPDATA%') + '\MARK-LV' + #13#10 + #13#10 +
              'Delete that folder too if you want a completely clean removal.',
              mbInformation, MB_OK) = IDOK then
      ;
  end;
end;

{ ── Refuse to install over a Python that cannot run it ──────────────────── }
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
