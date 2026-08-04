# Adam - restart WITHOUT losing an in-flight turn.
#
# Use this INSTEAD of clicking the X on the Adam server window. It asks the running
# server to DRAIN - finish the in-flight code turn (up to config.DRAIN_MAX_WAIT_SECONDS,
# default 5 min) and refuse new ones - then waits for it to exit and reopens Adam
# exactly like the desktop icon does.
#
# Why not just close the window? A window-close (X) sends CTRL_CLOSE_EVENT and Windows
# hard-kills the process in ~5s - far too short to finish a long turn. Only a
# cooperative stop (this script, or Ctrl+C in the window) can drain.
#
#   scripts\restart-adam.ps1           # drain, then reopen
#   scripts\restart-adam.ps1 -Force    # stop immediately (old behavior; in-flight turn is lost)

param([switch]$Force)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root
function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

# Port from config so the launcher and the app never disagree.
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $pyExe = "python" }
$port = [int](& $pyExe -c "import config; print(config.PORT)")
$healthUrl = "http://127.0.0.1:$port/health"
$drainUrl  = "http://127.0.0.1:$port/drain"

# Read ADAM_TOKEN (or legacy JARVIS_TOKEN) from .env - /drain is token-gated.
$adamToken = $null
$envPath = Join-Path $root ".env"
if (Test-Path $envPath) {
    foreach ($line in Get-Content $envPath) {
        if ($line -match '^\s*(?:ADAM_TOKEN|JARVIS_TOKEN)\s*=\s*(.*)$') {
            $val = $Matches[1].Trim()
            if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            if ($val) { $adamToken = $val }
        }
    }
}

# Is Adam up right now?
$up = $false
try { $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3; if ($h.status -eq "ok") { $up = $true } } catch { $up = $false }

# Without a token we can't authorize the drain - fall back to an immediate stop.
if ($up -and -not $Force -and -not $adamToken) {
    Say "No ADAM_TOKEN in .env, so I can't authorize a drain - restarting immediately instead." "Yellow"
    $Force = $true
}

# Remember the current 'Adam' server window(s) so we can close the idle shell the
# server leaves behind after it drains and exits (it launches with -NoExit).
$oldWindows = @()
try {
    $oldWindows = Get-Process powershell -ErrorAction SilentlyContinue |
                  Where-Object { $_.MainWindowTitle -eq "Adam" } |
                  Select-Object -ExpandProperty Id
} catch {}

if ($up) {
    if ($Force) {
        Say "Force restart: stopping Adam now (any in-flight turn is lost)." "Yellow"
        & (Join-Path $PSScriptRoot "stop-dev.ps1")
    }
    else {
        Say "Asking Adam to finish the in-flight turn, then restart..." "Cyan"
        $drained = $false
        try {
            $d = Invoke-RestMethod -Uri $drainUrl -Method Post -TimeoutSec 10 `
                    -Headers @{ Authorization = "Bearer $adamToken" }
            Say "Draining $($d.running_jobs) in-flight turn(s), up to $($d.max_wait_s)s. Ctrl+C to stop waiting." "DarkGray"
        }
        catch {
            Say "Drain request failed ($_). Falling back to an immediate stop." "Yellow"
            & (Join-Path $PSScriptRoot "stop-dev.ps1")
            $drained = $true   # handled via stop-dev; skip the wait loop
        }
        if (-not $drained) {
            # The server exits itself once drained (or at its cap). Wait for the
            # port to go quiet, a touch past the server's 330s graceful ceiling.
            $capSeconds = 345
            for ($i = 0; $i -lt ($capSeconds * 2); $i++) {
                Start-Sleep -Milliseconds 500
                try { $null = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 }
                catch { $drained = $true; break }
                if ($i -gt 0 -and ($i % 20) -eq 0) { Say "  still draining... ($([int]($i / 2))s)" "DarkGray" }
            }
            if ($drained) { Say "Adam drained and stopped cleanly." "Green" }
            else { Say "Drain didn't finish in time - forcing a stop." "Yellow"; & (Join-Path $PSScriptRoot "stop-dev.ps1") }
        }
    }

    # Close the idle server window(s) left behind, and backstop the port.
    foreach ($winId in $oldWindows) {
        try { Stop-Process -Id $winId -Force -ErrorAction SilentlyContinue } catch {}
    }
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($procId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}
else {
    Say "Adam wasn't running - just starting it." "DarkGray"
}

Start-Sleep -Seconds 1
Say "Reopening Adam..." "Cyan"
& (Join-Path $PSScriptRoot "start-adam.ps1") -AppWindow
