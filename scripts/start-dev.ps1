# Adam - start the backend (dev).
# Reads host/port from settings.json (via config.py), creates data dirs, and
# launches uvicorn. Run from anywhere; paths resolve to the project root.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

# Pull host/port from config so the launcher and the app never disagree.
$cfg = python -c "import json, config; print(json.dumps({'host': config.HOST, 'port': config.PORT}))"
$c = $cfg | ConvertFrom-Json

if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "WARNING: no .env found. Copy .env.example to .env and set ADAM_TOKEN." -ForegroundColor Yellow
}

Write-Host "Starting Adam on http://$($c.host):$($c.port) ..." -ForegroundColor Cyan
Write-Host "(Ctrl+C drains an in-flight turn then stops; Ctrl+C again forces. Or run scripts\stop-dev.ps1)" -ForegroundColor DarkGray
# --timeout-graceful-shutdown lets the on-shutdown drain finish a long code turn
# on Ctrl+C (cap 300s + headroom) before uvicorn force-exits. See config.DRAIN_MAX_WAIT_SECONDS.
python -m uvicorn server:app --host $c.host --port $c.port --timeout-graceful-shutdown 330
