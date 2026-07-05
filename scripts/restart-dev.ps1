# Adam - restart the dev backend.
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "stop-dev.ps1")
Start-Sleep -Seconds 1
& (Join-Path $PSScriptRoot "start-dev.ps1")
