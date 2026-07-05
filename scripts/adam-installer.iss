; Adam - Inno Setup installer (ROADMAP P3-1).
;
; Do NOT compile this directly: run scripts\build-installer.ps1. That wrapper
; stages the guarded release ZIP (every make_release.py guard runs fail-closed,
; so the installer payload can never carry secrets or owner data) and passes:
;   /DStageDir=<extracted release tree, brain\ moved out>
;   /DBrainDir=<the brain\ template tree>
;   /DAppVersion=<x.y.z>
;   /DOutDir=<repo dist\>
;
; Design notes:
;  - Per-user install ({userpf} = %LOCALAPPDATA%\Programs), PrivilegesRequired=
;    lowest: no UAC, and the in-app updater keeps working because the install
;    dir stays user-writable.
;  - brain\ installs onlyifdoesntexist: a reinstall/upgrade over an existing
;    install must never clobber a user's edited brain files. Program-file
;    upgrades between releases go through the in-app updater (UPDATE.cmd),
;    which has the merge/backup logic; the installer is for first install.
;  - Silent mode works for winget: /VERYSILENT /SUPPRESSMSGBOXES installs,
;    "unins000.exe /VERYSILENT" uninstalls. The post-install "Set up Adam now"
;    run is skipifsilent.
;  - Uninstall keeps user data (data\, .env, logs) in place by design — the
;    "what stays after uninstall" consumer story. It also removes the
;    wizard-made shortcuts (add-app-shortcut.ps1 creates the same names).
;  - Signing: once the Azure Artifact Signing cert exists, add a SignTool=
;    directive here and sign via build-installer.ps1 — nothing else changes.

#ifndef StageDir
  #error Pass /DStageDir=... — run scripts\build-installer.ps1, not ISCC directly
#endif
#ifndef BrainDir
  #error Pass /DBrainDir=... — run scripts\build-installer.ps1, not ISCC directly
#endif
#ifndef AppVersion
  #error Pass /DAppVersion=...
#endif
#ifndef OutDir
  #error Pass /DOutDir=...
#endif

[Setup]
; Stable install identity — never change this GUID or upgrades become new installs.
AppId={{6E9A2C54-71B3-4A8D-9F2E-0C5D8B7A4E13}
AppName=Adam
AppVersion={#AppVersion}
AppPublisher=Zachary Campos
AppSupportURL=https://github.com/DCom17/adam/issues
AppUpdatesURL=https://github.com/DCom17/adam-releases/releases
DefaultDirName={userpf}\Adam
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=adam-setup-v{#AppVersion}
SetupIconFile={#StageDir}\web\icon.ico
UninstallDisplayIcon={app}\web\icon.ico
UninstallDisplayName=Adam
LicenseFile={#StageDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription=Adam - your local AI assistant

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; brain\ is the user's editable template — never overwrite an existing copy,
; and never delete it on uninstall (a vault pointed at brain\ holds real notes).
Source: "{#BrainDir}\*"; DestDir: "{app}\brain"; Flags: recursesubdirs createallsubdirs onlyifdoesntexist uninsneveruninstall

[Icons]
; Same pinnable app shortcut add-app-shortcut.ps1 makes: wscript -> adam-app.vbs
; (Windows 11 won't pin a .cmd/console launcher).
Name: "{userprograms}\Adam\Adam"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\adam-app.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\web\icon.ico"; Comment: "Adam"
Name: "{userprograms}\Adam\Set up Adam"; Filename: "{app}\SETUP.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\web\icon.ico"; Comment: "Run the Adam setup wizard"
Name: "{userprograms}\Adam\Update Adam"; Filename: "{app}\UPDATE.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\web\icon.ico"; Comment: "Get the latest Adam"
Name: "{userdesktop}\Adam"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\adam-app.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\web\icon.ico"; Comment: "Adam"; Tasks: desktopicon

[Run]
Filename: "{app}\SETUP.cmd"; Description: "Set up Adam now (recommended)"; Flags: postinstall shellexec skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
; Wizard-made shortcuts share these names (add-app-shortcut.ps1); Inno removes
; its own icons automatically, this catches the wizard's copies too.
Type: files; Name: "{userdesktop}\Adam.lnk"
Type: files; Name: "{userprograms}\Adam\Adam.lnk"
; Python bytecode caches created at runtime (so the app dir can actually empty).
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\routers\__pycache__"
Type: filesandordirs; Name: "{app}\scripts\__pycache__"
; NOTE: data\ and .env are deliberately NOT deleted — the user's token,
; settings, backups and logs survive an uninstall (documented consumer story).
