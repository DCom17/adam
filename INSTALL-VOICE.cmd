@echo off
REM ===================================================================
REM  Jarvis Voice Local - INSTALL THE REAL JARVIS VOICE (optional)
REM  Double-click to upgrade from the browser's robotic voice to the
REM  high-quality Jarvis voice. One-time ~340 MB download. Restart
REM  Jarvis afterward to hear it.
REM ===================================================================
title Jarvis Voice Local - Install Voice
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-voice.ps1"
