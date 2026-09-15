#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CTF_HOSTNAME="${CTF_HOSTNAME:-lloydsctf.lab}"
TARGET_PORT_RANGE_START="${TARGET_PORT_RANGE_START:-20000}"
TARGET_PORT_RANGE_END="${TARGET_PORT_RANGE_END:-20999}"
KALI_PORT_RANGE_START="${KALI_PORT_RANGE_START:-21000}"
KALI_PORT_RANGE_END="${KALI_PORT_RANGE_END:-21999}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

detect_lan_ip() {
    local ip="" iface=""
    if [[ "$(uname)" == "Darwin" ]]; then
        iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
        [[ -n "$iface" ]] && ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
        [[ -n "$ip" ]] && printf '%s\n' "$ip" && return
    fi
    if command -v ip >/dev/null 2>&1; then
        ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')"
        [[ -n "$ip" ]] && printf '%s\n' "$ip" && return
    fi
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -n "$ip" ]] && printf '%s\n' "$ip" && return
    printf '127.0.0.1\n'
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        if [[ "$(uname)" == "Darwin" ]]; then
            die "Docker Desktop is required on macOS. Install it once, start it, then rerun ./start-production.sh"
        fi
        log "Docker not found; installing Docker Engine..."
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        sudo sh /tmp/get-docker.sh
        rm -f /tmp/get-docker.sh
    fi
    docker info >/dev/null 2>&1 || die "Docker is installed but not running."
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
}

set_env_value() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env; then
        sed -i.bak "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

[[ -f .env ]] || cp .env.example .env
HOST_IP="${HOST_IP:-$(detect_lan_ip)}"

require_docker

set_env_value HOST_IP "$HOST_IP"
set_env_value CTF_HOSTNAME "$CTF_HOSTNAME"
set_env_value TARGET_PORT_RANGE_START "$TARGET_PORT_RANGE_START"
set_env_value TARGET_PORT_RANGE_END "$TARGET_PORT_RANGE_END"
set_env_value KALI_PORT_RANGE_START "$KALI_PORT_RANGE_START"
set_env_value KALI_PORT_RANGE_END "$KALI_PORT_RANGE_END"

export HOST_IP CTF_HOSTNAME TARGET_PORT_RANGE_START TARGET_PORT_RANGE_END KALI_PORT_RANGE_START KALI_PORT_RANGE_END

log "Starting Lloyds CTF production node..."
docker compose build
docker compose up -d

log "Waiting for CTFd health..."
for _ in $(seq 1 60); do
    if docker compose ps ctfd --format '{{.Status}}' 2>/dev/null | grep -q healthy; then
        break
    fi
    sleep 3
done
docker compose ps

cat <<EOF

Lloyds CTF node is ready.

Open from LAN:
  http://${HOST_IP}
  http://${CTF_HOSTNAME}  (after DNS points ${CTF_HOSTNAME} to ${HOST_IP})

Open these inbound TCP/UDP ports on this Mac/Linux host:
  TCP 80, 443
  UDP/TCP 53 if this host provides DNS
  TCP ${TARGET_PORT_RANGE_START}-${TARGET_PORT_RANGE_END} for vBank targets
  TCP ${KALI_PORT_RANGE_START}-${KALI_PORT_RANGE_END} for Pwn Machines

For a 4-Mac cluster, run this on each backend Mac, then run the load balancer
on one front-door Mac with docker-compose.lb.yml and BACKEND_1..BACKEND_4.
EOF
