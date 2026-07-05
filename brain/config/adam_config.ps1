# Shared config loader for Adam Kit PowerShell scripts.
#
# Dot-source it from any script:
#     . "$PSScriptRoot\..\config\adam_config.ps1"
#
# Provides:
#   $AdamConfig      - parsed object from config/adam.config.json
#   Get-AdamEnv NAME - read a secret from .env (errors if required and missing)
#
# This keeps every URL, path, and token out of the scripts themselves. Secrets
# live in .env (gitignored); non-secret settings live in config/adam.config.json.

# When dot-sourced, $PSScriptRoot is THIS file's folder (config/).
$script:ConfigDir  = $PSScriptRoot
$script:RepoRoot   = Split-Path -Parent $script:ConfigDir
$script:ConfigPath = Join-Path $script:ConfigDir "adam.config.json"
$script:EnvPath    = Join-Path $script:RepoRoot ".env"

if (-not (Test-Path $script:ConfigPath)) {
  Write-Error "Missing config/adam.config.json. Copy config/adam.config.example.json to config/adam.config.json and fill it in (or run 'Bootstrap Adam')."
  exit 1
}

$AdamConfig = Get-Content -Raw -Encoding UTF8 $script:ConfigPath | ConvertFrom-Json

# Parse .env into a hashtable once.
$script:AdamEnv = @{}
if (Test-Path $script:EnvPath) {
  foreach ($line in Get-Content $script:EnvPath) {
    $t = $line.Trim()
    if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
      $i = $t.IndexOf("=")
      $k = $t.Substring(0, $i).Trim()
      $v = $t.Substring($i + 1).Trim()
      $script:AdamEnv[$k] = $v
    }
  }
}

function Get-AdamEnv {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [bool]$Required = $true
  )
  $val = $script:AdamEnv[$Name]
  if ([string]::IsNullOrWhiteSpace($val)) {
    if ($Required) {
      Write-Error "Missing '$Name' in .env. Copy .env.example to .env and fill it in (see the relevant setup_*.md)."
      exit 1
    }
    return ""
  }
  return $val
}
