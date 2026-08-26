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
    # One shape for every yes/no question in setup.
    #
    # The old prompt encoded the default in CAPITALISATION - [Y/n] when Enter
    # meant yes, [y/N] when it meant no. That convention is invisible unless you
    # already know it, so across a 6-step wizard it just reads as the letters
    # randomly changing case. But the default genuinely matters (Enter says yes
    # to installing Python and no to a 340 MB voice download), so it can't just
    # be flattened away - it gets said in words instead.
    #
    # Cyan because the answer hint is the one thing on screen the user has to
    # act on, and plain Blue is near-unreadable on a black console.
    #
    # There is deliberately NO "press Enter for yes" shortcut. Showing
    # "Enter = yes" alongside the question reads as "the Enter key means yes",
    # which is alarming at exactly the wrong moment: you have just typed n and
    # now have to press Enter to send it. An explicit y or n is always required,
    # so no keystroke can mean the opposite of what the user typed.
    #
    # Also rejects anything that isn't yes or no. Previously "banana" was
    # silently treated as "no", identical to a deliberate refusal - a bad way to
    # decide whether someone wants their API key saved.
    for ($i = 0; $i -lt 5; $i++) {
        Write-Host ("    " + $m + " ") -NoNewline
        Write-Host '(respond "y" or "n"): ' -ForegroundColor Cyan -NoNewline
        $a = (Read-Host).Trim().ToLower()
        if ($a -in @("y", "yes")) { return $true }
        if ($a -in @("n", "no"))  { return $false }
        if ($a) { Warn "Please answer y or n." }
        else    { Warn "Type y or n, then press Enter." }
    }
    # Bounded so a non-interactive stdin (piped/EOF) can never spin forever. The
    # caller's default only ever applies here, never to a real keystroke.
    return $defaultYes
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
Info "This will set up Adam on this computer. Adam is an AI assistant,"
Info "powered by Claude: what you say to it is processed by Anthropic's"
Info "Claude under YOUR own account. Everything else runs entirely on"
Info "YOUR machine. Nothing is shared or hosted anywhere else."
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

# --- the plan step: two doors for paying for AI time -------------------------------
# Records auth_mode + voice_model in settings.json (and the API key in .env for
# door 2) through integration_config's atomic, backed-up writers — same code path
# the in-app Settings -> AI plan control uses, so the two never disagree.
# This step MUST stay after STEP 3: integration_config imports config, which needs
# the just-installed python-dotenv — on a fresh machine, running it before the
# dependency install crashed and silently dropped the choice (and any pasted key).
function Set-AiPlan([string]$mode, [string]$model, [string]$key = "") {
    # SINGLE QUOTES ONLY inside this snippet. PowerShell strips embedded double
    # quotes when it hands an argument to a native exe, so `section_header="# AI
    # plan (...)"` reached python as `section_header=#` — the # opened a comment
    # that swallowed the closing paren, and python failed to COMPILE the module
    # ("'(' was never closed"). That killed both doors, not just the one with a
    # key: compilation happens before the `if` below can guard the line.
    $planPy = @'
import sys
sys.path.insert(0, sys.argv[1])
import integration_config as ic
ic.set_settings_top_level('auth_mode', sys.argv[2])
ic.set_settings_top_level('voice_model', sys.argv[3])
if len(sys.argv) > 4 and sys.argv[4]:
    ic.set_env_var('ANTHROPIC_API_KEY', sys.argv[4], section_header='# AI plan (pay-as-you-go)')
'@
    # A failed native python call doesn't throw in PowerShell — check the exit code.
    $planOk = $false
    try {
        & $pythonExe -c $planPy $root $mode $model $key | Out-Null
        $planOk = ($LASTEXITCODE -eq 0)
    } catch { $planOk = $false }
    if (-not $planOk) {
        Warn "Couldn't record the plan choice automatically."
        Info "No harm done - after setup, open Adam and finish this under"
        Info "Settings -> AI plan."
        if ($key) { Warn "Your API key was NOT saved - paste it again there." }
    }
    return $planOk
}

