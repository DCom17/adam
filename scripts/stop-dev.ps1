# Adam - stop the dev backend.
# Finds the process listening on the configured port and stops it.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

$port = python -c "import config; print(config.PORT)"
$port = [int]$port

$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "Nothing listening on port $port." -ForegroundColor Yellow
    return
}

$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $pids) {
    try {
        $p = Get-Process -Id $procId -ErrorAction Stop
        Write-Host "Stopping $($p.ProcessName) (PID $procId) on port $port ..." -ForegroundColor Cyan
        Stop-Process -Id $procId -Force -Confirm:$false
    } catch {
        Write-Host "Could not stop PID ${procId}: $_" -ForegroundColor Red
    }
}
Write-Host "Stopped." -ForegroundColor Green
