# Adam - check the backend is up and report its config sanity.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

$port = [int](python -c "import config; print(config.PORT)")
$url = "http://127.0.0.1:$port/health"

# Read ADAM_TOKEN from .env (read-only; JARVIS_TOKEN accepted for pre-rename
# installs) — /health only returns the full config summary to the token holder;
# anonymous callers get liveness + version.
$token = $null
try {
    foreach ($line in (Get-Content -LiteralPath (Join-Path $root ".env"))) {
        $t = $line.Trim()
        if ($t.StartsWith("#")) { continue }
        if ($t -match '^\s*(?:ADAM_TOKEN|JARVIS_TOKEN)\s*=\s*(.*)$') {
            $val = $Matches[1].Trim()
            if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'")))) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            $token = $val
            break
        }
    }
} catch { $token = $null }
$hdrs = @{}
if (-not [string]::IsNullOrWhiteSpace($token) -and $token -ne "replace-with-a-long-random-token") {
    $hdrs["Authorization"] = "Bearer $token"
}

try {
    $r = Invoke-RestMethod -Uri $url -TimeoutSec 5 -Headers $hdrs
    Write-Host "Adam is UP at $url" -ForegroundColor Green
    Write-Host ("  app:               {0} v{1}" -f $r.app, $r.version)
    if ($null -eq $r.claude_configured) {
        Write-Host "  (details need the token - set ADAM_TOKEN in .env to see the full summary)" -ForegroundColor DarkGray
        exit 0
    }
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
    Write-Host "Adam is DOWN (no response at $url)." -ForegroundColor Red
    Write-Host "  Start it with scripts\start-dev.ps1" -ForegroundColor DarkGray
    exit 1
}