function Test-ClaudeSignedIn {
    # Run ONE real Claude turn. This is the only thing that proves a sign-in
    # works — and setup previously just ASKED the user whether they were signed
    # in and believed the answer. A person can be honestly wrong: the `claude`
    # REPL opens and looks completely normal while signed out, so "I was already
    # logged in, I closed the window" is a reasonable read of a broken state.
    # Setup then declared success and handed over an Adam that failed on its very
    # first message.
    #
    # Note where the CLI speaks: a not-signed-in run exits 1 with an EMPTY stderr
    # and puts the reason in STDOUT's JSON ("result":"Not logged in · Please run
    # /login"). Reading stderr alone finds nothing. Returns @{Ok; Why}.
    $out = ""
    try {
        $out = (& claude -p --output-format json "Reply with the single word OK." | Out-String)
    } catch {
        return @{ Ok = $false; Why = "CLAUDE_MISSING"; Detail = $_.Exception.Message }
    }
    $code = $LASTEXITCODE
    if ($code -eq 0 -and $out -notmatch '"is_error"\s*:\s*true') {
        return @{ Ok = $true; Why = ""; Detail = "" }
    }
    $detail = ""
    $m = [regex]::Match($out, '"result"\s*:\s*"((?:[^"\\]|\\.)*)"')
    if ($m.Success) { $detail = $m.Groups[1].Value }
    if (-not $detail) { $detail = ($out -replace '\s+', ' ').Trim() }
    if (-not $detail) { $detail = "Claude exited with code $code and printed nothing." }
    if ($detail.Length -gt 240) { $detail = $detail.Substring(0, 240) + "..." }
    $why = "UNKNOWN"
    if ($detail -match '(?i)not logged in|/login|log in to|not authenticated|unauthorized|credentials') { $why = "AUTH" }
    elseif ($detail -match '(?i)usage limit|rate limit|quota|limit reached') { $why = "LIMIT" }
    elseif ($detail -match '(?i)credit balance|billing|insufficient') { $why = "BILLING" }
    return @{ Ok = $false; Why = $why; Detail = $detail }
}

function Show-ClaudeFailureHelp($result) {
    # Whatever went wrong, the user leaves this screen knowing what it was and
    # what to do next. Never a bare failure.
    Write-Host ""
    switch ($result.Why) {
        "AUTH" {
            Warn "Claude is not signed in yet."
            Info "Claude said: $($result.Detail)"
            Write-Host ""
            Info "This is the most common one, and it's quick:"
            Info "  1. In the Claude window that opens, type   /login   and press Enter."
            Info "     (Type it even if Claude looks like it's already signed in -"
            Info "      that's exactly how this gets missed.)"
            Info "  2. Your browser should open on its own. If it does NOT, Claude"
            Info "     prints a long web address instead - select it, copy it, and"
            Info "     paste it into your browser yourself."
            Info "  3. Sign in there, or create a free account."
            Info "  4. Some sign-ins finish in the browser and you're done. Others end"
            Info "     by showing you a CODE. If you get a code, copy it, click back on"
            Info "     the Claude window, paste it in, and press Enter."
            Info "  5. Claude should now say you're logged in. Type   /exit   , then"
            Info "     press Enter here."
        }
        "LIMIT" {
            Warn "Claude is signed in, but its usage limit is currently reached."
            Info "Claude said: $($result.Detail)"
            Write-Host ""
            Info "Nothing is broken. Either wait for your plan's limit to reset, or"
            Info "restart SETUP and choose door [1] (pay as you go) instead."
        }
        "BILLING" {
            Warn "Claude is signed in, but the account has no credit available."
            Info "Claude said: $($result.Detail)"
            Write-Host ""
            Info "Add credit at  https://console.anthropic.com  ->  Billing, or use a"
            Info "Claude Pro/Max subscription and re-run SETUP choosing door [2]."
        }
        "CLAUDE_MISSING" {
            Warn "Couldn't run Claude at all."
            Info "Detail: $($result.Detail)"
            Write-Host ""
            Info "Claude Code doesn't appear to be installed, or isn't on your PATH."
            Info "  1. Close this window."
            Info "  2. Run SETUP again - step 2 installs Claude Code."
            Info "  3. If it keeps failing, open a terminal and type   claude   ."
            Info "     If that says 'not recognized', Claude Code isn't installed."
        }
        default {
            Warn "Claude ran but couldn't complete a message."
            Info "Claude said: $($result.Detail)"
            Write-Host ""
            Info "Things worth trying, in order:"
            Info "  1. Check you're online - Claude needs the internet to answer."
            Info "  2. In the Claude window, type   /login   and sign in again."
            Info "  3. If your company network blocks things, try another network."
            Info "  4. Still stuck? Copy the line above and open an issue at"
            Info "     https://github.com/DCom17/adam-releases/issues"
        }
    }
    Write-Host ""
}

