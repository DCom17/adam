# Adam - friendly one-click launcher (v1.0 Slice 1).
#
# Double-click this (or run it from PowerShell) to start the backend and open the app
# in your browser. It is intentionally simple and transparent:
#   * it starts the server in a VISIBLE window (you can see the logs; nothing hidden);
#   * it never installs anything, creates no service / scheduled task / autostart;
#   * it does not modify .env, settings.json, or Tailscale;
#   * if the server is already running, it just opens the browser (no duplicate).
#
# First-time setup is SETUP.cmd (the wizard); this launcher points there if it
# looks like setup hasn't been run yet.
#
# -AppWindow opens Adam as a clean Edge "app" window (no browser chrome) instead of a
# normal browser tab; this is what the pinnable Adam shortcut uses. Falls back to the
# default browser if Edge isn't present. Server-start behavior is otherwise identical.

param([switch]$AppWindow)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

# Locate Microsoft Edge (for the clean -AppWindow open mode).
function Get-Edge {
    foreach ($p in @("$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
                     "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
# Open the app at the (signed-in) URL: a chrome-less Edge app window when -AppWindow is
# set and Edge exists, otherwise the default browser. Same URL either way.
function Open-App($url) {
    if ($AppWindow) {
        $edge = Get-Edge
        if ($edge) { Start-Process -FilePath $edge -ArgumentList "--app=$url", "--window-size=480,900"; return }
    }
    Start-Process $url
}

# 1) Find a Python 3.10+ (Adam needs it). Prefer one on PATH; otherwise scan the
#    standard install dirs (covers a Python that's installed but not first on PATH).
function Get-AppPython {
    $cands = @()
    $onPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($onPath) { $cands += $onPath }
    $cands += (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($exe in $cands) {
        try { & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>$null
              if ($LASTEXITCODE -eq 0) { return $exe } } catch {}
    }
    return $null
}
$pyExe = Get-AppPython
if (-not $pyExe) {
    Say "Adam needs Python 3.10 or newer, and I couldn't find it." "Red"
    Say "Install the latest Python 3 from https://www.python.org/downloads/ (check 'Add to PATH')," "Yellow"
    Say "or run SETUP again - it can install it for you." "Yellow"
    exit 1
}

# 2) Core files present (are we in the right folder)?
foreach ($f in @("server.py", "config.py")) {
    if (-not (Test-Path (Join-Path $root $f))) {
        Say "Missing $f - run this from inside the adam-local folder." "Red"
        exit 1
    }
}

# 3) Looks set up? (.env is required; settings.json is optional - falls back to example)
if (-not (Test-Path (Join-Path $root ".env"))) {
    Say "It looks like Adam hasn't been set up on this computer yet." "Yellow"
    Say "Double-click SETUP in the Adam folder first - it walks you through" "Cyan"
    Say "everything (about 15 minutes) - then open Adam again." "Cyan"
    Say ""
    Say "(Advanced/manual path: python scripts\setup.py, then python scripts\doctor.py)" "DarkGray"
    exit 1
}

# 4) Resolve host/port from config so the launcher and app never disagree.
try {
    $cfg = & $pyExe -c "import json, config; print(json.dumps({'host': config.HOST, 'port': config.PORT}))"
    $c = $cfg | ConvertFrom-Json
    $port = [int]$c.port
} catch {
    Say "Could not read host/port from config.py:" "Red"
    Say "  $_" "Red"
    Say "Try:  python scripts\doctor.py   to see what's wrong." "Yellow"
    exit 1
}
$localUrl = "http://localhost:$port/"
$consoleUrl = "http://localhost:$port/console"
$healthUrl = "http://127.0.0.1:$port/health"

# Read ADAM_TOKEN from .env (read-only; same parsing as scripts\copy-token.ps1) so we
# can open the browser ALREADY SIGNED IN. The token rides in the URL *fragment* (#token=),
# which the app's bootstrap stores to localStorage and immediately strips from the URL.
# A fragment is never sent to the server, so it stays out of server logs. If the token is
# missing/placeholder we fall back to the plain URL (the page's sign-in gate still works).
$openUrl = $localUrl
$adamToken = $null
try {
    foreach ($line in (Get-Content -LiteralPath (Join-Path $root ".env"))) {
        $t = $line.Trim()
        if ($t.StartsWith("#")) { continue }
        if ($t -match '^\s*ADAM_TOKEN\s*=\s*(.*)$') {
            $val = $Matches[1].Trim()
            if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'")))) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            $adamToken = $val
            break
        }
    }
} catch { $adamToken = $null }
if (-not [string]::IsNullOrWhiteSpace($adamToken) -and $adamToken -ne "replace-with-a-long-random-token") {
    $openUrl = "$localUrl#token=" + [uri]::EscapeDataString($adamToken)
}

# Start the high-quality Adam voice (Kokoro TTS) on 127.0.0.1:8001 if it's installed
# (INSTALL-VOICE sets it up). If it's absent or fails, the app uses the browser voice.
$ttsPy    = Join-Path $root "scripts\tts_server\.venv\Scripts\python.exe"
$ttsModel = Join-Path $root "scripts\tts_server\kokoro-v1.0.onnx"
$ttsUp = $false
try { $tp = Invoke-WebRequest "http://127.0.0.1:8001/ping" -UseBasicParsing -TimeoutSec 2; if ($tp.StatusCode -eq 200) { $ttsUp = $true } } catch {}
if (-not $ttsUp -and (Test-Path $ttsPy) -and (Test-Path $ttsModel)) {
    Say "Starting the Adam voice (Kokoro)..." "DarkGray"
    Start-Process -FilePath $ttsPy -ArgumentList "tts_server.py" -WorkingDirectory (Join-Path $root "scripts\tts_server") -WindowStyle Hidden
}

# 5) Already running? If so, just open the browser - never start a duplicate.
# The token rides along so /health returns the full summary (anonymous callers
# get liveness + version only); without it we still detect "up" fine.
$alreadyUp = $false
try {
    $hdrs = @{}
    if (-not [string]::IsNullOrWhiteSpace($adamToken) -and $adamToken -ne "replace-with-a-long-random-token") {
        $hdrs["Authorization"] = "Bearer $adamToken"
    }
    $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -Headers $hdrs
    if ($h.status -eq "ok") { $alreadyUp = $true }
} catch { $alreadyUp = $false }

if ($alreadyUp) {
    $modeTxt = ""
    if ($h.agent_safety) { $modeTxt = ", mode $($h.agent_safety.mode)" }
    Say "Adam is already running on port $port (v$($h.version)$modeTxt)." "Green"
    Say "Opening $localUrl (signed in automatically)" "Cyan"
    Open-App $openUrl
    Say "Operator console: $consoleUrl" "DarkGray"
    Say "To stop it, use the server window or:  scripts\stop-dev.ps1" "DarkGray"
    exit 0
}

# 6) Start the server in its OWN VISIBLE window (transparent; logs are on screen).
Say "Starting Adam on $localUrl ..." "Cyan"
# Title the server window "Adam" so users recognize it and it matches the
# update/setup instructions ("close the black window titled 'Adam'"). The
# backtick escapes `$Host so it's evaluated in the NEW window, not here at build time.
$serverCmd = "`$Host.UI.RawUI.WindowTitle = 'Adam'; Set-Location -LiteralPath '$root'; & '$pyExe' -m uvicorn server:app --host $($c.host) --port $port"
try {
    Start-Process -FilePath "powershell" `
        -ArgumentList @("-NoExit", "-NoProfile", "-Command", $serverCmd) `
        -WorkingDirectory $root | Out-Null
} catch {
    Say "Could not launch the server window:" "Red"
    Say "  $_" "Red"
    exit 1
}

# 7) Wait (briefly) for the server to answer, then open the browser.
Say "Waiting for the server to come up ..." "DarkGray"
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        # Send the token when we have it: anonymous /health returns only
        # liveness+version, and the fuller body lets us print the mode below.
        $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 -Headers $hdrs
        if ($h.status -eq "ok") { $ready = $true; break }
    } catch { }
}

if ($ready) {
    $modeTxt = ""
    if ($h.agent_safety) { $modeTxt = ", mode $($h.agent_safety.mode)" }
    Say "Adam is UP (v$($h.version)$modeTxt)." "Green"
    Say "Opening $localUrl (signed in automatically)" "Cyan"
    Open-App $openUrl
    Say ""
    if ($openUrl -eq $localUrl) {
        Say "Note: ADAM_TOKEN wasn't found in .env, so the app opened without signing you in." "Yellow"
        Say "  Double-click SETUP in the Adam folder to fix this, then open Adam again." "Yellow"
        Say ""
    }
    Say "Next steps:" "White"
    Say "  - You're signed in on this computer. To add your phone, open the Operator" "Gray"
    Say "    console and use 'Connect phone' (scan one QR):  $consoleUrl" "Gray"
    Say "  - Phone access (Tailscale): docs\CONNECT_YOUR_PHONE.md" "Gray"
    Say "  - The server runs in the other window. Close it (or run scripts\stop-dev.ps1) to stop." "DarkGray"
    exit 0
} else {
    Say "The server did not answer on $healthUrl within ~15s." "Yellow"
    Say "Check the server window for errors, then run:  python scripts\doctor.py" "Yellow"
    Say "(Common cause: ADAM_TOKEN missing or Claude not found - doctor will say which.)" "DarkGray"
    exit 1
}
