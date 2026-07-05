# Adam - build the Windows installer (ROADMAP P3-1).
#
# Wraps make_release.py + Inno Setup so the installer payload is EXACTLY the
# guarded release ZIP: every fail-closed guard (secret scan, allow-list,
# import-ship, boot deps) runs before a single installer byte is written.
#
#   .\scripts\build-installer.ps1              # build ZIP fresh, then installer
#   .\scripts\build-installer.ps1 -Zip dist\adam-local-v0.9.38.zip   # reuse a ZIP
#
# Output: dist\adam-setup-v<version>.exe
# Requires Inno Setup 6 (winget install JRSoftware.InnoSetup).

param(
    [string]$Zip = "",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path   # scripts/
$root = Split-Path -Parent $here                          # repo root
if (-not $OutDir) { $OutDir = Join-Path $root "dist" }

# --- locate ISCC ---------------------------------------------------------
$iscc = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }

# --- version (from config.py, same source of truth as make_release) ------
Push-Location $root
try { $version = (python -c "import config; print(config.APP_VERSION)").Trim() }
finally { Pop-Location }
if (-not $version) { throw "could not read config.APP_VERSION" }

# --- build or reuse the guarded ZIP --------------------------------------
if (-not $Zip) {
    Write-Host "[1/4] Building the guarded release ZIP (all guards fail-closed)..."
    Push-Location $root
    try { python (Join-Path $here "make_release.py") | Write-Host; if ($LASTEXITCODE -ne 0) { throw "make_release.py failed" } }
    finally { Pop-Location }
    $Zip = Join-Path $OutDir "adam-local-v$version.zip"
} else {
    Write-Host "[1/4] Reusing ZIP: $Zip"
    if (-not (Split-Path $Zip -IsAbsolute)) { $Zip = Join-Path $root $Zip }
}
if (-not (Test-Path $Zip)) { throw "release ZIP not found: $Zip" }

# --- stage: extract ZIP, move brain\ aside so the .iss can install it
#     onlyifdoesntexist (reinstall must never clobber a user's brain) -----
Write-Host "[2/4] Staging installer payload..."
$stage = Join-Path $env:TEMP "adam-installer-stage"
$brainStage = Join-Path $env:TEMP "adam-installer-brain"
foreach ($d in @($stage, $brainStage)) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
Expand-Archive -Path $Zip -DestinationPath $stage
if (-not (Test-Path (Join-Path $stage "SETUP.cmd"))) { throw "staged tree looks wrong (no SETUP.cmd)" }
Move-Item (Join-Path $stage "brain") $brainStage

# --- compile --------------------------------------------------------------
Write-Host "[3/4] Compiling installer (Inno Setup)..."
& $iscc /Qp "/DStageDir=$stage" "/DBrainDir=$brainStage" "/DAppVersion=$version" "/DOutDir=$OutDir" (Join-Path $here "adam-installer.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

$exe = Join-Path $OutDir "adam-setup-v$version.exe"
if (-not (Test-Path $exe)) { throw "expected output missing: $exe" }

# --- tidy -----------------------------------------------------------------
Write-Host "[4/4] Cleaning staging..."
Remove-Item -Recurse -Force $stage, $brainStage

$size = "{0:N1} MB" -f ((Get-Item $exe).Length / 1MB)
Write-Host ""
Write-Host "[OK] wrote $exe ($size)" -ForegroundColor Green
Write-Host "     silent install:   adam-setup-v$version.exe /VERYSILENT /SUPPRESSMSGBOXES"
Write-Host "     (unsigned until the Azure Artifact Signing cert lands - SmartScreen will warn)"