Write-Host ""
Info "How will Adam's AI time be paid for? Two doors - and you can switch"
Info "anytime later under Settings -> AI plan in the app:"
Write-Host ""
Info "  [1] Pay as you go   (recommended - the most predictable)"
Info "      Load prepaid credit onto your own Anthropic API key - like an arcade"
Info "      card: `$5 is roughly 200-300 conversations, it reloads only when YOU"
Info "      choose, and Adam can never spend past your credit. A monthly budget in"
Info "      the app adds a hard stop and a live cost meter keeps it honest. This is"
Info "      the supported path: a clear per-use cost that never surprises you."
Write-Host ""
Info "  [2] Sign in with Claude   (if you already have a Claude plan)"
Info "      Adam runs on the Claude subscription you already pay for - nothing"
Info "      extra to buy, no card on file, no per-use cost. Usage counts against"
Info "      your plan's normal limits; if you ever reach them, Adam pauses until"
Info "      they reset."
Write-Host ""
$door = ""
while ($door -ne "1" -and $door -ne "2") {
    # Same treatment as YesNo: the thing you have to type is cyan, and the
    # accepted answers are spelled out rather than implied.
    Write-Host "    Which door? " -NoNewline
    Write-Host '(respond "1" or "2"): ' -ForegroundColor Cyan -NoNewline
    $door = (Read-Host).Trim()
    if ($door -ne "1" -and $door -ne "2") { Warn "Please answer 1 or 2." }
}

if ($door -eq "1") {
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
        if (Set-AiPlan "api_key" "claude-sonnet-5" $apiKey) {
            Good "Pay-as-you-go is set up (model: Claude Sonnet - fast and affordable)."
            Info "Switch models, raise the budget, or change doors anytime in the app."
        }
    } else {
        # No key recorded -> leave the subscription default so the app's sign-in
        # guidance stays truthful; the user finishes the choice in Settings -> AI plan.
        Warn "No key added - finish this later in the app under Settings -> AI plan."
    }
} else {
    $null = Set-AiPlan "subscription" "claude-opus-4-8"
    Write-Host ""
    Info "Now the one step only you can do: signing in to your Claude account."
    Info "I'll open Claude. Type  /login  in it - do that even if Claude looks like"
    Info "it's already signed in, because a signed-out Claude looks completely"
    Info "normal until something asks it to do work."
    Write-Host ""
    Info "Your browser should open by itself. If it doesn't, Claude prints a long"
    Info "web address - copy that into your browser. And if the website hands you a"
    Info "CODE at the end, copy the code back into the Claude window and press"
    Info "Enter. Then type  /exit  and come back here."
    Write-Host ""
    if (YesNo "Open Claude to sign in now?") {
        # Verify-and-retry. This step used to open Claude, ask "press Enter when
        # you've signed in", and believe whatever the user said. That is how a
        # fresh install shipped a broken Adam: the user closed a normal-looking
        # Claude window, honestly reported success, setup congratulated them, and
        # the first message died with "connection error". Never self-report —
        # prove it with a real turn, and if it fails, say exactly why.
        $signedIn = $false
        for ($attempt = 1; $attempt -le 3 -and -not $signedIn; $attempt++) {
            try {
                # New window so the login session is clean and doesn't take over this wizard.
                Start-Process "cmd.exe" -ArgumentList "/k", "claude"
                Info "A Claude window opened. Type  /login  there, sign in, then  /exit  ."
            } catch {
                Warn "Couldn't open it automatically. Open a terminal and type:  claude"
            }
            Pause-Enter "When you've signed in to Claude, press Enter here to check it"
            Write-Host ""
            Info "Checking the sign-in by sending Claude one real message..."
            $probe = Test-ClaudeSignedIn
            if ($probe.Ok) {
                $signedIn = $true
                Good "Signed in and answering - Adam will work."
                break
            }
            Show-ClaudeFailureHelp $probe
            if ($attempt -lt 3) {
                if (-not (YesNo "Open Claude and try the sign-in again?")) { break }
            } else {
                Warn "That's three tries - moving on so you're not stuck here."
            }
        }
        if (-not $signedIn) {
            Write-Host ""
            Warn "Continuing WITHOUT a verified Claude sign-in."
            Info "Adam will install fine, but it can't answer until Claude signs in."
            Info "When you want to finish: open a terminal, type  claude  , then  /login  ."
            Info "  (No browser? Copy the address Claude prints into one. Given a code"
            Info "   at the end? Paste it back into the Claude window.)"
            Info "Then check it worked with:   python scripts\doctor.py --live"
            Pause-Enter "Press Enter to continue"
        }
    } else {
        Warn "You can sign in later, but Adam won't answer until you do."
        Info "To sign in later: open a terminal, type  claude  , then  /login  ."
        Info "  (No browser? Copy the address Claude prints into one. Given a code"
        Info "   at the end? Paste it back into the Claude window.)"
        Info "Check it worked with:   python scripts\doctor.py --live"
        Pause-Enter "Press Enter to continue"
    }
}

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
# --live sends ONE real message through Claude. Without it the sign-in check is
# only a heuristic ("is there a credentials file?"), which an expired, revoked or
# wrong-account credential passes — and the user then discovers the problem on
# their very first message, as a bare "connection error" with nothing to act on.
# Spending one tiny turn here moves that discovery into setup, where there is a
# person watching and a specific instruction to give them.
& $pythonExe (Join-Path $root "scripts\doctor.py") --live | Out-Host
$doctorExit = $LASTEXITCODE
if ($doctorExit -ne 0) {
    Warn "The health check above found something that needs attention (a FAIL line)."
    Info "Read the FAIL line - it names the problem and what to do about it."
    Info "If it's the Claude sign-in: open a terminal, type  claude  and press Enter,"
    Info "then type  /login  , finish signing in, and run SETUP again."
    Info "You can re-run this check any time with:  python scripts\doctor.py --live"
    if (-not (YesNo "Try launching anyway?" $false)) {
        Pause-Enter "Press Enter to close"
        exit 1
    }
}

