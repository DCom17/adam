# Jarvis Voice Local - copy your JARVIS_TOKEN to the clipboard (no manual .env editing).
#
# This helper makes first desktop sign-in easier: instead of opening .env by hand and
# hunting for the token, run this and the token lands on your clipboard, ready to paste
# into the Jarvis app's settings field.
#
# It is intentionally minimal and safe:
#   * READ-ONLY: it reads .env to find JARVIS_TOKEN and nothing else;
#   * it NEVER prints the token, never logs it, never sends it anywhere;
#   * it does NOT modify .env or settings.json;
#   * it shows only a masked hint (length + last 4 chars) so you can confirm the right one.
#
# Usage:   .\scripts\copy-token.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

# 1) .env present?
if (-not (Test-Path -LiteralPath $envFile)) {
    Say "No .env found next to the app - setup hasn't been run yet." "Yellow"
    Say "Run first:  python scripts\setup.py" "Cyan"
    Say "(That generates your JARVIS_TOKEN. Then re-run this helper.)" "DarkGray"
    exit 1
}

# 2) Find JARVIS_TOKEN (skip comments; tolerate spaces and optional quotes).
$token = $null
foreach ($line in (Get-Content -LiteralPath $envFile)) {
    $t = $line.Trim()
    if ($t.StartsWith("#")) { continue }
    if ($t -match '^\s*JARVIS_TOKEN\s*=\s*(.*)$') {
        $val = $Matches[1].Trim()
        # strip a single pair of surrounding quotes, if present
        if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'")))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        $token = $val
        break
    }
}

# 3) Missing or placeholder/empty token?
if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "replace-with-a-long-random-token") {
    Say "JARVIS_TOKEN is not set in .env (missing, blank, or still the placeholder)." "Yellow"
    Say "Re-run setup to generate one:  python scripts\setup.py" "Cyan"
    Say "If you just ran setup, open .env and confirm a JARVIS_TOKEN= line has a real value." "DarkGray"
    exit 1
}

# 4) Copy to clipboard WITHOUT echoing the value. Prefer Set-Clipboard; fall back to clip.exe.
$copied = $false
try {
    Set-Clipboard -Value $token
    $copied = $true
} catch {
    try {
        $token | clip.exe
        $copied = $true
    } catch {
        $copied = $false
    }
}

if (-not $copied) {
    Say "Could not access the Windows clipboard on this machine." "Red"
    Say "As a fallback, open .env yourself and copy the JARVIS_TOKEN value." "Yellow"
    exit 1
}

# 5) Safe confirmation only - masked hint, never the token itself.
$len = $token.Length
$last4 = if ($len -ge 4) { $token.Substring($len - 4) } else { ("*" * $len) }
Say "Copied JARVIS_TOKEN to clipboard." "Green"
Say "  (token: $len chars, ends with '$last4' - the value is NOT shown here)" "DarkGray"
Say "Paste it into the Jarvis app settings." "Cyan"
Say "Treat this token like a password - don't share it, screenshot it, or post it." "Yellow"
exit 0
