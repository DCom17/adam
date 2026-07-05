@echo off
REM ===================================================================
REM  Adam - UPDATE
REM  Double-click this to get the latest version. It keeps your token,
REM  your settings, and your files - only the program is updated.
REM  Restart Adam afterward (close the Adam window, open it again).
REM ===================================================================
title Adam - Update
REM The ZIP-trap guard runs ONLY on the first invocation (no %1): the TEMP
REM copy below re-runs this file from %TEMP%, where scripts\ never exists.
if "%~1"=="" if not exist "%~dp0scripts\update.ps1" (
    echo.
    echo  It looks like you're running this from INSIDE the ZIP file.
    echo  Right-click the ZIP you downloaded, choose "Extract All...",
    echo  then open the NEW folder and double-click UPDATE in there.
    echo.
    pause
    exit /b 1
)
if "%~1"=="" (
    REM The update replaces UPDATE.cmd itself while cmd.exe is still reading it
    REM by byte offset - so hand off to a TEMP copy and never touch this file
    REM again. (No "call": execution transfers there and does not return here.)
    copy /Y "%~f0" "%TEMP%\adam-update-run.cmd" >nul
    "%TEMP%\adam-update-run.cmd" "%~dp0"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~1scripts\update.ps1"
