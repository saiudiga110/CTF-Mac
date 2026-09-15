# Instance Manager

This service is the migration path from single-host CTFd Docker control to a
cross-platform worker fleet.

Run the manager on the protected control-plane machine. Run the worker agent on
each eligible machine. The agent auto-detects OS, architecture, Docker runtime,
available resources, port ranges, platform support, and security features, then
heartbeats to the manager.

The manager schedules new instances by capability and live capacity. It does not
assume macOS, Windows, Linux, Intel, or Apple Silicon.

## Local Smoke Test

```bash
python -m infra.instance_manager.discovery
INSTANCE_MANAGER_DISABLE_AUTH=1 INSTANCE_MANAGER_DB=.data/instance-manager.sqlite3 \
  python -m infra.instance_manager.app
```

In another shell:

```bash
INSTANCE_MANAGER_DISABLE_AUTH=1 WORKER_ONCE=1 \
  python -m infra.instance_manager.worker_agent
```

## Production Autoscaling Mode

On the control-plane machine:

```bash
docker compose up -d instance-manager
```

Set:

```text
CTFD_TARGET_ORCHESTRATOR=instance_manager
INSTANCE_MANAGER_DEPLOY_MODE=worker
INSTANCE_MANAGER_URL=http://instance-manager:8088
INSTANCE_MANAGER_SECRET=<same strong secret everywhere>
```

On every worker machine:

```bash
export INSTANCE_MANAGER_URL=http://<control-plane-ip>:8088
export INSTANCE_MANAGER_SECRET=<same strong secret>
export WORKER_SERVE=1
./scripts/start-worker-agent.sh
```

Windows PowerShell:

```powershell
.\scripts\start-worker-agent.ps1 `
  -ManagerUrl http://<control-plane-ip>:8088 `
  -Secret <same strong secret> `
  -Serve
```

The worker agent auto-detects the machine and continuously heartbeats to the
manager. With `WORKER_SERVE=1`, it also accepts signed create/status/delete
commands from the manager and executes Docker operations locally.

## Required Production Secret

Set the same value on manager and workers:

```text
INSTANCE_MANAGER_SECRET=<random 32+ byte secret>
```

CTFd must call the manager with `X-CTF-Timestamp` and `X-CTF-Signature`, where
the signature is HMAC-SHA256 over:

```text
timestamp + "." + raw_request_body
```

CTFd integration is feature-flagged through `CTFD_TARGET_ORCHESTRATOR`. The
legacy local Docker path remains available by setting it back to `local`.
