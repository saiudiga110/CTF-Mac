# CTF Host Agent Installer
# Run ONCE as Administrator. After this, everything is automatic on every boot.
# The agent starts silently in the background — no window, no manual steps.

param()
$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "ERROR: Right-click install.bat and choose 'Run as administrator'." -ForegroundColor Red
    pause; exit 1
}

$agentPath = Join-Path $PSScriptRoot "agent.ps1"
$taskName  = "CTF-Host-Agent"

# Stop existing instance if running
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

# Register as a scheduled task that runs as SYSTEM at startup (hidden, no window)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$agentPath`""

$triggers = @(
    $(New-ScheduledTaskTrigger -AtStartup),
    $(New-ScheduledTaskTrigger -AtLogOn)
)

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null

# Start immediately (don't wait for reboot)
Start-ScheduledTask -TaskName $taskName

Start-Sleep -Seconds 2

# Verify it's running
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " CTF Host Agent installed successfully!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host " Status : $state" -ForegroundColor Cyan
Write-Host " Task   : $taskName" -ForegroundColor Cyan
Write-Host " Agent  : $agentPath" -ForegroundColor Cyan
Write-Host ""
Write-Host " The agent now runs automatically on every Windows boot." -ForegroundColor White
Write-Host " Target IPs (10.10.100.X) will work in Chrome automatically." -ForegroundColor White
Write-Host ""
pause
