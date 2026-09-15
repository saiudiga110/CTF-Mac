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
- A practical baseline is `KALI_MEM_LIMIT=2048m`, which supports about 10 Kali sessions per 32 GB worker Mac after host/runtime reserves.
- Keep CTFd, MariaDB, Redis, proxy, and Instance Manager on the control-plane Mac; use other Macs as workers for target/Kali containers.

## Health Checks

```bash
curl http://127.0.0.1/healthcheck
curl http://127.0.0.1/plugins/ctfd-target/health
curl http://127.0.0.1/plugins/ctfd-target/target/mode
```

## Important Docker Requirement

If new containers stay in `Created` state or Kali/targets do not launch, restart Docker and confirm Docker has enough memory allocated. The application may be healthy while Docker is unable to start new workload containers.