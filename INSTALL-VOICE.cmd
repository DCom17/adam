@echo off
REM ===================================================================
REM  Adam - INSTALL THE REAL ADAM (optional)
REM  Double-click to upgrade from the browser's robotic voice to the
REM  high-quality Adam voice. One-time ~340 MB download. Restart
REM  Adam afterward to hear it.
REM ===================================================================
title Adam - Install Voice
if not exist "%~dp0scripts\install-voice.ps1" (
    echo.
    echo  It looks like you're running this from INSIDE the ZIP file.
    echo  Right-click the ZIP you downloaded, choose "Extract All...",
    echo  then open the NEW folder and double-click INSTALL-VOICE in there.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-voice.ps1"
