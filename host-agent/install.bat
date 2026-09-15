@echo off
:: Right-click this file and choose "Run as administrator"
:: One-time setup — after this, target IPs work automatically forever.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
