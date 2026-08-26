# Adam - publish a release (maintainer only).
#
# Builds the versioned release zip and publishes it to your PUBLIC releases repo as a
# GitHub Release. Installs pick it up automatically (in-app "Update now" or UPDATE.cmd)
# from the repo's permanent "latest release" endpoint - no file IDs, no file-swapping.
#
# Usage:   powershell -ExecutionPolicy Bypass -File scripts\publish-release.ps1
#          (optional)  -Repo owner/name   -NotesFile notes.txt   -Notes 'what changed'
#
# NOTE ON NOTES: if the text contains a price, use -NotesFile (read verbatim) or
# SINGLE quotes. Double-quoted "$24.99" makes PowerShell expand $24 to nothing —
# that is how v0.9.62 shipped with notes reading "a one-time \.99". Guarded below.
#
# Needs the GitHub CLI (`gh`) installed + signed in (`gh auth login`) ONLY for the
# automated path. Without it, this still builds the zip and prints the 3 web steps.

param(
    [string]$Repo = "",
    [string]$Notes = "",
    # Prefer this over -Notes for anything containing a price. A file is read verbatim,
    # so PowerShell can never eat a "$24" the way a double-quoted -Notes string does.
    [string]$NotesFile = "",
    # Escape hatches (fail-closed by default — see the installer stage below):
    #   -ZipOnly       publish WITHOUT an installer (old behavior; must be explicit now)
    #   -AllowUnsigned build/ship an UNSIGNED installer when signing isn't configured
    [switch]$ZipOnly,
    [switch]$AllowUnsigned
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }

# Auto-load local signing config if the maintainer created one (scripts/signing.local.ps1,
# gitignored + never shipped). Lets a signed release build with one command instead of
# re-typing ADAM_ACS_* every session. Absent on a user's machine → skipped, no effect.
$signingLocal = Join-Path $here "signing.local.ps1"
if (Test-Path $signingLocal) { . $signingLocal }

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
    # Match the ACTUAL default in the _as(...) call, not the "owner/name" example in
    # the comment above it (the old 'update_repo\D+"..."' matched the comment first).
    $cm = Select-String -Path (Join-Path $root "config.py") -Pattern '_as\("update_repo",\s*"([^"]+)"' | Select-Object -First 1
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

# Gate 3 (advisory): known CVEs in the pinned dependencies. Deliberately does
# NOT block — a vulnerability in a transitive dep must not stop a hotfix going
# out — but the operator sees it before publishing rather than months later.
Say "Auditing dependencies for known vulnerabilities ..." "Cyan"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "audit-deps.ps1")
if ($LASTEXITCODE -ne 0) {
    Say "WARNING: dependency audit reported findings (see above). Publishing anyway." "Yellow"
    Say "         Fix with a version bump in requirements.txt when you can." "Yellow"
}

Say "Building release zip for $tag ..." "Cyan"
& $py (Join-Path $here "make_release.py") | Write-Host
$zip = Join-Path $root ("dist\adam-local-$tag.zip")
if (-not (Test-Path $zip)) { Say "Build did not produce $zip" "Red"; exit 1 }
Say "Built: $zip" "Green"

# --- Installer stage: a release MUST carry a signed installer, not a bare ZIP. --------
# History (0.9.41/43/46/48): the installer silently fell off "quick" publishes because
# building it was a SEPARATE manual step this script never ran. Now it's part of publish,
# fail-closed — the only ways to ship without a signed installer are explicit flags.
$exe = $null
$exeStable = $null
if ($ZipOnly) {
    Say "-ZipOnly: publishing WITHOUT an installer (explicit opt-out)." "Yellow"
} else {
    $signConfigured = ($env:ADAM_ACS_DLIB -and (Test-Path $env:ADAM_ACS_DLIB) -and $env:ADAM_ACS_METADATA -and (Test-Path $env:ADAM_ACS_METADATA))
    $doSign = [bool]$signConfigured
    if (-not $signConfigured -and -not $AllowUnsigned) {
        Say "Refusing to publish: code signing is not configured." "Red"
        Say "  Set ADAM_ACS_DLIB + ADAM_ACS_METADATA (see scripts/signing.local.ps1 / RELEASE.md)," "Yellow"
        Say "  or pass -AllowUnsigned to ship an unsigned installer (SmartScreen will warn)," "Yellow"
        Say "  or pass -ZipOnly to publish without an installer." "Yellow"
        exit 1
    }
    Say "Building installer for $tag ($(if ($doSign) { 'signed' } else { 'UNSIGNED' })) ..." "Cyan"
    $bi = Join-Path $here "build-installer.ps1"
    try {
        if ($doSign) { & $bi -Zip $zip -Sign } else { & $bi -Zip $zip }
    } catch {
        Say "Refusing to publish: installer build failed: $($_.Exception.Message)" "Red"
        exit 1
    }
    $exe = Join-Path $root ("dist\adam-setup-$tag.exe")
    if (-not (Test-Path $exe)) { Say "Refusing to publish: installer not produced ($exe)." "Red"; exit 1 }
    if ($doSign) {
        $sig = Get-AuthenticodeSignature $exe
        if ($sig.Status -ne 'Valid') {
            Say "Refusing to publish: installer signature is '$($sig.Status)', not Valid." "Red"; exit 1
        }
        Say "Installer signed OK: $($sig.SignerCertificate.Subject)" "Green"
    }
    Say "Built: $exe" "Green"
    # Stable-named copy so the website can link the permanent
    # releases/latest/download/adam-setup.exe redirect (the versioned name
    # changes every release and would break a hard-coded site link).
    $exeStable = Join-Path $root "dist\adam-setup.exe"
    Copy-Item $exe $exeStable -Force
    Say "Copied stable-named installer: $exeStable" "Green"
}

