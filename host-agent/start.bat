@echo off
:: Run this ONCE as Administrator before starting the CTF.
:: It stays running in the background — keep this window open.
:: Each target launch will automatically get a real 10.10.100.X IP in Chrome.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent.ps1"
pause
