# CTF Host Agent - Manages 10.10.100.X IP aliases and port proxies on Windows.
# Installed as a Windows scheduled task via install.ps1 — runs automatically at boot.
# Docker plugin calls http://127.0.0.1:7777/ to add/remove team IPs.

param([int]$AgentPort = 7777)

function Write-Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path "$PSScriptRoot\agent.log" -Value "[$ts] $msg" -ErrorAction SilentlyContinue
}

function Add-TeamIP([string]$ip, [int]$proxyPort) {
    # Clean up any stale entry first
    netsh interface portproxy delete v4tov4 listenaddress=$ip listenport=80 2>$null | Out-Null
    netsh interface ip delete address "Loopback Pseudo-Interface 1" $ip 2>$null | Out-Null
    # Add IP alias to Windows loopback adapter
    $r1 = netsh interface ip add address "Loopback Pseudo-Interface 1" $ip 255.255.255.255 2>&1
    # Create port proxy: ip:80 → localhost:proxyPort (Docker-bound port)
    $r2 = netsh interface portproxy add v4tov4 listenaddress=$ip listenport=80 connectaddress=127.0.0.1 connectport=$proxyPort 2>&1
    Write-Log "ADD $ip`:80 -> 127.0.0.1:$proxyPort | $r1 | $r2"
}

function Remove-TeamIP([string]$ip) {
    netsh interface portproxy delete v4tov4 listenaddress=$ip listenport=80 2>$null | Out-Null
    netsh interface ip delete address "Loopback Pseudo-Interface 1" $ip 2>$null | Out-Null
    Write-Log "REMOVE $ip"
}

# Re-apply all portproxy entries that survive a reboot (loopback IPs are lost on reboot)
function Restore-AllProxies {
    $entries = netsh interface portproxy show v4tov4 2>$null | Select-String "10\.10\."
    foreach ($line in $entries) {
        if ($line -match "(10\.10\.\d+\.\d+)\s+80\s+127\.0\.0\.1\s+(\d+)") {
            $ip = $matches[1]; $port = $matches[2]
            netsh interface ip add address "Loopback Pseudo-Interface 1" $ip 255.255.255.255 2>$null | Out-Null
            Write-Log "RESTORE $ip`:80 -> 127.0.0.1:$port"
        }
    }
}

Restore-AllProxies
Write-Log "Agent started on port $AgentPort"

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$AgentPort/")
try { $listener.Start() } catch {
    Write-Log "Failed to start listener: $_"
    exit 1
}

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $req = $ctx.Request
    $res = $ctx.Response
    $out = '{"success":false}'
    try {
        $body = [System.IO.StreamReader]::new($req.InputStream, [System.Text.Encoding]::UTF8).ReadToEnd()
        $d = $body | ConvertFrom-Json
        switch ($d.action) {
            "add"    { if ($d.ip -and $d.port) { Add-TeamIP $d.ip ([int]$d.port); $out = '{"success":true}' } }
            "remove" { if ($d.ip) { Remove-TeamIP $d.ip; $out = '{"success":true}' } }
            "ping"   { $out = '{"success":true,"status":"ok"}' }
        }
    } catch { Write-Log "Error: $_" }
    $b = [System.Text.Encoding]::UTF8.GetBytes($out)
    $res.ContentType = "application/json"; $res.ContentLength64 = $b.Length
    $res.OutputStream.Write($b, 0, $b.Length); $res.OutputStream.Close()
}
