@echo off
REM ===================================================================
REM  Jarvis Voice Local - UPDATE
REM  Double-click this to get the latest version. It keeps your token,
REM  your settings, and your files - only the program is updated.
REM  Restart Jarvis afterward (close the Jarvis window, open it again).
REM ===================================================================
title Jarvis Voice Local - Update
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
