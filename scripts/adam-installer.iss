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
;  - Signing: build-installer.ps1 -Sign passes /DSignBuild plus a
;    /Ssigntool=... definition; the [Setup] SignTool= below then signs the
;    installer AND the uninstaller. Unsigned builds simply omit -Sign.

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
#ifdef SignBuild
; The "signtool" name is defined on the ISCC command line by build-installer.ps1
; (-Sign): Azure Artifact Signing via signtool /dlib. Signs setup + uninstaller.
SignTool=signtool
SignedUninstaller=yes
#endif

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
; The optional voice package: a ~1 GB DOWNLOADED artifact (venv + Kokoro model),
; not user data — without these the app dir silently kept a gigabyte forever.
Type: filesandordirs; Name: "{app}\scripts\tts_server\.venv"
Type: files; Name: "{app}\scripts\tts_server\kokoro-v1.0.onnx"
Type: files; Name: "{app}\scripts\tts_server\voices-v1.0.bin"
; Updater leftovers (retired modules / interrupted atomic writes).
Type: files; Name: "{app}\*.removed"
Type: files; Name: "{app}\*.jvltmp"
Type: files; Name: "{app}\routers\*.removed"
Type: files; Name: "{app}\routers\*.jvltmp"
Type: files; Name: "{app}\scripts\*.removed"
Type: files; Name: "{app}\scripts\*.jvltmp"
; NOTE: data\ and .env are deliberately NOT deleted — the user's token,
; settings, backups and logs survive an uninstall (documented consumer story).

[Code]
{ Is the Adam server answering locally? (default port). Catches "install/
  uninstall while Adam is running" — mixed old/new code in memory, locked
  files — and the port-8000 fight with a second install. }
function AdamServerRunning(): Boolean;
var
  WinHttp: Variant;
begin
  Result := False;
  try
    WinHttp := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    WinHttp.SetTimeouts(400, 400, 400, 400);
    WinHttp.Open('GET', 'http://127.0.0.1:8000/ping', False);
    WinHttp.Send();
    Result := (WinHttp.Status = 200);
  except
    Result := False; { nothing listening — good }
  end;
end;

{ Does a DIFFERENT Adam install own the desktop shortcut? (A ZIP-install tester
  running this setup would otherwise get a second, empty Adam and a shortcut
  that silently stops pointing at their data — reads as total data loss.) }
function OtherInstallShortcutPath(): String;
var
  Shell, Lnk: Variant;
  LnkFile, Args: String;
begin
  Result := '';
  LnkFile := ExpandConstant('{userdesktop}\Adam.lnk');
  if not FileExists(LnkFile) then Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Lnk := Shell.CreateShortcut(LnkFile);
    Args := Lnk.Arguments;
    // The shortcut targets wscript + "<install>\scripts\adam-app.vbs" — if
    // that path isn't under OUR install dir, another install owns it.
    if (Args <> '') and (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Args)) = 0) then
      Result := Args;
  except
    Result := '';
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if AdamServerRunning() then begin
    if WizardSilent() then begin
      Log('Adam server is running - aborting silent install.');
      Result := False;
      Exit;
    end;
    if MsgBox('Adam is currently running on this PC. Close the black Adam window first, then click OK to continue (or Cancel to stop).',
              mbConfirmation, MB_OKCANCEL) = IDCANCEL then begin
      Result := False;
      Exit;
    end;
    if AdamServerRunning() then begin
      MsgBox('Adam is still running. Close the Adam window and run this installer again.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Other: String;
begin
  // By the Ready page the install dir is final — warn a ZIP-install user
  // before we plant a second install and take over their shortcut. NEVER in
  // silent mode: a custom MsgBox is NOT suppressed by /SUPPRESSMSGBOXES and
  // would hang a winget install forever (found the hard way).
  if (CurPageID = wpReady) and (not WizardSilent()) then begin
    Other := OtherInstallShortcutPath();
    if Other <> '' then
      MsgBox('Heads up: Adam appears to already be set up elsewhere on this PC ('
             + Other + '). This installer creates a SEPARATE copy at '
             + ExpandConstant('{app}')
             + ' and points the desktop shortcut at the new copy.'#13#10#13#10
             + 'Your existing Adam and all its data stay untouched where they are. '
             + 'If you meant to UPDATE your existing Adam, cancel and double-click '
             + 'UPDATE in its folder instead.', mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  App, BackupDir: String;
  ResultCode: Integer;
begin
  App := ExpandConstant('{app}');
  { BEFORE files are copied: if this is an upgrade over an existing install,
    snapshot the program files. Inno overwrites blindly (ignoreversion) — any
    local customization would otherwise be silently destroyed with no backup.
    robocopy excludes data\ (contains this very backup dir), the voice venv,
    caches, and secrets. }
  if (CurStep = ssInstall) and FileExists(App + '\server.py') then begin
    BackupDir := App + '\data\backups\installer-pre-' + ExpandConstant('{#AppVersion}');
    Exec(ExpandConstant('{cmd}'),
         '/c robocopy "' + App + '" "' + BackupDir + '" /E /R:1 /W:1 ' +
         '/XD data .venv __pycache__ /XF .env *.jvltmp',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Log('installer-pre backup robocopy exit ' + IntToStr(ResultCode));
  end;
  { AFTER files land: reset the updater baseline. It still describes the OLD
    version; left stale, the next in-app update three-ways old-base vs new
    files and mass-misclassifies everything changed in both spans as conflicts.
    Deleting it is safe by design — the server re-seeds the baseline from the
    now-current program files on next start. }
  if (CurStep = ssPostInstall) and DirExists(App + '\data\baseline') then begin
    if DelTree(App + '\data\baseline', True, True, True) then
      Log('stale updater baseline cleared (re-seeded on next start)')
    else
      Log('WARNING: could not clear stale baseline');
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if AdamServerRunning() then begin
    if UninstallSilent() then begin
      Log('Adam server is running - aborting silent uninstall.');
      Result := False;
      Exit;
    end;
    if MsgBox('Adam is currently running. Close the black Adam window first, then click OK.',
              mbConfirmation, MB_OKCANCEL) = IDCANCEL then begin
      Result := False;
      Exit;
    end;
    Result := not AdamServerRunning();
    if not Result then
      MsgBox('Adam is still running. Close the Adam window and try again.', mbError, MB_OK);
  end;
end;
