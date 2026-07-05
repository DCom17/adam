# Adam - rotate the bearer token (ROTATE-TOKEN.cmd runs this).
#
# Use this if your token may have been exposed (a photographed token QR, a lost
# device). It generates a fresh ADAM_TOKEN in .env - backing the old .env up
# first - and tells you what to do next. Read-only otherwise: it never touches
# settings.json, your files, or the running server (the new token takes effect
# on the next server start).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

Write-Host ""
Say "=== Adam - rotate your access token ===" "Cyan"
Write-Host ""

$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) {
    Say "No .env found - Adam isn't set up yet. Double-click SETUP first." "Red"
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}

# Find Python (same probing as the launcher).
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) {
    $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
    if ($cand) { $pyExe = $cand.FullName }
}
if (-not $pyExe) {
    Say "Python not found - it's needed to generate a secure token." "Red"
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}

Say "This will invalidate the CURRENT token on every device (PC browser + phone)." "Yellow"
Say "You'll sign in again afterward - the launcher signs the PC back in for you," "Yellow"
Say "and the console's 'Connect phone' QR re-pairs the phone." "Yellow"
Write-Host ""
$ok = Read-Host "Type YES to rotate the token"
if ($ok -ne "YES") { Say "Nothing changed."; Read-Host "Press Enter to close" | Out-Null; exit 0 }

# Back up .env first (*.bak files never ship in releases and are excluded from updates).
$stamp = Get-Date -Format "yyyyMMddHHmmss"
Copy-Item -LiteralPath $envPath -Destination "$envPath.bak.$stamp"

# Generate the new token (same shape setup uses: 64 hex chars).
$newToken = (& $pyExe -c "import secrets; print(secrets.token_hex(32))").Trim()
if (-not $newToken -or $newToken.Length -lt 32) {
    Say "Token generation failed - nothing was changed." "Red"
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}

# Replace (or append) the ADAM_TOKEN line, preserving everything else byte-for-byte.
$lines = Get-Content -LiteralPath $envPath
$found = $false
$out = foreach ($line in $lines) {
    if (-not $found -and $line -match '^\s*ADAM_TOKEN\s*=') { $found = $true; "ADAM_TOKEN=$newToken" }
    else { $line }
}
if (-not $found) { $out = @($out) + "ADAM_TOKEN=$newToken" }
Set-Content -LiteralPath $envPath -Value $out -Encoding ascii

Say "Done. A fresh token is in .env (old .env kept as .env.bak.$stamp)." "Green"
Write-Host ""
Say "Next steps:" "White"
Say "  1. Restart Adam: close the black Adam window, reopen it from the desktop" "Gray"
Say "     icon. The old token stops working the moment it restarts." "Gray"
Say "  2. The PC browser signs back in automatically via the launcher." "Gray"
Say "  3. On your phone: open the Operator console -> Connect phone -> scan the" "Gray"
Say "     token QR again (or paste the new token in the app's settings)." "Gray"
Write-Host ""
Read-Host "Press Enter to close" | Out-Null
exit 0
