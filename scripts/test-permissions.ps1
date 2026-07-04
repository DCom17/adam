# Jarvis Voice Local - run the permission-system test suite.
# Exercises the Level 3 layer (read/write allow-lists, blocked + protected paths,
# destructive detection, backups, audit log, approval lifecycle) against a
# throwaway sandbox. Does not touch your real data/ tree or require the server.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

Write-Host "Running permission-system tests..." -ForegroundColor Cyan
python test_permissions.py
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host "All permission tests passed." -ForegroundColor Green
} else {
    Write-Host "Permission tests FAILED (exit $code)." -ForegroundColor Red
}
exit $code
