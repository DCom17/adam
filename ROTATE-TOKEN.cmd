@echo off
REM ===================================================================
REM  Adam - ROTATE TOKEN
REM  Double-click this if your access token may have been exposed
REM  (a photographed QR, a lost device). It generates a fresh token;
REM  you re-pair your devices afterward. Your files are not touched.
REM ===================================================================
title Adam - Rotate token
if not exist "%~dp0scripts\rotate-token.ps1" (
    echo.
    echo  It looks like you're running this from INSIDE the ZIP file.
    echo  Right-click the ZIP you downloaded, choose "Extract All...",
    echo  then open the NEW folder and double-click ROTATE-TOKEN in there.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rotate-token.ps1"
