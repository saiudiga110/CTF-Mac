# ============================================================
#  set-player-dns.ps1 — Run as Administrator on PLAYER laptops
#  Points their DNS to the CTF server so lloydsctf.lab resolves.
#  Works on Windows 10/11.
# ============================================================
#Requires -RunAsAdministrator

$CTF_SERVER_IP = "10.34.203.115"   # IP of the CTF host machine

Write-Host "`n=== Lloyds CTF — Player DNS Setup ===" -ForegroundColor Cyan
Write-Host "  This will set your DNS to the CTF server ($CTF_SERVER_IP)" -ForegroundColor Gray
Write-Host "  so you can reach http://lloydsctf.lab in your browser.`n"

# Find the active network adapter (connected, not loopback/virtual)
$adapters = Get-NetAdapter | Where-Object {
    $_.Status -eq "Up" -and
    $_.InterfaceDescription -notmatch "Loopback|Virtual|Hyper-V|VMware|Bluetooth"
} | Sort-Object -Property LinkSpeed -Descending

if (-not $adapters) {
    Write-Host "ERROR: No active network adapter found. Make sure you're connected to the CTF LAN." -ForegroundColor Red
    pause; exit 1
}

$adapter = $adapters[0]
Write-Host "  Detected adapter: $($adapter.Name) ($($adapter.InterfaceDescription))" -ForegroundColor White

# Set DNS: CTF server first, Google as fallback
Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses ($CTF_SERVER_IP, "8.8.8.8")
ipconfig /flushdns | Out-Null

# Verify
Start-Sleep -Seconds 1
try {
    $result = Resolve-DnsName -Name "lloydsctf.lab" -Type A -ErrorAction Stop
    Write-Host "`n  SUCCESS! lloydsctf.lab → $($result.IPAddress)" -ForegroundColor Green
    Write-Host "  Open your browser and go to: http://lloydsctf.lab`n" -ForegroundColor Cyan
} catch {
    Write-Host "`n  DNS set, but lloydsctf.lab didn't resolve yet." -ForegroundColor Yellow
    Write-Host "  Try: http://lloydsctf.lab — if it doesn't work, check you're on the right WiFi.`n"
}

pause
