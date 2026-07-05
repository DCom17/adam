# Adam - add the pinnable Adam app shortcut (Desktop + Start Menu).
#
# Creates an "Adam" shortcut that opens Adam like a real app: click it to start the
# server (if needed) and open a clean app window, already signed in. The shortcut targets
# wscript.exe -> scripts\adam-app.vbs, which is what makes it PINNABLE to the Windows
# taskbar with the blue-orb icon (Windows 11 won't pin a .cmd/console launcher). It also
# tidies up the older launcher shortcuts this script used to make — including the
# pre-rename "JARVIS" ones — so you don't end up with two confusing icons.
# Re-runnable and harmless. The setup wizard runs this for you; you can also run
# it by hand any time.

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path   # scripts/
$root    = Split-Path -Parent $here                          # project root
$vbs     = Join-Path $here "adam-app.vbs"
$icon    = Join-Path $root "web\icon.ico"
$oldCmd  = Join-Path $root "START.cmd"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

function New-AppLnk($path) {
    $ws  = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($path)
    $lnk.TargetPath       = $wscript
    $lnk.Arguments        = '"' + $vbs + '"'
    $lnk.WorkingDirectory = $root
    $lnk.Description       = "Adam"
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
    $lnk.Save()
}

# Remove a stale .lnk ONLY if it's our own old launcher: it points at this
# install's START.cmd, or at wscript running this install's (pre-rename)
# jarvis-app.vbs. Never touches anything else the user made.
$oldVbs = Join-Path $here "jarvis-app.vbs"
function Remove-OldLnk($path) {
    if (-not (Test-Path $path)) { return }
    try {
        $ws  = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut($path)
        $t   = $lnk.TargetPath
        $ours = ($t -and ($t -ieq $oldCmd)) -or
                ($t -and ($t -ieq $wscript) -and ($lnk.Arguments -match [regex]::Escape($oldVbs)))
        if ($ours) { Remove-Item -LiteralPath $path -Force }
    } catch {}
}

$made = @()
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    Remove-OldLnk (Join-Path $desktop "Adam.lnk")
    Remove-OldLnk (Join-Path $desktop "JARVIS.lnk")   # pre-rename installs
    New-AppLnk (Join-Path $desktop "Adam.lnk")
    $made += "Desktop"
} catch {}
try {
    $startDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Adam"
    if (-not (Test-Path $startDir)) { New-Item -ItemType Directory -Path $startDir -Force | Out-Null }
    Remove-OldLnk (Join-Path $startDir "Adam.lnk")
    New-AppLnk (Join-Path $startDir "Adam.lnk")
    $made += "Start Menu"
} catch {}
# Pre-rename Start Menu folder: remove our old entry, and the folder if now empty.
try {
    $oldStartDir = Join-Path ([Environment]::GetFolderPath("Programs")) "JARVIS"
    if (Test-Path $oldStartDir) {
        Remove-OldLnk (Join-Path $oldStartDir "JARVIS.lnk")
        if (-not (Get-ChildItem -LiteralPath $oldStartDir)) {
            Remove-Item -LiteralPath $oldStartDir -Force
        }
    }
} catch {}

if ($made.Count) {
    Write-Host ("Added the 'Adam' app shortcut to: " + ($made -join " and ") + ".") -ForegroundColor Green
    Write-Host "Tip: right-click it on the Desktop -> Show more options -> Pin to taskbar to keep Adam on your bottom bar." -ForegroundColor Cyan
} else {
    Write-Host "Couldn't create the app shortcut (you can still open Adam with START.cmd)." -ForegroundColor Yellow
}
