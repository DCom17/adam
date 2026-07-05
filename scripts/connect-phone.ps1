# Thin wrapper around scripts/connect-phone.py so Windows users can run the connect-phone
# helper without fighting PowerShell execution policy. All logic lives in the Python core.
# READ-ONLY / PRINT-ONLY: it never changes Tailscale, Adam config, or anything else.
#
#   .\scripts\connect-phone.ps1                 # human-readable guidance
#   .\scripts\connect-phone.ps1 -Json           # machine-readable diagnostic (no secrets)
#   .\scripts\connect-phone.ps1 -Port 8443      # force the Adam HTTPS serve port
#   .\scripts\connect-phone.ps1 -TargetPort 8010 # override the local Adam port

param(
    [switch]$Json,
    [int]$Port,
    [int]$TargetPort
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "connect-phone.py"

$pyArgs = @($script)
if ($Json)       { $pyArgs += "--json" }
if ($Port)       { $pyArgs += @("--port", $Port) }
if ($TargetPort) { $pyArgs += @("--target-port", $TargetPort) }

python @pyArgs
exit $LASTEXITCODE
