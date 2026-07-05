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
Write-Host "(Ctrl+C to stop, or run scripts\stop-dev.ps1 from another window)" -ForegroundColor DarkGray
python -m uvicorn server:app --host $c.host --port $c.port
