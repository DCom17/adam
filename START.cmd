@echo off
REM ===================================================================
REM  Jarvis Voice Local - OPEN JARVIS
REM  Double-click this AFTER you've run SETUP once.
REM  It starts Jarvis and opens it in your browser, already signed in.
REM ===================================================================
REM  This launcher window is disposable; the SERVER runs in its own window titled
REM  "Jarvis Voice Local". Keep this one distinct so users don't confuse the two.
title Jarvis - Launcher (safe to close)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-jarvis.ps1"
echo.
echo  (Jarvis runs in its own window. You can close THIS one.)
pause >nul
