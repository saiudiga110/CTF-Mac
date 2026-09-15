param(
    [string]$ManagerUrl = $env:INSTANCE_MANAGER_URL,
    [string]$Secret = $env:INSTANCE_MANAGER_SECRET,
    [switch]$Serve
)

if (-not $ManagerUrl) {
    Write-Error "INSTANCE_MANAGER_URL is required, for example http://192.168.1.10:8088"
    exit 1
}

if (-not $Secret) {
    Write-Error "INSTANCE_MANAGER_SECRET is required and must match the control-plane manager"
    exit 1
}

$env:INSTANCE_MANAGER_URL = $ManagerUrl
$env:INSTANCE_MANAGER_SECRET = $Secret
$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"
if ($Serve) {
    $env:WORKER_SERVE = "1"
}

python -m infra.instance_manager.worker_agent
