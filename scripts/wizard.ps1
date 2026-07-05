# Adam — guided first-run wizard.
#
# Launched by double-clicking SETUP.cmd (which bypasses the PowerShell execution policy).
# Goal: take a non-technical Windows user from a freshly-extracted ZIP to a running,
# signed-in app — auto-installing what it safely can, and clearly guiding the ONE step it
# can't do for them: logging into their own Claude (Anthropic) account.
#
# It is transparent and conservative:
#   * it never installs anything without asking first;
#   * it only uses official installers (winget / claude.ai/install.ps1);
#   * it does not modify your files, only this app's own .env/settings.json via setup.py;
#   * every step degrades to a plain-language instruction + link if auto-install fails.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path     # ...\scripts
$root = Split-Path -Parent $here                            # project root
Set-Location -LiteralPath $root

$TOTAL = 6

# --- tiny UI helpers ---------------------------------------------------------------
function Line()        { Write-Host ("  " + ("-" * 60)) -ForegroundColor DarkGray }
function Section($n, $title) {
    Write-Host ""
    Write-Host ("  STEP $n of $TOTAL  -  $title") -ForegroundColor Cyan
    Line
}
function Info($m) { Write-Host "    $m" }
function Good($m) { Write-Host "    [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "    $m" -ForegroundColor Red }
function Ask($m)  { return (Read-Host ("    " + $m)).Trim() }
function YesNo($m, $defaultYes = $true) {
    $hint = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    $a = (Read-Host ("    " + $m + " " + $hint)).Trim().ToLower()
    if (-not $a) { return $defaultYes }
    return $a -in @("y", "yes")
}
function Pause-Enter($m = "Press Enter to continue") { Read-Host ("    " + $m) | Out-Null }

# Re-read PATH from the registry so tools installed during THIS run are found without a
# fresh shell (winget / the Claude installer update the persisted PATH, not our $env).
function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}
function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function HaveWinget() { return (Have "winget") }

# Locate claude.exe wherever the installer put it. The official native installer
# drops it in %USERPROFILE%\.local\bin and does NOT add that to PATH, so a plain
# `Get-Command claude` right after install misses it — check the known locations too.
function Find-Claude {
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    $cands = @(
        (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Claude Code\claude.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\claude.exe")
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    return $null
}

# Make an exe reachable: prepend its folder to THIS session's PATH (so the steps that
# follow and the child processes we spawn find it) and persist it to the USER PATH so
# future terminals and the START launcher find it too.
function Ensure-OnPath($exePath) {
    if (-not $exePath) { return }
    $dir = Split-Path $exePath
    if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$dir;" + $env:Path }
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) { $userPath = "" }
        if (($userPath -split ';') -notcontains $dir) {
            $newPath = if ($userPath) { "$dir;$userPath" } else { $dir }
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        }
    } catch {}
}