# === STEP 6 — Launch ===============================================================
Section 6 "Starting Adam"
Info "Adding an Adam app shortcut..."
# Make Adam launchable like an app (Desktop + Start Menu), not just from this folder.
$shortcutOk = $true
try { & (Join-Path $root "scripts\add-app-shortcut.ps1") | Out-Host }
catch { $shortcutOk = $false }

# The voice offer is the LAST question, and it is asked BEFORE the first launch.
# It used to sit after start-adam.ps1, which meant Adam opened in the browser on
# top of a console prompt still waiting for an answer — and saying yes then asked
# the user to restart the app to hear the voice they had just installed. Asking
# first means the very first run already sounds the way it should.
# The original reason it sat after the launch was so a 340 MB download could not
# break the first run; that protection is kept by wrapping the install, so a
# failed or abandoned download still falls through to a normal start.
Write-Host ""
Line
Info "Last question. Right now Adam uses your browser's built-in (robotic) voice."
Info "You can upgrade to the real Adam voice - a one-time ~340 MB download that"
Info "runs entirely on your PC."
if (YesNo "Install the real Adam voice now?" $false) {
    try {
        & (Join-Path $root "scripts\install-voice.ps1")
        Info "Installed - Adam will start with the real voice."
    } catch {
        Warn "The voice download didn't finish: $($_.Exception.Message)"
        Info "Adam will still start now, using the built-in voice. Double-click"
        Info "INSTALL-VOICE in this folder to try again whenever you like."
    }
} else {
    Info "No problem. Double-click INSTALL-VOICE in this folder whenever you want it."
}

Write-Host ""
Good "Setup complete!"
if ($shortcutOk) {
    Info "Open Adam any time from the 'Adam' icon on your Desktop"
    Info "or in the Start Menu. (Double-clicking START in this folder still works too.)"
} else {
    # Never promise an icon that didn't get made — point at the always-true path.
    Warn "Couldn't add the Desktop icon on this machine."
    Info "No problem: open Adam any time by double-clicking START in this folder."
}
Write-Host ""
Info "Starting Adam..."
try {
    & (Join-Path $root "scripts\start-adam.ps1")
} catch {
    Warn "Couldn't auto-launch: $($_.Exception.Message)"
    Info "Double-click START (or run scripts\start-adam.ps1) to open Adam."
}
Pause-Enter "Press Enter to close"
