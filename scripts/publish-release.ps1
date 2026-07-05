# Adam - publish a release (maintainer only).
#
# Builds the versioned release zip and publishes it to your PUBLIC releases repo as a
# GitHub Release. Installs pick it up automatically (in-app "Update now" or UPDATE.cmd)
# from the repo's permanent "latest release" endpoint - no file IDs, no file-swapping.
#
# Usage:   powershell -ExecutionPolicy Bypass -File scripts\publish-release.ps1
#          (optional)  -Repo owner/name   -Notes "what changed"
#
# Needs the GitHub CLI (`gh`) installed + signed in (`gh auth login`) ONLY for the
# automated path. Without it, this still builds the zip and prints the 3 web steps.

param(
    [string]$Repo = "",
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }

# Version from config.py
$ver = ""
$m = Select-String -Path (Join-Path $root "config.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($m) { $ver = $m.Matches.Groups[1].Value }
if (-not $ver) { Say "Couldn't read APP_VERSION from config.py." "Red"; exit 1 }
$tag = "v$ver"

# Repo: arg > settings.json update_repo > config.py default
if (-not $Repo) {
    try {
        $sj = Join-Path $root "settings.json"
        if (Test-Path $sj) { $cfg = Get-Content $sj -Raw | ConvertFrom-Json; if ($cfg.update_repo) { $Repo = [string]$cfg.update_repo } }
    } catch {}
}
if (-not $Repo) {
    $cm = Select-String -Path (Join-Path $root "config.py") -Pattern 'update_repo\D+"([^"]+)"' | Select-Object -First 1
    if ($cm) { $Repo = $cm.Matches.Groups[1].Value }
}
if (-not $Repo) { $Repo = "DCom17/adam-releases" }

# Find Python to build the zip
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Say "Python not found on PATH - needed to build the release zip." "Red"; exit 1 }

# --- Publish gates: never ship a ZIP that matches no commit or fails its tests ----
# Gate 1: clean tree. A dirty-tree publish ships bytes that correspond to no commit,
# making the release unreproducible and un-debuggable.
$dirty = (& git -C $root status --porcelain) | Where-Object { $_ -and ($_ -notmatch '^\?\?') }
if ($dirty) {
    Say "Refusing to publish: uncommitted changes in the working tree:" "Red"
    $dirty | ForEach-Object { Say "  $_" "Yellow" }
    Say "Commit (or stash) first, then publish. (Untracked files are allowed.)" "Red"
    exit 1
}
# Gate 2: release tests (includes every packaging guard + the boot-the-ZIP smoke).
Say "Running release tests before publishing ..." "Cyan"
& $py (Join-Path $root "test_release.py")
if ($LASTEXITCODE -ne 0) {
    Say "Refusing to publish: test_release.py failed (exit $LASTEXITCODE)." "Red"
    exit 1
}

Say "Building release zip for $tag ..." "Cyan"
& $py (Join-Path $here "make_release.py") | Write-Host
$zip = Join-Path $root ("dist\adam-local-$tag.zip")
if (-not (Test-Path $zip)) { Say "Build did not produce $zip" "Red"; exit 1 }
Say "Built: $zip" "Green"

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if ($gh) {
    if (-not $Notes) { $Notes = "Adam $tag" }
    Say "Publishing $tag to $Repo via gh ..." "Cyan"
    # Create the release (or, if the tag already exists, upload/replace the asset).
    $exists = $false
    try { & $gh release view $tag --repo $Repo *> $null; if ($LASTEXITCODE -eq 0) { $exists = $true } } catch {}
    if ($exists) {
        & $gh release upload $tag $zip --repo $Repo --clobber
    } else {
        & $gh release create $tag $zip --repo $Repo --title $tag --notes $Notes
    }
    if ($LASTEXITCODE -eq 0) {
        Say "Published. Installs will see $tag as the latest release." "Green"
    } else {
        Say "gh failed (exit $LASTEXITCODE). Make sure the repo exists and you're signed in (gh auth login)." "Red"
        exit 1
    }
} else {
    Say "" "Gray"
    Say "GitHub CLI (gh) not found - publish in the browser instead (one minute):" "Yellow"
    Say "  1. Go to:  https://github.com/$Repo/releases/new" "Cyan"
    Say "     (If the repo doesn't exist yet, create it first as a PUBLIC repo named '$($Repo.Split('/')[-1])'.)" "Gray"
    Say "  2. Tag version: $tag   Title: $tag" "Cyan"
    Say "  3. Drag this file into 'Attach binaries', then click 'Publish release':" "Cyan"
    Say "       $zip" "Green"
    Say "" "Gray"
    Say "Tip: install gh once (winget install GitHub.cli; gh auth login) and future releases are one command." "Gray"
}
