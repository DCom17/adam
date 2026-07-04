# Jarvis Voice Local - manual safety probe.
# Empirically checks whether Claude Code can still write files directly, in both
# the unrestricted (legacy) and restricted (safe-mode) spawns. Runs the real
# claude CLI in a throwaway temp dir - never your vault. Costs a couple of small
# model calls.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

Write-Host "Running agent write probe (spawns claude twice in a temp dir)..." -ForegroundColor Cyan
python agent_write_probe.py
exit $LASTEXITCODE
