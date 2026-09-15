@echo off
title Lloyds CTF — Auto Setup
echo.
echo  Lloyds CTF Auto-Setup
echo  ─────────────────────
echo  Detecting platform and starting services...
echo.

:: Run the PowerShell installer with no execution policy restrictions
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*

echo.
pause
