# Jarvis Voice Local - check the backend is up and report its config sanity.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

$port = [int](python -c "import config; print(config.PORT)")
$url = "http://127.0.0.1:$port/health"

try {
    $r = Invoke-RestMethod -Uri $url -TimeoutSec 5
    Write-Host "Jarvis is UP at $url" -ForegroundColor Green
    Write-Host ("  app:               {0} v{1}" -f $r.app, $r.version)
    Write-Host ("  claude_configured: {0}" -f $r.claude_configured)
    Write-Host ("  vault_configured:  {0}" -f $r.vault_configured)
    Write-Host ("  voice_model:       {0}" -f $r.voice_model)
    Write-Host ("  push_enabled:      {0}" -f $r.push_enabled)
    Write-Host ("  twilio_enabled:    {0}" -f $r.twilio_enabled)
    if (-not $r.claude_configured) {
        Write-Host "  WARNING: Claude executable not found - set claude_exe in settings.json." -ForegroundColor Yellow
    }
    if (-not $r.vault_configured) {
        Write-Host "  WARNING: vault_path does not exist - set vault_path in settings.json." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Jarvis is DOWN (no response at $url)." -ForegroundColor Red
    Write-Host "  Start it with scripts\start-dev.ps1" -ForegroundColor DarkGray
    exit 1
}
