# Adam - post-update relauncher.
#
# The server spawns this (detached + hidden) right BEFORE it exits to finish a
# self-update. Its only job: wait for the old server to fully come down, then
# start Adam again from the freshly-updated files - so the new version comes
# back up on its own and the user never has to close a window and reopen.
#
# It is safe to run by hand, too: it waits for the port to free before starting,
# and start-adam.ps1 refuses to launch a second server if one is already up.

param([int]$Port = 8000)

$ErrorActionPreference = "SilentlyContinue"
$here = $PSScriptRoot
$healthUrl = "http://127.0.0.1:$Port/health"

# 1) Wait (up to ~90s) for the OLD server to come down. While it drains an
#    in-flight turn it still answers /health, so keep polling until it stops
#    answering (connection refused = it's really gone).
for ($i = 0; $i -lt 180; $i++) {
    try {
        $null = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing
        # still up (draining) - keep waiting
    } catch {
        break   # no longer answering -> it's down
    }
    Start-Sleep -Milliseconds 500
}

# 2) Let the socket fully release, then bring Adam back.
Start-Sleep -Milliseconds 800

# Some installs (the owner's dev box) run a boot/supervisor SCHEDULED TASK that owns
# :8000 and restarts the server on its own. If one exists, let IT bring the server
# back - starting our own windowed instance too would race it for the port and can
# leave a stray "Adam stopped unexpectedly" window. Normal end-user installs have no
# such task, so $boot stays $null and we start Adam ourselves (the branch below).
$boot = $null
try {
    $boot = Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.State -ne 'Disabled' -and (@($_.Actions | Where-Object { $_.Arguments -match 'uvicorn\s+server:app' }).Count -gt 0) } |
        Select-Object -First 1
} catch {}

$broughtUp = $false
if ($boot) {
    # Did the supervisor already bring it back on its own? Then leave it alone.
    try { $null = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing; $broughtUp = $true } catch {}
    if (-not $broughtUp) {
        # Deterministically restart the supervisor: clear any pending/running instance,
        # then start exactly one fresh one - promptly, rather than waiting out its
        # restart-on-failure timer, and without a competing instance from us.
        try {
            Stop-ScheduledTask  -TaskName $boot.TaskName -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 400
            Start-ScheduledTask -TaskName $boot.TaskName -ErrorAction SilentlyContinue
        } catch {}
        for ($i = 0; $i -lt 40; $i++) {            # up to ~20s for it to answer
            try { $null = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing; $broughtUp = $true; break } catch {}
            Start-Sleep -Milliseconds 500
        }
    }
}

if (-not $broughtUp) {
    # No supervisor task (the normal end-user install), or it didn't answer in time -
    # start Adam ourselves from the updated files. adam-app.vbs is the pinnable
    # launcher (server in its own window, then the app) - one code path, no surprises.
    $vbs = Join-Path $here "adam-app.vbs"
    if (Test-Path $vbs) {
        Start-Process -FilePath "wscript.exe" -ArgumentList ('"' + $vbs + '"')
    } else {
        # Fallback if the pinnable launcher is missing: start the server directly.
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $here "start-adam.ps1"), "-AppWindow")
    }
}
