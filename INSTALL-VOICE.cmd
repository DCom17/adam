@echo off
REM ===================================================================
REM  Adam - INSTALL THE REAL ADAM (optional)
REM  Double-click to upgrade from the browser's robotic voice to the
REM  high-quality Adam voice. One-time ~340 MB download. Restart
REM  Adam afterward to hear it.
REM ===================================================================
title Adam - Install Voice
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-voice.ps1"
