# audit-deps.ps1 — CVE scan of Adam's pinned Python dependencies.
#
# Every version in requirements.txt is pinned with ==, which stops a dependency
# changing under us but says nothing about a pinned version later turning out to
# be vulnerable. Nothing was watching for that, so this is the watcher.
#
# Usage:
#   .\scripts\audit-deps.ps1              # scan requirements.txt (what ships)
#   .\scripts\audit-deps.ps1 -Environment # scan what's actually installed here
#   .\scripts\audit-deps.ps1 -Json out.json
#
# Exit codes:  0 = clean · 1 = vulnerabilities found · 2 = could not run.
# The release build treats a non-zero exit as a WARNING, not a stop: a CVE in a
# transitive dep shouldn't block shipping a fix on a Friday. Read it and decide.

[CmdletBinding()]
param(
    [switch]$Environment,
    [string]$Json = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$reqs = Join-Path $repo "requirements.txt"

Write-Host ""
Write-Host "Adam - dependency vulnerability audit" -ForegroundColor Cyan
Write-Host ("=" * 44)

# pip-audit is a dev dependency, so a plain runtime install won't have it.
$has = $null
try { $has = python -m pip show pip-audit 2>$null } catch { $has = $null }
if (-not $has) {
    Write-Host "pip-audit is not installed." -ForegroundColor Yellow
    Write-Host "  python -m pip install -r requirements-dev.txt"
    exit 2
}

$args = @()
if ($Environment) {
    Write-Host "Scanning: the installed environment"
} else {
    if (-not (Test-Path $reqs)) {
        Write-Host "requirements.txt not found at $reqs" -ForegroundColor Red
        exit 2
    }
    Write-Host "Scanning: $reqs"
    $args += @("-r", $reqs)
}
if ($Json) { $args += @("-f", "json", "-o", $Json) }
Write-Host ""

# stderr is where pip-audit puts progress; let it through rather than wrapping it
# in an ErrorRecord (PS 5.1 turns a redirected native stderr into a failure).
python -m pip_audit @args
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "CLEAN - no known vulnerabilities." -ForegroundColor Green
} else {
    Write-Host "FINDINGS - review the table above." -ForegroundColor Yellow
    Write-Host "A fix is usually a version bump in requirements.txt; re-run to confirm."
}
exit $code
