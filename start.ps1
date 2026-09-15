# Lloyds CTF - Auto-install and start script for Windows / PowerShell
#
# Usage:
#   .\start.ps1              start the platform
#   .\start.ps1 -Production  LAN event build (workers, logging, core services)
#   .\start.ps1 -Down        stop all services
#   .\start.ps1 -Restart     re-detect LAN IP and restart affected services
#   .\start.ps1 -Logs        tail logs after start
#   .\start.ps1 -Status      show service health

param(
    [switch]$Down,
    [switch]$Restart,
    [switch]$Logs,
    [switch]$Production,
    [switch]$Status
)

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

function Write-Banner {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " Lloyds CTF - Auto Setup" -ForegroundColor White
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
}
function Write-Step($Message) { Write-Host "> $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "OK $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "WARN $Message" -ForegroundColor Yellow }
function Write-Fail($Message) { Write-Host "ERROR $Message" -ForegroundColor Red; exit 1 }

if ($Down) {
    Write-Step "Stopping all CTF services..."
    docker compose down
    Write-Ok "All services stopped."
    exit 0
}

if ($Status) {
    docker compose ps
    try {
        $code = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri "http://127.0.0.1/plugins/ctfd-target/health").StatusCode
        Write-Host "Health endpoint: $code"
    } catch {
        Write-Host "Health endpoint: not reachable yet"
    }
    exit 0
}

Write-Banner
Write-Step "Checking Docker..."

$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExe) {
    Write-Warn "Docker Desktop not found. Starting download..."

    $installerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
    $installerPath = "$env:TEMP\DockerDesktopInstaller.exe"

    Write-Step "Downloading Docker Desktop..."
    try {
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($installerUrl, $installerPath)
        Write-Ok "Downloaded to $installerPath"
    } catch {
        Write-Fail "Download failed: $_. Download manually: $installerUrl"
    }

    Write-Step "Installing Docker Desktop..."
    $proc = Start-Process -FilePath $installerPath `
        -ArgumentList "install", "--quiet", "--accept-license" `
        -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Fail "Docker installer exited with code $($proc.ExitCode). Try installing manually."
    }
    Write-Ok "Docker Desktop installed."
    Write-Warn "Restart your machine, then re-run this script."
    Write-Host ""
    Write-Host "Press any key to exit..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 0
}

$dockerRunning = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerRunning = $true }
} catch {}

if (-not $dockerRunning) {
    Write-Warn "Docker Desktop is not running. Attempting to start..."
    $dockerDesktopPath = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $dockerDesktopPath) {
        Write-Fail "Docker Desktop executable not found. Open Docker Desktop manually, then re-run this script."
    }

    Start-Process $dockerDesktopPath
    Write-Step "Waiting for Docker to start..."
    $wait = 0
    do {
        Start-Sleep -Seconds 3
        $wait += 3
        $running = $false
        try {
            $null = docker info 2>&1
            if ($LASTEXITCODE -eq 0) { $running = $true }
        } catch {}
    } until ($running -or $wait -ge 60)

    if (-not $running) {
        Write-Fail "Docker Desktop did not start in time. Open it manually, wait until it is running, then re-run this script."
    }
}

if (-not (docker compose version 2>$null)) {
    Write-Fail "docker compose was not found. Update Docker Desktop or install the Compose plugin."
}

$dockerVer = (docker --version) -replace 'Docker version ','' -replace ',.*',''
Write-Ok "Docker $dockerVer is running."

Write-Step "Preflight checks..."
try {
    $drive = (Get-Item -LiteralPath $PSScriptRoot).PSDrive
    if ($drive -and $drive.Free -lt 2GB) {
        Write-Fail "Less than 2 GB free on $($drive.Name):. Free disk space before starting."
    } elseif ($Production -and $drive -and $drive.Free -lt 5GB) {
        Write-Fail "Less than 5 GB free. A live event needs more disk for player instances."
    } elseif ($drive -and $drive.Free -lt 15GB) {
        Write-Warn ("Disk free: {0:N1} GB. 15 GB+ is safer for a full event." -f ($drive.Free / 1GB))
    } else {
        Write-Ok ("Disk free: {0:N1} GB" -f ($drive.Free / 1GB))
    }
} catch {
    Write-Warn "Could not check disk space."
}
if (Test-Path ".env") {
    $envText = Get-Content ".env" -Raw -ErrorAction SilentlyContinue
    if ($envText -match "(?m)^ADMIN_PASSWORD=ChangeMe2024!") {
        Write-Warn "ADMIN_PASSWORD is still the example value. Change it in .env before a real event."
    }
}

Write-Step "Detecting LAN IP..."
$HostIP = $null