# Assets to attach: ZIP always; installer + its stable-named copy unless -ZipOnly.
$assets = @($zip)
if ($exe) { $assets += $exe }
if ($exeStable) { $assets += $exeStable }

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if ($gh) {
    if ($NotesFile) {
        if (-not (Test-Path $NotesFile)) { Say "NotesFile not found: $NotesFile" "Red"; exit 1 }
        $Notes = Get-Content -Raw -Encoding UTF8 $NotesFile
    }
    if (-not $Notes) { $Notes = "Adam $tag" }

    # Guard: v0.9.62 shipped with release notes reading "a one-time \.99" because the
    # caller passed -Notes "...$24.99..." in a DOUBLE-quoted PowerShell string — $24
    # expanded to nothing and left the backslash behind. That sat live on the page where
    # buyers decide. Refuse to publish notes that show the same signature.
    $priceBug = ($Notes -match '\\\s*\.\d') -or ($Notes -match '(?<![\d$])\.\d{2}\b(?!\d)' -and $Notes -notmatch '\$\d')
    if ($priceBug) {
        Say "REFUSING TO PUBLISH - the release notes look like a variable was eaten:" "Red"
        Say "  $($Notes.Trim())" "Yellow"
        Say "A price lost its digits. In PowerShell, `"...`$24.99...`" expands `$24 to nothing." "Red"
        Say "Fix: use -NotesFile <path>, or single-quote the string: -Notes '...`$24.99...'" "Cyan"
        exit 1
    }

    Say "Publishing $tag to $Repo via gh ..." "Cyan"
    # gh receives the notes as a FILE, never as an argument. PowerShell re-parses a
    # multi-line string when handing it to a native exe, so `--notes $Notes` arrived
    # at gh word-split across many argv entries: v0.9.63's publish died with
    # "no matches found for `record`" after the tests, zip, and SIGNED installer had
    # all succeeded. A file is read verbatim and cannot be re-parsed. (Same class of
    # bug as the wizard's `python -c` snippet — see wizard.ps1's plan step.)
    # UTF8 without BOM: Set-Content -Encoding utf8 writes a BOM in PS 5.1, which
    # would show up as stray characters at the top of the published notes.
    $notesTmp = Join-Path ([System.IO.Path]::GetTempPath()) "adam-relnotes-$tag.md"
    [System.IO.File]::WriteAllText($notesTmp, $Notes, (New-Object System.Text.UTF8Encoding($false)))
    # Create the release (or, if the tag already exists, upload/replace the assets).
    $exists = $false
    try { & $gh release view $tag --repo $Repo *> $null; if ($LASTEXITCODE -eq 0) { $exists = $true } } catch {}
    if ($exists) {
        & $gh release upload $tag @assets --repo $Repo --clobber
    } else {
        & $gh release create $tag @assets --repo $Repo --title $tag --notes-file $notesTmp
    }
    Remove-Item -LiteralPath $notesTmp -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        Say "gh failed (exit $LASTEXITCODE). Make sure the repo exists and you're signed in (gh auth login)." "Red"
        exit 1
    }
    # Post-publish guard: confirm every asset we built is actually attached.
    $published = @(& $gh release view $tag --repo $Repo --json assets --jq '.assets[].name' 2>$null)
    foreach ($a in $assets) {
        $name = Split-Path $a -Leaf
        if ($published -notcontains $name) { Say "PUBLISH INCOMPLETE: '$name' is not attached to the release!" "Red"; exit 1 }
    }
    Say "Published. Assets on $($tag): $((($assets | ForEach-Object { Split-Path $_ -Leaf }) -join ', '))" "Green"
    Say "Installs will see $tag as the latest release." "Green"
} else {
    Say "" "Gray"
    Say "GitHub CLI (gh) not found - publish in the browser instead (one minute):" "Yellow"
    Say "  1. Go to:  https://github.com/$Repo/releases/new" "Cyan"
    Say "     (If the repo doesn't exist yet, create it first as a PUBLIC repo named '$($Repo.Split('/')[-1])'.)" "Cyan"
    Say "  2. Tag version: $tag   Title: $tag" "Cyan"
    Say "  3. Drag these file(s) into 'Attach binaries', then click 'Publish release':" "Cyan"
    foreach ($a in $assets) { Say "       $a" "Green" }
    Say "" "Gray"
    Say "Tip: install gh once (winget install GitHub.cli; gh auth login) and future releases are one command." "Gray"
}
