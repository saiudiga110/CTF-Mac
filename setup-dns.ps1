# ============================================================
#  setup-dns.ps1 — Run ONCE as Administrator on the CTF host
#  Sets up lloydsctf.lab DNS so the host and all LAN clients
#  can reach the CTF platform by name.
# ============================================================
#Requires -RunAsAdministrator

$LAN_IP   = "10.34.203.115"   # Auto-detected WiFi IP — update if it changes
$HOSTNAME = "lloydsctf.lab"

Write-Host "`n=== Lloyds CTF — DNS Setup ===" -ForegroundColor Cyan

# ── 1. Windows Hosts file (so this machine resolves it too) ──────────────────
Write-Host "`n[1] Updating hosts file..." -ForegroundColor Yellow
$hostsFile = "C:\Windows\System32\drivers\etc\hosts"
$entriesToAdd = @(
    "$LAN_IP lloydsctf.lab",
    "$LAN_IP ctfd.lab"
)
$current = Get-Content $hostsFile -Raw
foreach ($entry in $entriesToAdd) {
    $hostPart = ($entry -split " ")[1]
    # Remove any old stale entry for this hostname first
    $current = $current -replace "(?m)^[^\n#]*\s+$([regex]::Escape($hostPart))\s*$", ""
    $current = $current.TrimEnd() + "`n$entry`n"
}
Set-Content $hostsFile $current -Encoding ASCII
Write-Host "  OK — hosts file updated." -ForegroundColor Green

# ── 2. Flush DNS cache ────────────────────────────────────────────────────────
Write-Host "`n[2] Flushing DNS cache..." -ForegroundColor Yellow
ipconfig /flushdns | Out-Null
Write-Host "  OK." -ForegroundColor Green

# ── 3. Open Windows Firewall for port 53 (so LAN clients can reach dnsmasq) ──
Write-Host "`n[3] Opening firewall port 53 (TCP + UDP)..." -ForegroundColor Yellow
@(
    @{ Name="CTF dnsmasq DNS UDP"; Protocol="UDP" },
    @{ Name="CTF dnsmasq DNS TCP"; Protocol="TCP" }
) | ForEach-Object {
    $existing = Get-NetFirewallRule -DisplayName $_.Name -ErrorAction SilentlyContinue
    if ($existing) { Remove-NetFirewallRule -DisplayName $_.Name }
    New-NetFirewallRule -DisplayName $_.Name `
        -Direction Inbound `
        -Protocol $_.Protocol `
        -LocalPort 53 `
        -Action Allow `
        -Profile Any | Out-Null
}
Write-Host "  OK — firewall rules created." -ForegroundColor Green

# ── 4. Open firewall for HTTP/HTTPS (ports 80 + 443) ─────────────────────────
Write-Host "`n[4] Opening firewall ports 80 and 443..." -ForegroundColor Yellow
@(
    @{ Name="CTF HTTP  (port 80)";  Port=80  },
    @{ Name="CTF HTTPS (port 443)"; Port=443 }
) | ForEach-Object {
    $existing = Get-NetFirewallRule -DisplayName $_.Name -ErrorAction SilentlyContinue
    if ($existing) { Remove-NetFirewallRule -DisplayName $_.Name }
    New-NetFirewallRule -DisplayName $_.Name `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $_.Port `
        -Action Allow `
        -Profile Any | Out-Null
}
Write-Host "  OK." -ForegroundColor Green

# ── 5. Open firewall for vBank/Kali port ranges ───────────────────────────────
Write-Host "`n[5] Opening firewall for target port ranges 20000-21999..." -ForegroundColor Yellow
$existing = Get-NetFirewallRule -DisplayName "CTF Target Ports" -ErrorAction SilentlyContinue
if ($existing) { Remove-NetFirewallRule -DisplayName "CTF Target Ports" }
New-NetFirewallRule -DisplayName "CTF Target Ports" `
    -Direction Inbound -Protocol TCP `
    -LocalPort "20000-21999" `
    -Action Allow -Profile Any | Out-Null
Write-Host "  OK." -ForegroundColor Green

# ── 6. Verify DNS is responding ───────────────────────────────────────────────
Write-Host "`n[6] Verifying DNS..." -ForegroundColor Yellow
try {
    $result = Resolve-DnsName -Name "lloydsctf.lab" -Server $LAN_IP -Type A -ErrorAction Stop
    Write-Host "  OK — lloydsctf.lab → $($result.IPAddress)" -ForegroundColor Green
} catch {
    Write-Host "  WARN — DNS query failed (dnsmasq may still be starting)" -ForegroundColor Red
}

Write-Host "`n=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "  CTF URL  : http://lloydsctf.lab" -ForegroundColor White
Write-Host "  LAN IP   : $LAN_IP" -ForegroundColor White
Write-Host "`n  Tell players to set their DNS to: $LAN_IP" -ForegroundColor Yellow
Write-Host "  Or give them 'set-player-dns.ps1' to run on their laptops.`n"
