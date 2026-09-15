param(
    [string]$CtfHostname = "lloydsctf.lab",
    [int]$TargetPortRangeStart = 20000,
    [int]$TargetPortRangeEnd = 20999,
    [int]$KaliPortRangeStart = 21000,
    [int]$KaliPortRangeEnd = 21999
)

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

function Write-Step($Message) { Write-Host "> $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "OK $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "WARN $Message" -ForegroundColor Yellow }
function Write-Fail($Message) { Write-Host "ERROR $Message" -ForegroundColor Red; exit 1 }

function Set-EnvValue($Key, $Value) {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
    }
    $content = Get-Content ".env" -Raw -Encoding UTF8
    if ($content -match "(?m)^$([regex]::Escape($Key))=") {
        $content = $content -replace "(?m)^$([regex]::Escape($Key))=.*", "$Key=$Value"
    } else {
        $content = $content.TrimEnd() + "`n$Key=$Value`n"
    }
    [System.IO.File]::WriteAllText((Resolve-Path ".env").Path, $content, (New-Object System.Text.UTF8Encoding $false))
}

Write-Step "Checking Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Fail "Docker Desktop is required. Install Docker Desktop, start it, then rerun .\start-production.ps1"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Docker Desktop is installed but not running."
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Docker Compose v2 is required."
}
Write-Ok "Docker is running."

Write-Step "Detecting LAN IP..."
$HostIP = $null
try {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric |
        Select-Object -First 1
    if ($route) {
        $HostIP = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "127.*" } |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
} catch {}
if (-not $HostIP) {
    Write-Fail "Could not detect the LAN IP. Check network connectivity."
}
Write-Ok "LAN IP: $HostIP"

Set-EnvValue "HOST_IP" $HostIP
Set-EnvValue "CTF_HOSTNAME" $CtfHostname
Set-EnvValue "TARGET_PORT_RANGE_START" $TargetPortRangeStart
Set-EnvValue "TARGET_PORT_RANGE_END" $TargetPortRangeEnd
Set-EnvValue "KALI_PORT_RANGE_START" $KaliPortRangeStart
Set-EnvValue "KALI_PORT_RANGE_END" $KaliPortRangeEnd

$env:HOST_IP = $HostIP
$env:CTF_HOSTNAME = $CtfHostname
$env:TARGET_PORT_RANGE_START = "$TargetPortRangeStart"
$env:TARGET_PORT_RANGE_END = "$TargetPortRangeEnd"
$env:KALI_PORT_RANGE_START = "$KaliPortRangeStart"
$env:KALI_PORT_RANGE_END = "$KaliPortRangeEnd"

Write-Step "Opening Windows Firewall ports when permitted..."
try {
    $ruleName = "Lloyds CTF LAN"
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort "80,443,$TargetPortRangeStart-$TargetPortRangeEnd,$KaliPortRangeStart-$KaliPortRangeEnd" | Out-Null
    New-NetFirewallRule -DisplayName "$ruleName DNS UDP" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 53 | Out-Null
    New-NetFirewallRule -DisplayName "$ruleName DNS TCP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 53 | Out-Null
    Write-Ok "Firewall rules configured."
} catch {
    Write-Warn "Could not configure firewall automatically. Run as Administrator or open TCP 80,443,$TargetPortRangeStart-$TargetPortRangeEnd,$KaliPortRangeStart-$KaliPortRangeEnd and UDP/TCP 53 manually."
}

Write-Step "Starting Lloyds CTF production node..."
docker compose build
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose build failed." }
docker compose up -d
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose up failed." }

Write-Step "Waiting for CTFd health..."
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    $status = docker compose ps ctfd --format "{{.Status}}" 2>$null
    if ($status -like "*healthy*") { $ok = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ok) { Write-Fail "CTFd did not become healthy. Run docker compose logs ctfd." }

Write-Host ""
Write-Host "Lloyds CTF node is ready." -ForegroundColor Green
Write-Host "Open from LAN: http://$HostIP"
Write-Host "DNS name:      http://$CtfHostname -> $HostIP"
Write-Host "vBank ports:   TCP $TargetPortRangeStart-$TargetPortRangeEnd"
Write-Host "Pwn ports:     TCP $KaliPortRangeStart-$KaliPortRangeEnd"
