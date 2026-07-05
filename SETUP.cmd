@echo off
REM ===================================================================
REM  Adam - ONE-CLICK SETUP
REM  Just double-click this file. It runs the guided setup wizard.
REM ===================================================================
title Adam - Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\wizard.ps1"
echo.
echo  (You can close this window.)
pause >nul
