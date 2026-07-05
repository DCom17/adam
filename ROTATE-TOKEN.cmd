@echo off
REM ===================================================================
REM  Adam - ROTATE TOKEN
REM  Double-click this if your access token may have been exposed
REM  (a photographed QR, a lost device). It generates a fresh token;
REM  you re-pair your devices afterward. Your files are not touched.
REM ===================================================================
title Adam - Rotate token
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rotate-token.ps1"
