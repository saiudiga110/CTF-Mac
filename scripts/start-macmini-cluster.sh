#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONTROL_HOST="${CONTROL_HOST:-$(hostname -s 2>/dev/null || echo mac-mini-1)}"
CONTROL_IP="${CONTROL_IP:-}"
REMOTE_USER="${REMOTE_USER:-$USER}"
# Explicit labels avoid swapping Mac mini names when Bonjour hostnames differ.
REMOTE_WORKERS_MAP="${REMOTE_WORKERS_MAP:-mac-mini-2=rislab-mini2.local,mac-mini-3=rislab-mini3.local}"
REMOTE_WORKERS_CSV="${REMOTE_WORKERS:-}"
REPO_URL="${REPO_URL:-https://github.com/saiudiga110/CTF-Main.git}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new}"
REMOTE_DIR="${REMOTE_DIR:-$HOME/CTF-Main}"
WORKER_CONTAINER_LIMIT="${WORKER_CONTAINER_LIMIT:-80}"
WORKER_RUNTIME_RAM_MB="${WORKER_RUNTIME_RAM_MB:-23994}"
WORKER_RUNTIME_CPUS="${WORKER_RUNTIME_CPUS:-}"
WORKER_TARGET_PORT_START="${WORKER_TARGET_PORT_START:-20000}"
WORKER_TARGET_PORT_END="${WORKER_TARGET_PORT_END:-20999}"
REMOTE_WORKER_TUNNEL_BASE_PORT="${REMOTE_WORKER_TUNNEL_BASE_PORT:-18090}"
CONTROL_WORKER_ID="${CONTROL_WORKER_ID:-mac-mini-1}"
CONTROL_WORKER_LABELS="${CONTROL_WORKER_LABELS:-site=mac-mini-1,role=control-worker}"
SYNC_WORKLOAD_IMAGES="${SYNC_WORKLOAD_IMAGES:-1}"
REQUIRED_IMAGES=(
  "kali-ctf:latest"
  "vbank-ctf:latest"
  "vbank-auth:1"
  "vbank-idor:1"
  "vbank-logic:1"
  "vbank-xss:1"
  "vbank-staff-auth:1"
  "vbank-staff-deep:1"
  "vbank-analytics:latest"
)

