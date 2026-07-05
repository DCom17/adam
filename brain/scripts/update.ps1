# Update Adam framework from the remote, touching ONLY core_manifest.txt files.
#
# Safe by construction: every file NOT in the manifest is your data (profile,
# memory, logs, tasks, dashboard/calendar state, your graph vocabulary) and this
# script never reads or writes it. Previous versions of changed framework files
# are backed up under _update_backup\<timestamp>\ before being overwritten.
#
# Requires this install to be a git clone of the kit (origin remote present).

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot   # scripts\ -> repo root
Set-Location $RepoRoot

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
  Write-Error "This install isn't a git clone, so updates can't be pulled automatically. Re-install by cloning the repo (see SETUP.md)."
  exit 1
}

# Remote/branch (defaults origin/main; overridable in config.update)
$remote = "origin"; $branch = "main"
$cfgPath = Join-Path $RepoRoot "config\adam.config.json"
if (Test-Path $cfgPath) {
  $cfg = Get-Content -Raw $cfgPath | ConvertFrom-Json
  if ($cfg.update) {
    if ($cfg.update.remote) { $remote = [string]$cfg.update.remote }
    if ($cfg.update.branch) { $branch = [string]$cfg.update.branch }
  }
}
$ref = "$remote/$branch"

$oldVersion = (Get-Content (Join-Path $RepoRoot "VERSION") -Raw).Trim()

Write-Host "Fetching $remote ..."
git fetch $remote --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "git fetch failed. Check your network and repo access."; exit 1 }

$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$backupDir = Join-Path $RepoRoot ("_update_backup\" + $stamp)

$manifest = Get-Content (Join-Path $RepoRoot "core_manifest.txt") |
  Where-Object { $_ -and ($_ -notmatch '^\s*#') } |
  ForEach-Object { $_.Trim() } | Where-Object { $_ }

$updated = @()
foreach ($path in $manifest) {
  $spec = [string]::Format("{0}:{1}", $ref, $path)
  # Skip files the remote doesn't have (e.g. a manifest entry newer than the remote).
  git cat-file -e $spec 2>$null
  if ($LASTEXITCODE -ne 0) { continue }

  # Skip files already identical to the remote version — nothing to refresh.
  git diff --quiet $ref -- $path
  if ($LASTEXITCODE -eq 0) { continue }

  $full = Join-Path $RepoRoot $path
  if (Test-Path $full) {
    $bdest = Join-Path $backupDir $path
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bdest) | Out-Null
    Copy-Item $full $bdest -Force
  }
  git checkout $ref -- $path
  if ($LASTEXITCODE -eq 0) { $updated += $path }
}

$newVersion = (Get-Content (Join-Path $RepoRoot "VERSION") -Raw).Trim()

Write-Host ""
Write-Host "Update complete: $oldVersion -> $newVersion"
Write-Host ("Framework files refreshed: " + $updated.Count)
if ($updated.Count -gt 0) { Write-Host ("Previous versions backed up to: " + $backupDir) }
Write-Host "Your personal data was not touched."
Write-Host "If $oldVersion differs from $newVersion, check MIGRATIONS.md for any data migrations to apply."
