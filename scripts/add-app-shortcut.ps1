# Jarvis Voice Local - add the pinnable JARVIS app shortcut (Desktop + Start Menu).
#
# Creates a "JARVIS" shortcut that opens Jarvis like a real app: click it to start the
# server (if needed) and open a clean app window, already signed in. The shortcut targets
# wscript.exe -> scripts\jarvis-app.vbs, which is what makes it PINNABLE to the Windows
# taskbar with the blue-orb icon (Windows 11 won't pin a .cmd/console launcher). It also
# tidies up the older "Jarvis Voice Local" launcher shortcut this script used to make, so
# you don't end up with two confusing icons. Re-runnable and harmless. The setup wizard
# runs this for you; you can also run it by hand any time.

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path   # scripts/
$root    = Split-Path -Parent $here                          # project root
$vbs     = Join-Path $here "jarvis-app.vbs"
$icon    = Join-Path $root "web\icon.ico"
$oldCmd  = Join-Path $root "START.cmd"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

function New-AppLnk($path) {
    $ws  = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($path)
    $lnk.TargetPath       = $wscript
    $lnk.Arguments        = '"' + $vbs + '"'
    $lnk.WorkingDirectory = $root
    $lnk.Description       = "JARVIS"
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
    $lnk.Save()
}

# Remove a stale .lnk ONLY if it's our own old launcher (points at this install's
# START.cmd). Never touches anything else the user made.
function Remove-OldLnk($path) {
    if (-not (Test-Path $path)) { return }
    try {
        $ws = New-Object -ComObject WScript.Shell
        $t  = $ws.CreateShortcut($path).TargetPath
        if ($t -and ($t -ieq $oldCmd)) { Remove-Item -LiteralPath $path -Force }
    } catch {}
}

$made = @()
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    Remove-OldLnk (Join-Path $desktop "Jarvis Voice Local.lnk")
    New-AppLnk (Join-Path $desktop "JARVIS.lnk")
    $made += "Desktop"
} catch {}
try {
    $startDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Jarvis Voice Local"
    if (-not (Test-Path $startDir)) { New-Item -ItemType Directory -Path $startDir -Force | Out-Null }
    Remove-OldLnk (Join-Path $startDir "Jarvis Voice Local.lnk")
    New-AppLnk (Join-Path $startDir "JARVIS.lnk")
    $made += "Start Menu"
} catch {}

if ($made.Count) {
    Write-Host ("Added the 'JARVIS' app shortcut to: " + ($made -join " and ") + ".") -ForegroundColor Green
    Write-Host "Tip: right-click it on the Desktop -> Show more options -> Pin to taskbar to keep JARVIS on your bottom bar." -ForegroundColor Cyan
} else {
    Write-Host "Couldn't create the app shortcut (you can still open Jarvis with START.cmd)." -ForegroundColor Yellow
}
