#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Lloyds CTF — Auto-install & start script (Mac / Linux / WSL2)
#
# What it does:
#   1. Detects the OS and installs Docker if missing
#   2. Auto-detects the LAN IP and writes HOST_IP to .env
#   3. Builds all Docker images (skips if already built)
#   4. Starts all services and waits for healthy status
#
# Usage:
#   ./start.sh              → install + start
#   ./start.sh --production → LAN event build (workers, logging, core services)
#   ./start.sh --down       → stop everything
#   ./start.sh --restart    → re-detect IP and restart affected services
#   ./start.sh --logs       → tail logs after start
#   ./start.sh --status     → show service health
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}▶${NC} $*"; }
ok()      { echo -e "${GREEN}✔${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
die()     { echo -e "${RED}✖${NC} $*" >&2; exit 1; }
banner()  { echo -e "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${BOLD} $* ${NC}"; echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }

ARG="${1:-}"
PRODUCTION=0
[[ "$ARG" == "--production" ]] && PRODUCTION=1

if [[ "$ARG" == "--status" ]]; then
    docker compose ps
    if curl -fsS --max-time 5 http://127.0.0.1/plugins/ctfd-target/health >/dev/null 2>&1; then
        echo "Health endpoint: ok"
    else
        echo "Health endpoint: not reachable yet"
    fi
    exit 0
fi

banner "Lloyds CTF — Auto Setup"

# ─────────────────────────────────────────────────────────────────────────────
# 1. DETECT OS
# ─────────────────────────────────────────────────────────────────────────────
OS="linux"
IS_WSL=false
IS_MAC=false

if [[ "$(uname)" == "Darwin" ]]; then
    OS="mac"
    IS_MAC=true
elif grep -qi microsoft /proc/version 2>/dev/null; then
    OS="wsl2"
    IS_WSL=true
fi
IS_ARM64=false
[[ "$(uname -m)" == "arm64" || "$(uname -m)" == "aarch64" ]] && IS_ARM64=true

info "Platform: ${BOLD}${OS}${NC} (arch: $(uname -m))"
if $IS_ARM64 && $IS_MAC; then
    info "Apple Silicon detected — Kali image will use Rosetta 2 (x86 emulation). Build may take longer."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. INSTALL DOCKER IF MISSING
# ─────────────────────────────────────────────────────────────────────────────
install_docker_linux() {
    warn "Docker not found. Installing Docker Engine..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    rm -f /tmp/get-docker.sh
    # Add current user to docker group so sudo isn't needed
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    # Start and enable service
    sudo systemctl enable docker --now 2>/dev/null || sudo service docker start 2>/dev/null || true
    ok "Docker installed. You may need to log out and back in for group changes."
}

if ! command -v docker &>/dev/null; then
    if $IS_MAC; then
        die "Docker Desktop not found.\nDownload from: https://www.docker.com/products/docker-desktop/\nInstall it, start it, then re-run this script."
    elif $IS_WSL; then
        die "Docker Desktop not found.\nInstall Docker Desktop for Windows from: https://www.docker.com/products/docker-desktop/\nEnable WSL2 integration in Docker Desktop → Settings → Resources → WSL Integration."
    else
        install_docker_linux
    fi
fi

# Check daemon is running
if ! docker info &>/dev/null; then
    if $IS_MAC || $IS_WSL; then
        die "Docker daemon is not running.\nOpen Docker Desktop and wait for it to start, then re-run this script."
    else
        warn "Docker daemon not running, attempting to start..."
        sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || \
            die "Could not start Docker. Run: sudo systemctl start docker"
        sleep 3
        docker info &>/dev/null || die "Docker daemon failed to start."
    fi
fi

# Resolve Compose v2. Prefer `docker compose`; fall back to standalone docker-compose v2.
# On macOS, repair the common Docker Desktop plugin symlink if needed.
COMPOSE_CMD=()
ensure_compose_v2() {
    if docker compose version &>/dev/null; then
        COMPOSE_CMD=(docker compose)
        return 0
    fi

    if command -v docker-compose &>/dev/null; then
        local dc_ver
        dc_ver="$(docker-compose version 2>/dev/null || true)"
        if echo "$dc_ver" | grep -Eqi 'version[[:space:]]*v?2\.'; then
            warn "docker compose plugin missing — using docker-compose v2 standalone."
            COMPOSE_CMD=(docker-compose)
            return 0
        fi
    fi

    if $IS_MAC; then
        local plugin_src="/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose"
        if [[ -x "$plugin_src" ]]; then
            info "Repairing Docker Compose v2 plugin link for macOS..."
            mkdir -p "${HOME}/.docker/cli-plugins"
            ln -sfn "$plugin_src" "${HOME}/.docker/cli-plugins/docker-compose"
            sudo mkdir -p /usr/local/lib/docker/cli-plugins 2>/dev/null || true
            sudo ln -sfn "$plugin_src" /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
            sudo ln -sfn "$plugin_src" /usr/local/bin/docker-compose 2>/dev/null || true
            hash -r 2>/dev/null || true
            if docker compose version &>/dev/null; then
                COMPOSE_CMD=(docker compose)
                ok "Docker Compose v2 restored."
                return 0
            fi
            if "$plugin_src" version &>/dev/null; then
                COMPOSE_CMD=("$plugin_src")
                ok "Using Docker Desktop Compose binary directly."
                return 0
            fi
        fi

        die "docker compose (v2) not available on this Mac.

Run these commands, then re-run ./start.sh:

  # 1) Make sure Docker Desktop is Running (whale icon)
  open -a Docker
  # 2) Link the Compose v2 plugin
  mkdir -p ~/.docker/cli-plugins
  ln -sfn /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose ~/.docker/cli-plugins/docker-compose
  # 3) Verify
  docker compose version

If step 2 says 'No such file', reinstall Docker Desktop:
  https://www.docker.com/products/docker-desktop/"
    fi

    if command -v docker-compose &>/dev/null; then
        die "Please upgrade to Docker Compose v2.
  Linux: sudo apt-get update && sudo apt-get install -y docker-compose-plugin
  Then verify: docker compose version"
    fi

    die "docker compose not found. Update Docker Desktop or install the Compose plugin.
  Verify with: docker compose version"
}

ensure_compose_v2
if [[ "$PRODUCTION" == "1" ]]; then
    COMPOSE_CMD+=(-f docker-compose.yml -f docker-compose.prod.yml)
    info "Production overlay enabled"
fi
DOCKER_VERSION=$(docker --version | sed -n 's/.*version \([0-9][0-9.]*\).*/\1/p' | head -1)
ok "Docker ${DOCKER_VERSION:-installed} + $("${COMPOSE_CMD[@]}" version --short 2>/dev/null || echo 'compose v2')"

info "Preflight checks..."
FREE_KB=$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')
if [[ -n "${FREE_KB:-}" ]]; then
    FREE_GB=$((FREE_KB / 1024 / 1024))
    if (( FREE_GB < 2 )); then
        die "Less than 2 GB free disk. Free space before starting."
    elif [[ "$PRODUCTION" == "1" ]] && (( FREE_GB < 5 )); then
        die "Less than 5 GB free. A live event needs more disk for player instances."
    elif (( FREE_GB < 15 )); then
        warn "Disk free: ${FREE_GB} GB. 15 GB+ is safer for a full event."
    else
        ok "Disk free: ${FREE_GB} GB"
    fi
fi
if [[ -f .env ]] && grep -q '^ADMIN_PASSWORD=ChangeMe2024!' .env; then
    warn "ADMIN_PASSWORD is still the example value. Change it in .env before a real event."
fi

# ── Stop shortcut (after compose is resolved) ─────────────────────────────────
if [[ "$ARG" == "--down" ]]; then
    info "Stopping all CTF services..."
    "${COMPOSE_CMD[@]}" down
    ok "All services stopped."
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. AUTO-DETECT LAN IP
# ─────────────────────────────────────────────────────────────────────────────
detect_lan_ip() {
    local ip=""

    # WSL2: get actual Windows LAN IP via PowerShell
    if $IS_WSL; then
        ip=$(powershell.exe -NoProfile -Command "
            \$r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                  Sort-Object RouteMetric | Select-Object -First 1;
            if (\$r) {
                (Get-NetIPAddress -InterfaceIndex \$r.InterfaceIndex -AddressFamily IPv4 \
                                  -ErrorAction SilentlyContinue |
                 Select-Object -First 1 -ExpandProperty IPAddress)
            }
        " 2>/dev/null | tr -d '\r\n ')
        [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && echo "$ip" && return
    fi

    # macOS
    if $IS_MAC; then
        # Method 1: follow the default route to find the active interface
        local iface
        iface=$(route -n get 1.1.1.1 2>/dev/null | awk '/interface:/{print $2}')
        if [[ -n "$iface" ]]; then
            ip=$(ipconfig getifaddr "$iface" 2>/dev/null)
            [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && echo "$ip" && return
        fi
        # Method 2: try common interface names in order (wired before Wi-Fi)
        for _iface in en0 en1 en2 en3 eth0; do
            ip=$(ipconfig getifaddr "$_iface" 2>/dev/null)
            [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && echo "$ip" && return
        done
        # Method 3: parse ifconfig — exclude loopback and link-local (169.254.x.x)
        ip=$(ifconfig 2>/dev/null \
             | awk '/inet /{print $2}' \
             | grep -v '^127\.' \
             | grep -v '^169\.254\.' \
             | head -1)
        [[ -n "$ip" ]] && echo "$ip" && return
    fi

    # Linux: ip route (most reliable)
    if command -v ip &>/dev/null; then
        ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')
        [[ -n "$ip" && "$ip" != "127.0.0.1" ]] && echo "$ip" && return
    fi

    # Fallback: hostname -I (exclude link-local)
    ip=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^169\.254\.' | head -1)
    [[ -n "$ip" ]] && echo "$ip" && return

    echo "127.0.0.1"
}

info "Detecting LAN IP..."
HOST_IP=$(detect_lan_ip)

if [[ "$HOST_IP" == "127.0.0.1" ]]; then
    warn "Could not detect LAN IP — targets will only be accessible on this machine."
else
    ok "LAN IP: ${BOLD}${HOST_IP}${NC}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. WRITE HOST_IP TO .env
# ─────────────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        ok "Created .env from .env.example"
    else
        die ".env file not found and .env.example is missing."
    fi
fi

rand() {
    openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p | tr -d '\n'
}

set_env_value() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env; then
        sed -i.bak "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
    else
        echo "${key}=${value}" >> .env
    fi
}

ensure_env_value() {
    local key="$1" value="$2"
    if ! grep -q "^${key}=" .env; then
        echo "${key}=${value}" >> .env
    fi
}

ensure_env_secret() {
    local key="$1" weak="$2" cur
    cur="$(grep "^${key}=" .env | cut -d= -f2- || true)"
    if [[ -z "$cur" || "$cur" == "$weak" ]]; then
        set_env_value "$key" "$(rand)"
        ok "Generated ${key}"
    fi
}

set_env_value HOST_IP "${HOST_IP}"
set_env_value CTF_HOSTNAME lloydsctf.lab
ensure_env_secret INSTANCE_MANAGER_SECRET change_this_to_a_random_instance_manager_secret
ensure_env_value INSTANCE_MANAGER_URL http://instance-manager:8088
ensure_env_value CTFD_TARGET_ORCHESTRATOR local
ensure_env_value INSTANCE_MANAGER_DEPLOY_MODE allocate
ensure_env_value CTFD_TARGET_IMAGE_PLATFORM multi
ensure_env_value CTFD_TARGET_INSTANCE_DISK_MB 256
export HOST_IP
export CTF_HOSTNAME=lloydsctf.lab
if [[ "$PRODUCTION" == "1" ]]; then
    weak_replace() {
        local key="$1" weak="$2"
        local cur
        cur="$(grep "^${key}=" .env | cut -d= -f2- || true)"
        if [[ -z "$cur" || "$cur" == "$weak" ]]; then
            if grep -q "^${key}=" .env; then
                sed -i.bak "s|^${key}=.*|${key}=$(rand)|" .env && rm -f .env.bak
            else
                echo "${key}=$(rand)" >> .env
            fi
            ok "Generated ${key}"
        fi
    }
    weak_replace FLAG_SECRET change_this_to_a_random_secret
    weak_replace CTFD_SECRET_KEY change_this_to_a_random_string
    weak_replace JWT_SECRET change_this_to_a_random_string
    set_env_value PRODUCTION 1
    set_env_value WORKERS 3
    set_env_value CTFD_TARGET_ORCHESTRATOR instance_manager
    set_env_value INSTANCE_MANAGER_DEPLOY_MODE worker
fi
ok ".env updated with HOST_IP=${HOST_IP} and CTF_HOSTNAME=lloydsctf.lab"

info "Validating Docker Compose configuration..."
"${COMPOSE_CMD[@]}" config --quiet
ok "Docker Compose configuration is valid."

# ─────────────────────────────────────────────────────────────────────────────
# 5. BUILD IMAGES
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$ARG" != "--restart" ]]; then
    info "Building Docker images (first run takes ~5 minutes)..."
    "${COMPOSE_CMD[@]}" build
    ok "Images built."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. START SERVICES
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$ARG" == "--restart" ]]; then
    info "Restarting dnsmasq and CTFd with new IP..."
    "${COMPOSE_CMD[@]}" up -d --force-recreate dnsmasq ctfd
else
    if [[ "$PRODUCTION" == "1" ]]; then
        info "Starting production core services..."
        "${COMPOSE_CMD[@]}" up -d proxy dnsmasq ctfd db cache instance-manager local-worker-agent
        "${COMPOSE_CMD[@]}" stop vbank-ctf vbank-analytics >/dev/null 2>&1 || true
    else
        info "Starting all services..."
        if $IS_MAC; then
            info "Optimizing for macOS..."
            docker update --pids-limit 2048 ctfd 2>/dev/null || true
        fi
        "${COMPOSE_CMD[@]}" up -d
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. WAIT FOR HEALTHY
# ─────────────────────────────────────────────────────────────────────────────
info "Waiting for CTFd to become healthy..."
TRIES=0
until "${COMPOSE_CMD[@]}" ps ctfd --format "{{.Status}}" 2>/dev/null | grep -q "healthy"; do
    TRIES=$((TRIES+1))
    [[ $TRIES -gt 50 ]] && die "\nCTFd failed to start. Check logs: ${COMPOSE_CMD[*]} logs ctfd"
    printf "."
    sleep 3
done
echo ""

if [[ "$PRODUCTION" == "1" ]]; then
    info "Waiting for local worker agent to register..."
    TRIES=0
    until "${COMPOSE_CMD[@]}" ps local-worker-agent --format "{{.Status}}" 2>/dev/null | grep -q "healthy"; do
        TRIES=$((TRIES+1))
        [[ $TRIES -gt 45 ]] && die "\nLocal worker agent failed to start. Check logs: ${COMPOSE_CMD[*]} logs local-worker-agent"
        printf "."
        sleep 2
    done
    echo ""
    ok "Local worker agent is healthy and auto-registered."
fi

info "Running CTFd setup job..."
if ! "${COMPOSE_CMD[@]}" up --no-deps --force-recreate setup; then
    "${COMPOSE_CMD[@]}" logs --tail 120 setup || true
    die "CTFd setup job failed."
fi
ok "CTFd setup job completed."

# ─────────────────────────────────────────────────────────────────────────────
# 8. DONE
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ✔ Lloyds CTF is live!${NC}"
echo ""
echo -e "  ${CYAN}Access URLs:${NC}"
echo -e "    • http://lloydsctf.lab or http://${HOST_IP}"
echo -e "    • https://lloydsctf.lab or https://${HOST_IP} (with CA cert trusted)"
echo ""
echo -e "  ${CYAN}DNS Server:${NC}  ${HOST_IP}:53"
echo ""
echo -e "  ${YELLOW}Certificate Status:${NC}"
echo -e "    ✓ Auto-generating in Docker (first start only)"
echo -e "    ✓ Available at: nginx/certs/ca.crt"
echo -e "    ✓ Players can download from: http://lloydsctf.lab/plugins/ctfd-target/certificate-guide"
echo ""
echo -e "  ${YELLOW}Player Setup Guide:${NC}"
echo -e "    1. Connect to the same LAN as this machine"
echo -e "    2. Set DNS server to ${HOST_IP} (or add this DNS server in your router DHCP settings)"
echo -e "    3. Install CA certificate (optional for HTTPS):"
echo -e "       • Visit: http://lloydsctf.lab/plugins/ctfd-target/certificate-guide"
echo -e "       • Download and trust the certificate"
echo -e "    4. Open: http://lloydsctf.lab"
echo ""
echo -e "  ${YELLOW}For macOS players:${NC}"
echo -e "    • Docker Desktop may pause idle containers - normal behavior"
echo -e "    • Containers auto-resume when accessed"
echo ""

if [[ "$PRODUCTION" == "1" ]]; then
    echo -e "  ${CYAN}Production:${NC}"
    echo -e "    - Instance Manager: enabled"
    echo -e "    - Local worker agent: auto-started"
    echo -e "    - Admin ops: http://${HOST_IP}/plugins/ctfd-target/admin/ops"
    echo ""
fi

if [[ "$ARG" == "--logs" ]]; then
    "${COMPOSE_CMD[@]}" logs -f
fi
