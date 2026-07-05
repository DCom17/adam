@echo off
REM ===================================================================
REM  Adam - ONE-CLICK SETUP
REM  Just double-click this file. It runs the guided setup wizard.
REM ===================================================================
title Adam - Setup
if not exist "%~dp0scripts\wizard.ps1" (
    echo.
    echo  It looks like you're running this from INSIDE the ZIP file.
    echo  Windows only unpacked this one file, so setup can't start yet.
    echo.
    echo  Close this window, then right-click the ZIP you downloaded and
    echo  choose "Extract All...". Open the NEW folder it creates and
    echo  double-click SETUP in there.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\wizard.ps1"
echo.
echo  (You can close this window.)
pause >nul
