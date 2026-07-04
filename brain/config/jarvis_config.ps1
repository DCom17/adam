# Shared config loader for JARVIS Kit PowerShell scripts.
#
# Dot-source it from any script:
#     . "$PSScriptRoot\..\config\jarvis_config.ps1"
#
# Provides:
#   $JarvisConfig      - parsed object from config/jarvis.config.json
#   Get-JarvisEnv NAME - read a secret from .env (errors if required and missing)
#
# This keeps every URL, path, and token out of the scripts themselves. Secrets
# live in .env (gitignored); non-secret settings live in config/jarvis.config.json.

# When dot-sourced, $PSScriptRoot is THIS file's folder (config/).
$script:ConfigDir  = $PSScriptRoot
$script:RepoRoot   = Split-Path -Parent $script:ConfigDir
$script:ConfigPath = Join-Path $script:ConfigDir "jarvis.config.json"
$script:EnvPath    = Join-Path $script:RepoRoot ".env"

if (-not (Test-Path $script:ConfigPath)) {
  Write-Error "Missing config/jarvis.config.json. Copy config/jarvis.config.example.json to config/jarvis.config.json and fill it in (or run 'Bootstrap JARVIS')."
  exit 1
}

$JarvisConfig = Get-Content -Raw -Encoding UTF8 $script:ConfigPath | ConvertFrom-Json

# Parse .env into a hashtable once.
$script:JarvisEnv = @{}
if (Test-Path $script:EnvPath) {
  foreach ($line in Get-Content $script:EnvPath) {
    $t = $line.Trim()
    if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
      $i = $t.IndexOf("=")
      $k = $t.Substring(0, $i).Trim()
      $v = $t.Substring($i + 1).Trim()
      $script:JarvisEnv[$k] = $v
    }
  }
}

function Get-JarvisEnv {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [bool]$Required = $true
  )
  $val = $script:JarvisEnv[$Name]
  if ([string]::IsNullOrWhiteSpace($val)) {
    if ($Required) {
      Write-Error "Missing '$Name' in .env. Copy .env.example to .env and fill it in (see the relevant setup_*.md)."
      exit 1
    }
    return ""
  }
  return $val
}