info() { printf '\033[0;36m▶\033[0m %s\n' "$*"; }
ok() { printf '\033[0;32m✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
die() { printf '\033[0;31m✖\033[0m %s\n' "$*" >&2; exit 1; }

set_env_value() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

detect_control_ip() {
  if [[ -n "$CONTROL_IP" && "$CONTROL_IP" != "127.0.0.1" ]]; then
    printf '%s\n' "$CONTROL_IP"
    return
  fi
  if [[ -f .env ]]; then
    awk -F= '/^HOST_IP=/ && $2 != "127.0.0.1" {print $2; exit}' .env || true
  fi
  if command -v route >/dev/null 2>&1 && command -v ipconfig >/dev/null 2>&1; then
    local iface
    iface="$(route -n get 1.1.1.1 2>/dev/null | sed -n 's/^ *interface: //p' | head -1 || true)"
    [[ -n "$iface" ]] && ipconfig getifaddr "$iface" 2>/dev/null || true
  elif command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{print $1}' || true
  fi
}

parse_workers() {
  if [[ -n "$REMOTE_WORKERS_CSV" ]]; then
    local idx=1
    IFS=',' read -r -a hosts <<< "$REMOTE_WORKERS_CSV"
    for host in "${hosts[@]}"; do
      host="${host//[[:space:]]/}"
      [[ -n "$host" ]] && printf 'mac-mini-%s=%s\n' "$idx" "$host"
      idx=$((idx + 1))
    done
    return
  fi
  IFS=',' read -r -a pairs <<< "$REMOTE_WORKERS_MAP"
  for pair in "${pairs[@]}"; do
    pair="${pair//[[:space:]]/}"
    [[ -n "$pair" ]] && printf '%s\n' "$pair"
  done
}

remote() {
  local host="$1"
  shift
  ssh ${SSH_OPTS} "${REMOTE_USER}@${host}" "$@"
}

remote_docker_ready() {
  local host="$1"
  remote "$host" "export PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin; docker info >/dev/null 2>&1"
}

ensure_remote_docker() {
  local host="$1"
  if remote_docker_ready "$host"; then
    return 0
  fi
  warn "Docker is not ready on ${host}; trying to start Docker Desktop"
  remote "$host" "open -a Docker >/dev/null 2>&1 || true" || true
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 5
    if remote_docker_ready "$host"; then
      ok "Docker is ready on ${host}"
      return 0
    fi
  done
  warn "Skipping ${host}; Docker did not become ready"
  return 1
}

local_image_manifest() {
  local image image_id
  for image in "${REQUIRED_IMAGES[@]}"; do
    image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
    [[ -n "$image_id" ]] || die "Required local image is missing: ${image}"
    printf '%s=%s\n' "$image" "$image_id"
  done
}

remote_image_manifest() {
  local host="$1"
  ssh ${SSH_OPTS} "${REMOTE_USER}@${host}" bash -s -- "${REQUIRED_IMAGES[@]}" <<'REMOTE_IMAGE_CHECK'
set -euo pipefail
export PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin
for image in "$@"; do
  image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
  if [[ -z "$image_id" ]]; then
    image_id="missing"
  fi
  printf '%s=%s\n' "$image" "$image_id"
done
REMOTE_IMAGE_CHECK
}

remote_has_current_images() {
  local host="$1"
  local local_manifest remote_manifest
  local_manifest="$(local_image_manifest)"
  remote_manifest="$(remote_image_manifest "$host" 2>/dev/null || true)"
  [[ "$local_manifest" == "$remote_manifest" ]]
}

sync_images_if_needed() {
  local host="$1"
  [[ "$SYNC_WORKLOAD_IMAGES" == "1" ]] || return 0
  if remote_has_current_images "$host"; then
    ok "Required images are current on ${host}"
    return 0
  fi
  local archive="/tmp/ctf-workload-images-$$.tar.gz"
  info "Packaging workload images for ${host}"
  docker save "${REQUIRED_IMAGES[@]}" | gzip -1 > "$archive"
  scp ${SSH_OPTS} "$archive" "${REMOTE_USER}@${host}:/tmp/ctf-workload-images.tar.gz"
  rm -f "$archive"
  remote "$host" "export PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin; docker load -i /tmp/ctf-workload-images.tar.gz; rm -f /tmp/ctf-workload-images.tar.gz"
  ok "Loaded workload images on ${host}"
}

start_worker_tunnel() {
  local host="$1" port="$2"
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    ok "Worker tunnel already listening on 127.0.0.1:${port}"
    return 0
  fi
  info "Opening worker tunnel 127.0.0.1:${port} -> ${host}:8090"
  ssh ${SSH_OPTS} -o ExitOnForwardFailure=yes -f -N \
    -L "127.0.0.1:${port}:127.0.0.1:8090" \
    "${REMOTE_USER}@${host}"
  ok "Worker tunnel ready on host.docker.internal:${port}"
}

CONTROL_IP="$(detect_control_ip | tail -1)"
[[ -n "$CONTROL_IP" ]] || die "Could not detect the control-plane LAN IP."

info "Starting control plane on ${CONTROL_HOST} (${CONTROL_IP})"
export WORKER_ID="$CONTROL_WORKER_ID"
export WORKER_LABELS="$CONTROL_WORKER_LABELS"
export INSTANCE_MANAGER_DEPLOY_MODE=worker
export CTFD_TARGET_ORCHESTRATOR=instance_manager
./start.sh --production

[[ -f .env ]] || die ".env was not created by start.sh"
SECRET="$(awk -F= '/^INSTANCE_MANAGER_SECRET=/{print $2; exit}' .env)"
[[ -n "$SECRET" ]] || die "INSTANCE_MANAGER_SECRET missing in .env"
set_env_value INSTANCE_MANAGER_URL http://instance-manager:8088
set_env_value INSTANCE_MANAGER_DEPLOY_MODE worker
set_env_value CTFD_TARGET_ORCHESTRATOR instance_manager
set_env_value WORKER_ID "$CONTROL_WORKER_ID"
set_env_value WORKER_LABELS "$CONTROL_WORKER_LABELS"
set_env_value KALI_MEM_LIMIT "${KALI_MEM_LIMIT:-1280m}"

worker_index=0
while IFS='=' read -r -u 3 worker_id host; do
  [[ -n "${worker_id:-}" && -n "${host:-}" ]] || continue
  worker_index=$((worker_index + 1))
  tunnel_port=$((REMOTE_WORKER_TUNNEL_BASE_PORT + worker_index))
  info "Preparing ${worker_id} on ${host}"
  if ! ssh ${SSH_OPTS} "${REMOTE_USER}@${host}" "true" >/dev/null 2>&1; then
    warn "Skipping ${worker_id} (${host}); passwordless SSH is not available"
    continue
  fi
  ensure_remote_docker "$host" || continue
  start_worker_tunnel "$host" "$tunnel_port"
  remote "$host" "mkdir -p '${REMOTE_DIR}'"
  rsync -az --delete \
    --exclude '.git' --exclude '.data' --exclude 'logs' --exclude '__pycache__' \
    ./ "${REMOTE_USER}@${host}:${REMOTE_DIR}/"
  sync_images_if_needed "$host"
  remote "$host" \
    "set -euo pipefail; \
     export PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin; \
     cd '${REMOTE_DIR}'; \
     iface=\$(route -n get 1.1.1.1 2>/dev/null | sed -n 's/^ *interface: //p' | head -1); \
     worker_ip=\$(ipconfig getifaddr \"\$iface\" 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print \$1}'); \
     export WORKER_PUBLIC_IP=\$worker_ip; \
     export WORKER_AGENT_URL='http://host.docker.internal:${tunnel_port}'; \
     export INSTANCE_MANAGER_URL='http://${CONTROL_IP}:8088'; \
     export INSTANCE_MANAGER_SECRET='${SECRET}'; \
     export WORKER_ID='${worker_id}'; \
     export WORKER_LABELS='site=${worker_id},role=remote-worker'; \
     export WORKER_CONTAINER_LIMIT='${WORKER_CONTAINER_LIMIT}'; \
     export WORKER_RUNTIME_RAM_MB='${WORKER_RUNTIME_RAM_MB}'; \
     export WORKER_RUNTIME_CPUS='${WORKER_RUNTIME_CPUS}'; \
     export WORKER_TARGET_PORT_START='${WORKER_TARGET_PORT_START}'; \
     export WORKER_TARGET_PORT_END='${WORKER_TARGET_PORT_END}'; \
     docker compose -f docker-compose.worker.yml up -d --build worker-agent"
  ok "Requested worker start on ${host}"
done 3< <(parse_workers)

info "Waiting for worker heartbeats"
sleep 10
curl -fsS "http://${CONTROL_IP}:8088/health" >/dev/null || warn "Instance Manager health endpoint was not reachable from this shell"
ok "Cluster startup completed. Architecture view: http://${CONTROL_IP}/plugins/ctfd-target/admin/architecture"
