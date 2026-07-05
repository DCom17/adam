# Adam - install the high-quality "real Adam" voice (Kokoro).
#
# Creates a dedicated voice engine workspace, installs the ONNX text-to-speech engine,
# and downloads the voice model (~340 MB, one time). The core app never needs any of
# this - it's a pure upgrade over the browser's built-in voice. Double-click
# INSTALL-VOICE.cmd to run. Re-runnable: it skips whatever's already done.

$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path     # scripts/
$ttsDir = Join-Path $here "tts_server"
$venvPy = Join-Path $ttsDir ".venv\Scripts\python.exe"
$model  = Join-Path $ttsDir "kokoro-v1.0.onnx"
$voices = Join-Path $ttsDir "voices-v1.0.bin"
$MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
$VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }

# Find a Python 3.10+ (the voice engine needs it) - same logic as the launcher.
function Get-Py {
    $cands = @(); $p = (Get-Command python -ErrorAction SilentlyContinue).Source; if ($p) { $cands += $p }
    $cands += (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($e in $cands) {
        try { & $e -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return $e } } catch {}
    }
    return $null
}

Say "Adam - install the real Adam voice (Kokoro)" "Cyan"
Say "One-time ~340 MB download plus a short install. The new voice takes effect" "Gray"
Say "after you restart Adam. Until then everything keeps working as-is." "Gray"
Write-Host ""

$py = Get-Py
if (-not $py) { Say "Python 3.10+ not found - run SETUP first." "Red"; Read-Host "Press Enter to close" | Out-Null; exit 1 }

# 1) Dedicated venv (keeps the heavy ONNX runtime out of the core app).
if (-not (Test-Path $venvPy)) {
    Say "Creating the voice engine's workspace..." "Gray"
    & $py -m venv (Join-Path $ttsDir ".venv")
    if (-not (Test-Path $venvPy)) { Say "Couldn't create the voice workspace." "Red"; Read-Host "Press Enter to close" | Out-Null; exit 1 }
}

# 2) Voice engine packages.
Say "Installing the voice engine (a few minutes; needs internet)..." "Gray"
# pip writes harmless notices (cache messages, "new release available") to stderr. With
# $ErrorActionPreference='Stop', piping a native command's stderr via 2>&1 turns those
# lines into a TERMINATING NativeCommandError and kills the installer mid-step. Relax the
# preference around pip only - the real success test below is whether the engine imports.
$pipEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $venvPy -m pip install --upgrade pip 2>&1 | Out-Host
& $venvPy -m pip install -r (Join-Path $ttsDir "requirements.txt") 2>&1 | Out-Host
$ErrorActionPreference = $pipEAP
& $venvPy -c "import kokoro_onnx, onnxruntime, soundfile" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "The voice engine didn't finish installing (usually a network hiccup). Run INSTALL-VOICE again." "Red"
    Read-Host "Press Enter to close" | Out-Null; exit 1
}

# 3) Model + voices (resume-friendly; skipped if already the right size).
function Get-Big($url, $dest, $minMB) {
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt ($minMB * 1MB))) { Say ("Already have " + (Split-Path $dest -Leaf) + "."); return $true }
    Say ("Downloading " + (Split-Path $dest -Leaf) + " - this is the big one, please wait...") "Gray"
    try {
        try { Import-Module BitsTransfer -ErrorAction Stop; Start-BitsTransfer -Source $url -Destination $dest -ErrorAction Stop }
        catch { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 1800 }
    } catch { Say ("Download failed: " + $_.Exception.Message) "Red"; return $false }
    return ((Test-Path $dest) -and ((Get-Item $dest).Length -gt ($minMB * 1MB)))
}
if (-not (Get-Big $MODEL_URL  $model  250)) { Say "Could not download the voice model. Check your connection and run INSTALL-VOICE again." "Yellow"; Read-Host "Press Enter to close" | Out-Null; exit 1 }
if (-not (Get-Big $VOICES_URL $voices 20))  { Say "Could not download the voices file. Run INSTALL-VOICE again." "Yellow"; Read-Host "Press Enter to close" | Out-Null; exit 1 }

Write-Host ""
Say "The real Adam voice is installed." "Green"
Say "Restart Adam (close the Adam window, then open it from the icon / START) to hear it." "Yellow"
Read-Host "Press Enter to close" | Out-Null
