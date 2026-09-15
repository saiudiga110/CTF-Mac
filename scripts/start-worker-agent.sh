#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${INSTANCE_MANAGER_URL:-}" ]]; then
  echo "INSTANCE_MANAGER_URL is required, for example http://192.168.1.10:8088" >&2
  exit 1
fi

if [[ -z "${INSTANCE_MANAGER_SECRET:-}" ]]; then
  echo "INSTANCE_MANAGER_SECRET is required and must match the control-plane manager" >&2
  exit 1
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
export WORKER_SERVE="${WORKER_SERVE:-0}"

if [[ "${WORKER_SERVE}" == "1" ]]; then
  python -m infra.instance_manager.worker_agent
else
  python -m infra.instance_manager.worker_agent
fi
