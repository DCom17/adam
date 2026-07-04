# Thin wrapper around scripts/make_release.py so Windows users can build a release ZIP
# without fighting PowerShell execution policy or remembering the Python invocation.
# All logic (allow-list + fail-closed deny guard + zip) lives in the Python core.
#
#   .\scripts\make_release.ps1                 # build dist\jarvis-voice-local-vX.Y.Z.zip
#   .\scripts\make_release.ps1 -List           # print the staged file list, build nothing
#   .\scripts\make_release.ps1 -Version 0.9.0  # override the version label
#   .\scripts\make_release.ps1 -Out C:\tmp     # choose the output directory

param(
    [switch]$List,
    [string]$Version,
    [string]$Out
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "make_release.py"

$pyArgs = @($script)
if ($List)    { $pyArgs += "--list" }
if ($Version) { $pyArgs += @("--version", $Version) }
if ($Out)     { $pyArgs += @("--out", $Out) }

python @pyArgs
exit $LASTEXITCODE