try {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric |
        Select-Object -First 1
    if ($route) {
        $HostIP = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex `
            -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "127.*" } |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
} catch {}

if (-not $HostIP) {
    $candidateIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
    $HostIP = $candidateIps |
        Where-Object {
            ($_.IPAddress -notlike "127.*") -and
            ($_.IPAddress -notlike "169.*") -and
            ($_.IPAddress -notlike "172.1[6-9].*") -and
            ($_.IPAddress -notlike "172.2*.*") -and
            ($_.IPAddress -notlike "172.3*.*") -and
            ($_.PrefixOrigin -ne "WellKnown")
        } |
        Sort-Object InterfaceMetric |
        Select-Object -First 1 -ExpandProperty IPAddress
}

if (-not $HostIP) {
    $HostIP = "127.0.0.1"
    Write-Warn "Could not detect LAN IP. Targets will only be accessible on this machine."
} else {
    Write-Ok "LAN IP: $HostIP"
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Ok "Created .env from .env.example"
    } else {
        Write-Fail ".env file not found. Copy .env.example to .env first."
    }
}

function New-CtfSecret {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', '')
}

function Set-EnvValue {
    param(
        [string]$Text,
        [string]$Key,
        [string]$Value
    )
    if ($Text -match "(?m)^$([regex]::Escape($Key))=") {
        return ($Text -replace "(?m)^$([regex]::Escape($Key))=.*", "$Key=$Value")
    }
    return $Text.TrimEnd() + "`n$Key=$Value`n"
}

function Ensure-EnvValue {
    param(
        [string]$Text,
        [string]$Key,
        [string]$Value
    )
    if ($Text -match "(?m)^$([regex]::Escape($Key))=") {
        return $Text
    }
    return $Text.TrimEnd() + "`n$Key=$Value`n"
}

function Ensure-EnvSecret {
    param(
        [string]$Text,
        [string]$Key,
        [string[]]$WeakValues
    )
    $pattern = "(?m)^$([regex]::Escape($Key))=(.*)$"
    if ($Text -match $pattern) {
        $cur = $Matches[1].Trim()
        if (($WeakValues -contains $cur) -or [string]::IsNullOrWhiteSpace($cur)) {
            Write-Ok "Generated $Key"
            return ($Text -replace $pattern, "$Key=$(New-CtfSecret)")
        }
        return $Text
    }
    Write-Ok "Generated $Key"
    return $Text.TrimEnd() + "`n$Key=$(New-CtfSecret)`n"
}

$content = Get-Content ".env" -Raw -Encoding UTF8
$content = Set-EnvValue $content "HOST_IP" $HostIP
$content = Set-EnvValue $content "CTF_HOSTNAME" "lloydsctf.lab"
$content = Ensure-EnvSecret $content "INSTANCE_MANAGER_SECRET" @("change_this_to_a_random_instance_manager_secret")
$content = Ensure-EnvValue $content "INSTANCE_MANAGER_URL" "http://instance-manager:8088"
$content = Ensure-EnvValue $content "CTFD_TARGET_ORCHESTRATOR" "local"
$content = Ensure-EnvValue $content "INSTANCE_MANAGER_DEPLOY_MODE" "allocate"
$content = Ensure-EnvValue $content "CTFD_TARGET_IMAGE_PLATFORM" "multi"
$content = Ensure-EnvValue $content "CTFD_TARGET_INSTANCE_DISK_MB" "256"

$composeArgs = @("compose")
if ($Production) {
    Write-Step "Production mode: core LAN stack"
    foreach ($pair in @(
        @{K="FLAG_SECRET"; Weak=@("change_this_to_a_random_secret")},
        @{K="CTFD_SECRET_KEY"; Weak=@("change_this_to_a_random_string")},
        @{K="JWT_SECRET"; Weak=@("change_this_to_a_random_string")}
    )) {
        if ($content -match "(?m)^$($pair.K)=(.*)$") {
            $cur = $Matches[1].Trim()
            if ($pair.Weak -contains $cur -or [string]::IsNullOrWhiteSpace($cur)) {
                $content = $content -replace "(?m)^$($pair.K)=.*", "$($pair.K)=$(New-CtfSecret)"
                Write-Ok "Generated $($pair.K)"
            }
        }
    }
    $content = Set-EnvValue $content "PRODUCTION" "1"
    $content = Set-EnvValue $content "WORKERS" "3"
    $content = Set-EnvValue $content "CTFD_TARGET_ORCHESTRATOR" "instance_manager"
    $content = Set-EnvValue $content "INSTANCE_MANAGER_DEPLOY_MODE" "worker"
    $composeArgs += @("-f", "docker-compose.yml", "-f", "docker-compose.prod.yml")
}
[System.IO.File]::WriteAllText(
    (Resolve-Path ".env").Path,
    $content,
    (New-Object System.Text.UTF8Encoding $false)
)

$env:HOST_IP = $HostIP
$env:CTF_HOSTNAME = "lloydsctf.lab"
Write-Ok ".env updated: HOST_IP=$HostIP, CTF_HOSTNAME=lloydsctf.lab"

Write-Step "Validating Docker Compose configuration..."
docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose config validation failed." }
Write-Ok "Docker Compose configuration is valid."

try {
    $hostsPath = "$env:WINDIR\System32\drivers\etc\hosts"
    $hostName = "lloydsctf.lab"
    $hostsContent = Get-Content $hostsPath -Raw -ErrorAction Stop
    $pattern = "(?m)^\s*\S+\s+$([regex]::Escape($hostName))(\s.*)?$"
    if ($hostsContent -match $pattern) {
        $hostsContent = [regex]::Replace($hostsContent, $pattern, "127.0.0.1 $hostName")
    } else {
        $hostsContent = $hostsContent.TrimEnd() + "`r`n127.0.0.1 $hostName`r`n"
    }
    Set-Content -Path $hostsPath -Value $hostsContent -Encoding ASCII -ErrorAction Stop
    ipconfig /flushdns | Out-Null
    Write-Ok "Windows hosts entry set: $hostName -> 127.0.0.1"
} catch {
    Write-Warn "Could not update Windows hosts file. Run this script as Administrator or open http://$HostIP instead."
}

if (-not $Restart) {
    Write-Step "Building Docker images. First run can take several minutes..."
    docker @composeArgs build
    if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose build failed." }
    Write-Ok "Images built."
}

if ($Restart) {
    Write-Step "Restarting dnsmasq and CTFd with new IP ($HostIP)..."
    docker @composeArgs up -d --force-recreate dnsmasq ctfd
} else {
    if ($Production) {
        Write-Step "Starting production core services..."
        docker @composeArgs up -d proxy dnsmasq ctfd db cache instance-manager local-worker-agent
        docker @composeArgs stop vbank-ctf vbank-analytics 2>$null
    } else {
        Write-Step "Starting all services..."
        docker @composeArgs up -d
    }
}
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose up failed." }

Write-Step "Waiting for CTFd to become healthy..."
$tries = 0
do {
    Start-Sleep -Seconds 3
    $tries++
    Write-Host "." -NoNewline
    if ($tries -gt 50) { Write-Fail "`nCTFd failed to start. Run: docker compose logs ctfd" }
    $ctfdPsJson = docker @composeArgs ps ctfd --format json
    $ctfdPs = $ctfdPsJson | ConvertFrom-Json
    if ($ctfdPs -is [array]) { $ctfdStatus = ($ctfdPs | Select-Object -First 1).Status } else { $ctfdStatus = $ctfdPs.Status }
} until ($ctfdStatus -like "*healthy*")
Write-Host ""

if ($Production) {
    Write-Step "Waiting for local worker agent to register..."
    $tries = 0
    do {
        Start-Sleep -Seconds 2
        $tries++
        Write-Host "." -NoNewline
        if ($tries -gt 45) { Write-Fail "`nLocal worker agent failed to start. Run: docker compose logs local-worker-agent" }
        $workerPsJson = docker @composeArgs ps local-worker-agent --format json
        $workerPs = $workerPsJson | ConvertFrom-Json
        if ($workerPs -is [array]) { $workerStatus = ($workerPs | Select-Object -First 1).Status } else { $workerStatus = $workerPs.Status }
    } until ($workerStatus -like "*healthy*")
    Write-Host ""
    Write-Ok "Local worker agent is healthy and auto-registered."
}

Write-Step "Running CTFd setup job..."
docker @composeArgs up --no-deps --force-recreate setup
if ($LASTEXITCODE -ne 0) {
    docker @composeArgs logs --tail 120 setup
    Write-Fail "CTFd setup job failed."
}
Write-Ok "CTFd setup job completed."

Write-Host ""
Write-Host "  Lloyds CTF is live!" -ForegroundColor Green
Write-Host ""
Write-Host "  Access URLs:" -ForegroundColor Cyan
Write-Host "    http://lloydsctf.lab"
Write-Host "    http://$HostIP"
Write-Host "    https://lloydsctf.lab  (optional, requires CA trust)"
Write-Host ""
Write-Host "  DNS server: $($HostIP):53" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Player setup:" -ForegroundColor Yellow
Write-Host "    1. Connect to the same LAN as this machine"
Write-Host "    2. Set DNS server to $HostIP, or set this in router DHCP"
Write-Host "    3. Open http://lloydsctf.lab"
Write-Host ""
if ($Production) {
    Write-Host "  Production:" -ForegroundColor Cyan
    Write-Host "    Admin: http://$HostIP/plugins/ctfd-target/admin/ops"
    Write-Host "    Instance Manager: enabled"
    Write-Host "    Local worker agent: auto-started"
    Write-Host "    CTFd workers: 3  |  demo vBank is not kept running"
    Write-Host ""
}

if ($Logs) {
    docker compose logs -f
}
