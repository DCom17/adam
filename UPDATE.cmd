@echo off
REM ===================================================================
REM  Adam - UPDATE
REM  Double-click this to get the latest version. It keeps your token,
REM  your settings, and your files - only the program is updated.
REM  Restart Adam afterward (close the Adam window, open it again).
REM ===================================================================
title Adam - Update
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
