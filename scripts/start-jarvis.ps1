# Forwarding shim: the product was renamed Adam, and this launcher is now
# start-adam.ps1. Kept for one release so desktop shortcuts created by older
# installs (add-app-shortcut.ps1 pointed here) keep working after an update.
& (Join-Path $PSScriptRoot "start-adam.ps1") @args
