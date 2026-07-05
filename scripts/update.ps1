# Adam - update in place from the latest published release.
#
# Checks the maintainer's GitHub Releases for a newer version and, if there is one,
# downloads it and applies the PROGRAM files over this install with the smart 3-way
# updater - WITHOUT touching your .env, settings.json, or data/ folder (your token,
# settings, and files stay exactly as they are). Double-click UPDATE.cmd to run this.
#
# Update source = the public releases repo in settings.json's "update_repo" (default
# baked into config.py). No file IDs, no manual file-swapping; deleting/replacing a
# release never breaks an install. The in-app "Update now" button does the same thing.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }

# Find the same Python 3.10+ Adam itself runs on (the updater needs it).
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

Say "Adam - Update" "Cyan"
Say "Checking for a newer version..." "Gray"

# The updater (check + download + smart apply) is Python - same module the in-app
# button uses. If Python is missing we stop rather than risk clobbering your files
# (Adam itself needs Python, so this should be rare).
$pyExe = Get-AppPython
if (-not $pyExe) {
    Say "Couldn't find the Python that runs Adam, so I can't update safely." "Red"
    Say "Run SETUP once (it can install Python), then try UPDATE again." "Yellow"
    Read-Host "Press Enter to close" | Out-Null; exit 1
}

& $pyExe (Join-Path $root "scripts\self_update.py")
$rc = $LASTEXITCODE
if ($rc -ne 0 -and $rc -ne 10) {
    Read-Host "Press Enter to close" | Out-Null; exit 1
}

$ver = ""
try {
    $m = Select-String -Path (Join-Path $root "config.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $ver = $m.Matches.Groups[1].Value }
} catch {}
Say ("Done." + $(if ($ver) { " You're on version $ver." } else { "" })) "Green"
Say "If anything was updated, you must restart the SERVER to finish: fully close the BLACK Adam" "Yellow"
Say "window (titled 'Adam') - closing the browser app is NOT enough - then reopen Adam." "Yellow"
if ($rc -eq 10) {
    Say "A few files you'd customized were changed by this update too - Adam kept YOUR version and" "Cyan"
    Say "saved the update's copy. Open Adam and say 'merge the update conflicts' to reconcile them." "Cyan"
}
Read-Host "Press Enter to close" | Out-Null
