@echo off
REM ===================================================================
REM  Adam - OPEN Adam
REM  Double-click this AFTER you've run SETUP once.
REM  It starts Adam and opens it in your browser, already signed in.
REM ===================================================================
REM  This launcher window is disposable; the SERVER runs in its own window titled
REM  "Adam". Keep this one distinct so users don't confuse the two.
title Adam - Launcher (safe to close)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-adam.ps1"
echo.
echo  (Adam runs in its own window. You can close THIS one.)
pause >nul
