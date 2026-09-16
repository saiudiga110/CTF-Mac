# Lloyds CTF Platform

LAN-only CTFd deployment with vBank targets, Instance Manager scheduling, worker agents, and optional per-user Kali/Pwn Machine sessions.

## Quick Start

Windows PowerShell:

```powershell
./start.ps1 -Production
```

macOS/Linux:

```bash
chmod +x start.sh
./start.sh --production
```

The startup command creates `.env` from `.env.example` when needed, generates local secrets, detects the LAN host IP, builds images, starts CTFd, starts the Instance Manager, and starts the local worker agent.

## Production Notes

- Do not commit `.env` or `.data`; they are local runtime state.
- For 32 GB Mac Minis, allocate about 24-26 GB RAM to Docker/Desktop or the Linux container runtime.
- Kali/Pwn Machine capacity is controlled by `KALI_MEM_LIMIT`, `KALI_CPU_QUOTA`, `KALI_DISK_MB`, `KALI_PIDS_LIMIT`, and `KALI_SHM_SIZE`.
- The default Kali image is now a focused event profile: Firefox, terminal, Burp Suite, nmap, Hydra, John, apt installs, noVNC/XFCE. It skips themes, wallpapers, hashcat, rockyou, and extra fuzzers unless enabled with build args.
- A practical baseline is `KALI_MEM_LIMIT=1280m`, which supports more concurrent Kali sessions per worker while keeping Firefox + Burp usable.
- Keep CTFd, MariaDB, Redis, proxy, and Instance Manager on the control-plane Mac; use other Macs as workers for target/Kali containers.


## Three Mac Mini Cluster Start

Run this from Mac mini 1, the control-plane machine:

```bash
chmod +x scripts/start-macmini-cluster.sh
./scripts/start-macmini-cluster.sh
```

Defaults:

- Mac mini 1 starts as `mac-mini-1` and runs CTFd, MariaDB, Redis, proxy, Instance Manager, and its local worker.
- `rislab-mini2.local` is labelled `mac-mini-2`; `rislab-mini3.local` is labelled `mac-mini-3`.
- The script copies the current repo to each worker, syncs changed workload images, and starts `docker-compose.worker.yml` there.
- Architecture view: `/plugins/ctfd-target/admin/architecture` shows the three machines, LAN IPs, live worker status, exact container names, and tracked image sizes.

Override hostnames or IPs when needed:

```bash
CONTROL_IP=10.34.204.246 REMOTE_WORKERS=10.34.204.241,10.34.204.242 ./scripts/start-macmini-cluster.sh
```

## Health Checks

```bash
curl http://127.0.0.1/healthcheck
curl http://127.0.0.1/plugins/ctfd-target/health
curl http://127.0.0.1/plugins/ctfd-target/target/mode
```

## Important Docker Requirement

If new containers stay in `Created` state or Kali/targets do not launch, restart Docker and confirm Docker has enough memory allocated. The application may be healthy while Docker is unable to start new workload containers.