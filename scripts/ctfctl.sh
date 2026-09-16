#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONTROL_IP_DEFAULT="10.34.204.246"
CONTROL_IP="${CONTROL_IP:-$CONTROL_IP_DEFAULT}"
REMOTE_USER="${REMOTE_USER:-$USER}"
REMOTE_WORKERS_MAP="${REMOTE_WORKERS_MAP:-mac-mini-2=rislab-mini2.local,mac-mini-3=rislab-mini3.local}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new}"

info() { printf '\033[0;36m▶\033[0m %s\n' "$*"; }
ok() { printf '\033[0;32m✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
fail() { printf '\033[0;31m✖\033[0m %s\n' "$*" >&2; exit 1; }

secret() {
  awk -F= '/^INSTANCE_MANAGER_SECRET=/{print $2; exit}' .env 2>/dev/null
}

signed_get() {
  local path="$1"
  python3 - "$path" <<'PY'
import hashlib,hmac,json,sys,time,urllib.request
from pathlib import Path
path=sys.argv[1]
secret=''
for line in Path('.env').read_text().splitlines():
    if line.startswith('INSTANCE_MANAGER_SECRET='):
        secret=line.split('=',1)[1].strip(); break
if not secret:
    raise SystemExit('INSTANCE_MANAGER_SECRET missing')
body=b''
ts=str(int(time.time()))
sig=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
req=urllib.request.Request('http://127.0.0.1:8088'+path,method='GET',headers={'X-CTF-Timestamp':ts,'X-CTF-Signature':sig})
with urllib.request.urlopen(req,timeout=30) as r:
    print(r.read().decode())
PY
}

remote_hosts() {
  IFS=',' read -r -a pairs <<< "$REMOTE_WORKERS_MAP"
  for pair in "${pairs[@]}"; do
    pair="${pair//[[:space:]]/}"
    [[ -n "$pair" ]] || continue
    printf '%s\n' "${pair#*=}"
  done
}

usage() {
  cat <<EOF
Usage: scripts/ctfctl.sh <command>

Commands:
  start          Start/restart all 3 Mac minis using the production cluster script
  status         Show live architecture summary from Instance Manager
  urls           Print admin/player URLs
  logs           Tail key local service logs
  cleanup-tests  Remove stale load-test containers/records with prefixes crash/ramp/seq
  doctor         Run status plus local Docker service summary

Default control plane: mac-mini-1 at ${CONTROL_IP}
EOF
}

start_cluster() {
  exec ./scripts/start-macmini-cluster.sh
}

status_cluster() {
  local raw
  raw="$(signed_get /architecture)"
  ARCH_JSON="$raw" python3 - <<'PY'
import json, os
raw=os.environ.get('ARCH_JSON', '')
if not raw.strip():
    raise SystemExit('No architecture response')
data=json.loads(raw)
print('Cluster:', 'OK' if data.get('success') else 'ERROR')
print('Totals:', data.get('totals'))
for n in data.get('nodes', []):
    name=(n.get('labels') or {}).get('site') or n.get('worker_id')
    inv=n.get('inventory') or {}
    print(f"- {name}: ip={n.get('public_ip')} fresh={n.get('fresh')} age={n.get('heartbeat_age_seconds')}s active={len(n.get('instances') or [])} inventory={inv.get('success')} images={len(inv.get('images') or [])}")
PY
}

print_urls() {
  cat <<EOF
Player URL:       http://${CONTROL_IP}
Admin URL:        http://${CONTROL_IP}/admin
Architecture:     http://${CONTROL_IP}/plugins/ctfd-target/admin/architecture
Operations:       http://${CONTROL_IP}/plugins/ctfd-target/admin/ops
Health:           http://${CONTROL_IP}/plugins/ctfd-target/health
Instance Manager: http://${CONTROL_IP}:8088/health
EOF
}

logs() {
  docker logs --tail=80 ctf-main-instance-manager-1 || true
  docker logs --tail=80 ctf-main-local-worker-agent-1 || true
  docker logs --tail=80 ctf-main-ctfd-1 || true
}

cleanup_tests() {
  info "Removing local crash/ramp/seq test containers"
  for c in $(docker ps -a --format '{{.Names}}' | grep -E '^ctfd-(kali|target)-u(70|71|72)' || true); do
    docker rm -f "$c" || true
  done
  for host in $(remote_hosts); do
    info "Removing test containers on ${host}"
    ssh ${SSH_OPTS} "${REMOTE_USER}@${host}" 'export PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin; for c in $(docker ps -a --format "{{.Names}}" | grep -E "^ctfd-(kali|target)-u(70|71|72)" || true); do docker rm -f "$c" || true; done' || warn "Could not clean ${host}"
  done
  info "Backing up manager DB and deleting stale crash/ramp/seq records"
  docker run --rm -v ctf-main_instance-manager-data:/data python:3.11-slim python -c "import sqlite3, shutil, time; p='/data/instance-manager.sqlite3'; b=f'/data/instance-manager.sqlite3.backup-ctfctl-{int(time.time())}'; shutil.copy2(p,b); con=sqlite3.connect(p); cur=con.cursor(); cur.execute(\"delete from instances where id like 'crash-%' or id like 'ramp-%' or id like 'seq-%'\"); con.commit(); print('backup', b, 'deleted', cur.rowcount)"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml restart instance-manager local-worker-agent
  ok "Cleanup complete"
}

doctor() {
  print_urls
  echo
  status_cluster || warn "Architecture status failed"
  echo
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | sed -n '1,20p'
}

cmd="${1:-}"
case "$cmd" in
  start) start_cluster ;;
  status) status_cluster ;;
  urls) print_urls ;;
  logs) logs ;;
  cleanup-tests) cleanup_tests ;;
  doctor) doctor ;;
  -h|--help|help|'') usage ;;
  *) usage; fail "Unknown command: $cmd" ;;
esac