# Return the path to a Python 3.10+ (what Adam needs), or $null. Checks PATH first,
# then the standard install dirs - a freshly-installed Python isn't always first on PATH.
function Find-GoodPython {
    $cands = @()
    $onPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($onPath) { $cands += $onPath }
    $cands += (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($c in $cands) {
        try { & $c -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>$null
              if ($LASTEXITCODE -eq 0) { return $c } } catch {}
    }
    return $null
}

# --- banner ------------------------------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "     ADAM LOCAL  -  easy setup" -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host ""
Info "This will set up Adam on this computer. It runs entirely on YOUR"
Info "machine, using YOUR own Claude account. Nothing is shared or hosted."
Write-Host ""
Info "I'll handle almost everything automatically. There is ONE step only you"
Info "can do: signing in to your Claude (Anthropic) account in your browser."
Info "I'll walk you through it when we get there."
Write-Host ""
if (-not (YesNo "Ready to begin?")) { Info "No problem - run SETUP again whenever you're ready."; exit 0 }

# === STEP 1 — Python ===============================================================
Section 1 "Python (the engine Adam runs on)"
Update-PathFromRegistry
$pythonExe = Find-GoodPython
if ($pythonExe) {
    Ensure-OnPath $pythonExe
    Good "Python is ready.  ($((& $pythonExe --version) 2>&1))"
} else {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Warn "Your Python is too old for Adam - it needs Python 3.10 or newer."
    } else {
        Warn "Python isn't installed yet. Adam needs it to run."
    }
    if ((HaveWinget) -and (YesNo "Install an up-to-date Python automatically now? (recommended)")) {
        Info "Installing Python 3.12 via winget - this can take a couple of minutes..."
        try {
            & winget install -e --id Python.Python.3.12 --source winget `
                --accept-package-agreements --accept-source-agreements | Out-Host
        } catch { Warn "The installer reported: $($_.Exception.Message)" }
        Update-PathFromRegistry
        $pythonExe = Find-GoodPython
        if ($pythonExe) { Ensure-OnPath $pythonExe; Good "Python installed.  ($((& $pythonExe --version) 2>&1))" }
    } elseif (-not (HaveWinget)) {
        Warn "The automatic installer (winget) isn't available on this Windows version."
    }
    if (-not $pythonExe) {
        Bad "Adam needs Python 3.10 or newer, and I couldn't set it up automatically."
        Info "  1. Go to:  https://www.python.org/downloads/"
        Info "  2. Download the latest Python 3, run the installer, and CHECK"
        Info "     'Add python.exe to PATH'."
        Info "  3. Close this window and double-click SETUP again."
        try { Start-Process "https://www.python.org/downloads/" } catch {}
        Pause-Enter "Press Enter to close"
        exit 1
    }
}

# === STEP 2 — Claude Code ==========================================================
Section 2 "Claude Code (your AI engine)"
Update-PathFromRegistry
$claudeExe = Find-Claude
if ($claudeExe) {
    Ensure-OnPath $claudeExe
    Good "Claude Code is already installed."
} else {
    Warn "Claude Code isn't installed yet. It's the AI that powers Adam."
    if (YesNo "Install Claude Code automatically now? (recommended)") {
        Info "Installing Claude Code (official installer, no extra software needed)..."
        try {
            Invoke-Expression (Invoke-RestMethod -Uri "https://claude.ai/install.ps1")
        } catch { Warn "The installer reported: $($_.Exception.Message)" }
        # The installer often succeeds but leaves claude off PATH — find it directly.
        Update-PathFromRegistry
        $claudeExe = Find-Claude
        if ($claudeExe) { Ensure-OnPath $claudeExe; Good "Claude Code installed." }
    }
    if (-not $claudeExe) {
        Bad "Claude Code still isn't ready. Please install it by hand, then run SETUP again:"
        Info "  Open PowerShell and run:   irm https://claude.ai/install.ps1 | iex"
        Info "  Or see:  https://docs.anthropic.com/en/docs/claude-code"
        Pause-Enter "Press Enter to close"
        exit 1
    }
}

# --- the plan step: two doors for paying for AI time -------------------------------
# Records auth_mode + voice_model in settings.json (and the API key in .env for
# door 2) through integration_config's atomic, backed-up writers — same code path
# the in-app Settings -> AI plan control uses, so the two never disagree.
function Set-AiPlan([string]$mode, [string]$model, [string]$key = "") {
    $planPy = @'
import sys
sys.path.insert(0, sys.argv[1])
import integration_config as ic
ic.set_settings_top_level("auth_mode", sys.argv[2])
ic.set_settings_top_level("voice_model", sys.argv[3])
if len(sys.argv) > 4 and sys.argv[4]:
    ic.set_env_var("ANTHROPIC_API_KEY", sys.argv[4], section_header="# AI plan (pay-as-you-go)")
'@
    try { & $pythonExe -c $planPy $root $mode $model $key | Out-Null }
    catch { Warn "Couldn't record the plan choice: $($_.Exception.Message)" }
}

Write-Host ""
Info "How will Adam's AI time be paid for? Two doors - and you can switch"
Info "anytime later under Settings -> AI plan in the app:"
Write-Host ""
Info "  [1] Sign in with Claude   (recommended for regular daily use)"
Info "      You have - or will get - a Claude plan (about `$20/month for Pro)."
Info "      Adam runs on it at a flat rate: no meter, nothing extra to pay."
Write-Host ""
Info "  [2] Pay as you go   (no subscription needed)"
Info "      Load prepaid credit onto an Anthropic API key - like an arcade card:"
Info "      `$5 is roughly 200-300 conversations, it reloads only when YOU choose,"
Info "      and Adam can never spend past your credit. A monthly budget in the"
Info "      app adds its own hard stop, and a live cost meter keeps it honest."
Write-Host ""
$door = ""
while ($door -ne "1" -and $door -ne "2") {
    $door = (Read-Host "  Type 1 or 2, then press Enter").Trim()
}

if ($door -eq "2") {
    Write-Host ""
    Info "Create a key at  https://console.anthropic.com  ->  API keys, and buy a"
    Info "small amount of credit (`$5 is plenty to start). Leave auto-reload OFF"
    Info "and overspending is impossible."
    $apiKey = ""
    while ($true) {
        $apiKey = (Read-Host "  Paste your API key (starts with sk-ant-), or press Enter to skip").Trim()
        if (-not $apiKey) { break }
        if ($apiKey.StartsWith("sk-ant-") -and $apiKey.Length -ge 20) { break }
        Warn "That doesn't look like an Anthropic key - they start with sk-ant-."
    }
    if ($apiKey) {
        Set-AiPlan "api_key" "claude-sonnet-5" $apiKey
        Good "Pay-as-you-go is set up (model: Claude Sonnet - fast and affordable)."
        Info "Switch models, raise the budget, or change doors anytime in the app."
    } else {
        # No key recorded -> leave the subscription default so the app's sign-in
        # guidance stays truthful; the user finishes the choice in Settings -> AI plan.
        Warn "No key added - finish this later in the app under Settings -> AI plan."
    }
} else {
    Set-AiPlan "subscription" "claude-opus-4-8"
    Write-Host ""
    Info "Now the one step only you can do: signing in to your Claude account."
    Info "I'll open Claude. A browser window will appear - sign in (or create an"
    Info "account). When it says you're logged in, type  /exit  to close Claude and"
    Info "come back here."
    Write-Host ""
    if (YesNo "Open Claude to sign in now?") {
        try {
            # New window so the login session is clean and doesn't take over this wizard.
            Start-Process "cmd.exe" -ArgumentList "/k", "claude"
            Info "A Claude window opened. Sign in there, then type  /exit  in it."
        } catch {
            Warn "Couldn't open it automatically. Open a terminal and type:  claude"
        }
        Pause-Enter "When you've signed in to Claude, press Enter here to continue"
    } else {
        Warn "You can sign in later, but Adam won't answer until you do."
        Info "To sign in later: open a terminal and type  claude  , then log in."
        Pause-Enter "Press Enter to continue"
    }
}

# === STEP 3 — Dependencies =========================================================
Section 3 "Adam's building blocks (one-time download)"
Info "Downloading the small set of components Adam needs (needs internet)..."
$req = Join-Path $root "requirements.txt"
# pip routinely writes harmless notices (cache messages, "new release available") to
# stderr. With $ErrorActionPreference='Stop', PowerShell turns any native stderr line
# piped through 2>&1 into a TERMINATING NativeCommandError and kills the wizard mid-step.
# Relax the preference around pip only - the real success test below is whether the
# modules actually import, not pip's exit chatter.
$pipEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonExe -m pip install --upgrade pip 2>&1 | Out-Host
& $pythonExe -m pip install -r $req 2>&1 | Out-Host
$ErrorActionPreference = $pipEAP
# pip can exit non-zero, install into the wrong place, or partly fail WITHOUT throwing
# a PowerShell error - so the real test is whether the core modules actually import in
# this same Python. (A silent failure here is what left an earlier build stuck later.)
function Test-CoreImports { & $pythonExe -c "import fastapi, uvicorn, dotenv, multipart" 2>$null; return ($LASTEXITCODE -eq 0) }
if (-not (Test-CoreImports)) {
    Warn "That didn't fully complete - retrying the download once..."
    $pipEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $pythonExe -m pip install -r $req 2>&1 | Out-Host
    $ErrorActionPreference = $pipEAP
}
if (-not (Test-CoreImports)) {
    Bad "Adam's components didn't finish installing."
    Info "This is almost always a momentary internet problem. To finish by hand:"
    Info "  1. Make sure you're online."
    Info "  2. In this folder's address bar type  powershell  and press Enter, then run:"
    Info "       python -m pip install -r requirements.txt"
    Info "  3. Then double-click SETUP again."
    Pause-Enter "Press Enter to close"
    exit 1
}
Good "Components installed."

# === STEP 4 — Your notes folder ====================================================
Section 4 "Your notes folder (the files Adam works with)"
$defaultVault = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Adam Files"
Info "Adam reads and helps with files in one folder you choose."
Info "Default (recommended):  $defaultVault"
$vault = $defaultVault
if (-not (YesNo "Use that default folder?")) {
    $custom = Ask "Type the full path to the folder you want (or leave blank for the default):"
    if ($custom) { $vault = $custom }
}
try {
    if (-not (Test-Path -LiteralPath $vault)) {
        New-Item -ItemType Directory -Path $vault -Force | Out-Null
        Good "Created your notes folder:  $vault"
    } else {
        Good "Using:  $vault"
    }
} catch {
    Warn "Couldn't create that folder; using the default instead."
    $vault = $defaultVault
    if (-not (Test-Path -LiteralPath $vault)) { New-Item -ItemType Directory -Path $vault -Force | Out-Null }
}
# Record vault_path via the app's own onboarding helpers (never edits safety settings).
try {
    $py = @"
import sys; sys.path.insert(0, r'$root')
import onboarding
from pathlib import Path
s = Path(r'$root') / 'settings.json'
e = Path(r'$root') / 'settings.example.json'
onboarding.ensure_settings_file(s, e)
onboarding.set_settings_value(s, 'vault_path', r'''$vault''')
onboarding.set_settings_value(s, 'claude_exe', r'''$claudeExe''')
print('config recorded')
"@
    $py | & $pythonExe - | Out-Host
} catch { Warn "Couldn't pre-set the folder; setup will use the default. ($($_.Exception.Message))" }

# === STEP 5 — Finish configuring (token, checks) ===================================
Section 5 "Finishing configuration"
Info "Generating your private access token and running a health check..."
# setup.py is idempotent and goes non-interactive when stdin isn't a console, so piping
# '' here makes it run unattended: it generates the token, detects Claude, keeps the
# folder we just set, and prints the doctor report. It never overwrites an existing token.
"" | & $pythonExe (Join-Path $root "scripts\setup.py") | Out-Host
Write-Host ""
Info "Running the full health check..."
& $pythonExe (Join-Path $root "scripts\doctor.py") | Out-Host
$doctorExit = $LASTEXITCODE
if ($doctorExit -ne 0) {
    Warn "The health check above found something that needs attention (a FAIL line)."
    Info "Most often this is the Claude sign-in - if you skipped it, open a terminal,"
    Info "type  claude  , sign in, then run SETUP again."
    if (-not (YesNo "Try launching anyway?" $false)) {
        Pause-Enter "Press Enter to close"
        exit 1
    }
}

# === STEP 6 — Launch ===============================================================
Section 6 "Starting Adam"
Info "Adding a Adam app shortcut, then starting it up..."
# Make Adam launchable like an app (Desktop + Start Menu), not just from this folder.
try { & (Join-Path $root "scripts\add-app-shortcut.ps1") | Out-Host } catch {}
Write-Host ""
Good "Setup complete!"
Info "Open Adam any time from the 'Adam' icon on your Desktop"
Info "or in the Start Menu. (Double-clicking START in this folder still works too.)"
Write-Host ""
try {
    & (Join-Path $root "scripts\start-adam.ps1")
} catch {
    Warn "Couldn't auto-launch: $($_.Exception.Message)"
    Info "Double-click START (or run scripts\start-adam.ps1) to open Adam."
}

# Offer the real Adam voice now that the app is up - kept OUT of core setup so a big
# download can't break the first run. Default is no; INSTALL-VOICE adds it any time.
Write-Host ""
Line
Info "Right now Adam uses your browser's built-in (robotic) voice. You can upgrade"
Info "to the real Adam voice - a one-time ~340 MB download that runs on your PC."
if (YesNo "Install the real Adam voice now?" $false) {
    & (Join-Path $root "scripts\install-voice.ps1")
    Info "Done - restart Adam (close its window, then open it again) to hear the new voice."
} else {
    Info "No problem. Double-click INSTALL-VOICE in this folder whenever you want it."
}
Pause-Enter "Press Enter to close"
