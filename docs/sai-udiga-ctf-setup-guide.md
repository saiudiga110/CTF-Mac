# Sai Udiga - CTF Platform Setup and Operations Guide

Prepared for: **Sai Udiga**  
System: **Lloyds CTF / CTF-Main**  
Repository: `https://github.com/saiudiga110/CTF-Main`  
Local path: `/Users/rislab/CTF-Main`

## 1. Quick Answer

For normal event hosting, you should not need to configure the cluster manually again. From Mac mini 1, run:

```bash
cd /Users/rislab/CTF-Main
./scripts/ctfctl.sh start
```

Then open:

- Player site: `http://10.34.204.246`
- Admin: `http://10.34.204.246/admin`
- Architecture view: `http://10.34.204.246/plugins/ctfd-target/admin/architecture`
- Operations: `http://10.34.204.246/plugins/ctfd-target/admin/ops`

Use this to verify health:

```bash
./scripts/ctfctl.sh doctor
```

## 2. Current Machine Roles

| Machine | Hostname | LAN IP | Role |
|---|---:|---:|---|
| Mac mini 1 | `rislab-mini1.local` | `10.34.204.246` | Control plane + local worker |
| Mac mini 2 | `rislab-mini2.local` | `192.168.10.52` | Remote Docker worker |
| Mac mini 3 | `rislab-mini3.local` | `192.168.10.53` | Remote Docker worker |

Mac mini 1 runs CTFd, MariaDB, Redis, proxy, Instance Manager, one local worker agent, and SSH tunnels to the remote worker agents. Mac mini 2 and Mac mini 3 run worker agents only. The tunnels are important on Docker Desktop because containers on Mac mini 1 may not route directly to the `192.168.10.x` lab subnet.

## 3. Architecture

```text
Players/Admin Browser
        |
        v
Mac mini 1 - 10.34.204.246
  - Nginx proxy
  - CTFd web application
  - MariaDB
  - Redis
  - Instance Manager
  - Local Worker Agent
  - SSH tunnels:
      127.0.0.1:18091 -> mac-mini-2:8090
      127.0.0.1:18092 -> mac-mini-3:8090
        |
        +--------------------------+
        |                          |
        v                          v
Mac mini 2                    Mac mini 3
192.168.10.52                 192.168.10.53
Worker Agent :8090            Worker Agent :8090
Docker containers             Docker containers
```

## 4. How Images Are Shared

Images are not pulled at player launch time. The startup command builds or verifies the required images on Mac mini 1 and then syncs current image IDs to Mac mini 2 and Mac mini 3.

Required workload images include:

- `kali-ctf:latest`
- `vbank-ctf:latest`
- `vbank-auth:1`
- `vbank-idor:1`
- `vbank-logic:1`
- `vbank-xss:1`
- `vbank-staff-auth:1`
- `vbank-staff-deep:1`
- `vbank-analytics:latest`

The startup script compares Docker image IDs. If a remote machine has an old or missing image, it sends a compressed Docker archive and loads it there. The same command also opens or reuses the SSH tunnels needed for the manager container to contact remote worker agents.

## 5. How Target Placement Works

When a player starts a target or Kali session:

1. The browser calls CTFd.
2. CTFd asks the Instance Manager on Mac mini 1.
3. The Instance Manager reads live worker heartbeats and capacity.
4. It chooses Mac mini 1, 2, or 3 based on available CPU/RAM/container/port capacity.
5. It tells that machine's worker agent to start the Docker container locally. For remote workers, the manager reaches the agent through the automatic SSH tunnel advertised as `host.docker.internal:18091` or `host.docker.internal:18092`.
6. The selected worker returns a player URL like `http://192.168.10.52:20003/`.

If Mac mini 1 is full, new launches move to Mac mini 2 or Mac mini 3 automatically.

Target and Kali sidecars for the same player/challenge are colocated on the same worker so Kali can reach the target by `vbank-app` inside the shared Docker network.

## 6. Automation Commands

Run all commands from Mac mini 1:

```bash
cd /Users/rislab/CTF-Main
```

### Start or resume everything

```bash
./scripts/ctfctl.sh start
```

### Show status

```bash
./scripts/ctfctl.sh status
```

### Show useful URLs

```bash
./scripts/ctfctl.sh urls
```

### Run a full quick doctor check

```bash
./scripts/ctfctl.sh doctor
```

### View local logs

```bash
./scripts/ctfctl.sh logs
```

### Clean stale load-test containers and test records

```bash
./scripts/ctfctl.sh cleanup-tests
```


## 7. Resume or Start Next Time

Use this section when the Mac minis were restarted, Docker Desktop was quit, the lab was paused, or the LAN IP changed. You do not need to manually configure IP addresses, worker addresses, image sharing, or tunnels.

Run this only on Mac mini 1:

```bash
cd /Users/rislab/CTF-Main
./scripts/ctfctl.sh start
```

The resume command automatically performs these steps:

1. Detects the current Mac mini 1 LAN IP.
2. Updates `.env` with the detected `HOST_IP`.
3. Starts or resumes CTFd, proxy, database, cache, Instance Manager, and the local worker.
4. Connects to Mac mini 2 and Mac mini 3 over SSH.
5. Starts Docker Desktop on the remote machines if needed.
6. Syncs the latest repo code and workload images to the remote machines.
7. Creates or reuses SSH tunnels for remote worker control traffic.
8. Starts or restarts the remote worker agents.
9. Waits for worker heartbeats so the architecture page can show all machines.

After the command finishes, print the current URLs:

```bash
./scripts/ctfctl.sh urls
```

Then verify all three Mac minis are visible:

```bash
./scripts/ctfctl.sh status
```

Expected healthy output includes:

```text
Cluster: OK
- mac-mini-1: fresh=True inventory=True
- mac-mini-2: fresh=True inventory=True
- mac-mini-3: fresh=True inventory=True
```

If Mac mini 1 receives a different LAN IP after reboot, use the new URL printed by `./scripts/ctfctl.sh urls`. The automation updates the application, but players still need the current address unless the router reserves a fixed DHCP address for Mac mini 1.

Recommended production setup:

- Reserve Mac mini 1, Mac mini 2, and Mac mini 3 IP addresses in the router DHCP settings.
- Keep Docker Desktop set to start automatically on all three machines.
- Run `./scripts/ctfctl.sh start` before every event or lab session.
- Keep the Architecture page open during the event.

Resume troubleshooting:

| Symptom | Fix |
|---|---|
| URL changed after reboot | Run `./scripts/ctfctl.sh urls` and use the printed IP URL |
| Architecture page misses Mac mini 2 or 3 | Run `./scripts/ctfctl.sh start` again to refresh SSH tunnels and worker heartbeats |
| Remote worker inventory is false | Check SSH with `ssh rislab@rislab-mini2.local hostname` and `ssh rislab@rislab-mini3.local hostname` |
| Docker is not ready on a remote Mac | Open Docker Desktop on that Mac, then rerun `./scripts/ctfctl.sh start` |
| Old crash-test containers remain | Run `./scripts/ctfctl.sh cleanup-tests`, then `./scripts/ctfctl.sh status` |

## 8. Installation From GitHub on a Fresh Mac mini 1

```bash
cd /Users/rislab
git clone https://github.com/saiudiga110/CTF-Main.git
cd CTF-Main
chmod +x start.sh scripts/start-macmini-cluster.sh scripts/ctfctl.sh
./scripts/ctfctl.sh start
```

Prerequisites:

- Docker Desktop installed and running on all three Mac minis.
- Passwordless SSH from Mac mini 1 to Mac mini 2 and Mac mini 3.
- Same LAN connectivity between the three machines.
- SSH local forwarding allowed from Mac mini 1 to each remote Mac mini; `ctfctl start` creates the required forwards automatically.
- Mac mini 2 hostname: `rislab-mini2.local`.
- Mac mini 3 hostname: `rislab-mini3.local`.

## 9. Available Endpoints

### User and player endpoints

| Endpoint | Purpose |
|---|---|
| `/` | CTFd player site |
| `/challenges` | Challenge list |
| `/plugins/ctfd-target/target/start` | Start player target |
| `/plugins/ctfd-target/target/status` | Target status |
| `/plugins/ctfd-target/target/stop` | Stop target |
| `/plugins/ctfd-target/kali/start` | Start Kali/noVNC sidecar |
| `/plugins/ctfd-target/kali/status` | Kali status |
| `/plugins/ctfd-target/kali/sso-url` | Kali noVNC access URL |
| `/plugins/ctfd-target/kali/stop` | Stop Kali sidecar |
| `/plugins/ctfd-target/health` | Public plugin health |

### Admin endpoints

| Endpoint | Purpose |
|---|---|
| `/admin` | CTFd admin panel |
| `/plugins/ctfd-target/admin/architecture` | Live architecture view |
| `/plugins/ctfd-target/admin/architecture.json` | Architecture JSON for UI refresh |
| `/plugins/ctfd-target/admin/ops` | Event operations dashboard |
| `/plugins/ctfd-target/admin/network` | Network and DNS settings |
| `/plugins/ctfd-target/admin/settings` | Target/Kali settings |
| `/plugins/ctfd-target/admin/dashboard` | Running instance dashboard |

### Instance Manager endpoints

| Endpoint | Purpose |
|---|---|
| `http://10.34.204.246:8088/health` | Manager health |
| `/workers/heartbeat` | Worker registration heartbeat |
| `/nodes` | Current fresh workers |
| `/architecture` | Full architecture and inventory data |
| `/instances` | Instance allocation/listing |
| `/instances/<id>/status` | Instance status |
| `/instances/<id>` DELETE | Stop instance |

## 10. Production Notes

- The cluster is currently clean and healthy with 3 fresh nodes and zero test containers.
- Current image sizes are reduced: vBank about 170 MB and Kali about 2.85 GB.
- Architecture page auto-refreshes every 10 seconds.
- Worker heartbeats now retry after transient manager timeouts instead of exiting.
- Instance allocation is locked in the manager to avoid duplicate port assignment under concurrent launch bursts.
- DNS port 53 on Mac mini 1 is currently occupied by another local service. The app still works through direct IP: `http://10.34.204.246`.

## 10. Capacity Test Result

The current cluster reliably reached 21 Kali + 21 vBank target pairs in sequential testing before Docker create calls became too slow and timed out. The requested 30+ Kali sessions are close, but not yet reliable with on-demand Kali creation.

To make 30+ Kali sessions production reliable, use one or both of these improvements:

1. Pre-warm a pool of Kali containers before players arrive.
2. Reduce Kali startup cost further or split Kali sessions across more worker machines.

The automation is ready, but the platform should avoid sudden burst creation of 30+ Kali desktops at once unless a warm pool is implemented.

## 12. Troubleshooting

### Check everything

```bash
./scripts/ctfctl.sh doctor
```

### Architecture page does not show all 3 nodes

Run the startup command again. It refreshes worker containers, heartbeats, image sync, and SSH tunnels.

```bash
./scripts/ctfctl.sh start
```

Then wait 30 seconds and run:

```bash
./scripts/ctfctl.sh status
```

### Remote worker is stale

Check SSH:

```bash
ssh rislab@rislab-mini2.local hostname
ssh rislab@rislab-mini3.local hostname
```

Check tunnel listeners on Mac mini 1:

```bash
lsof -nP -iTCP:18091 -sTCP:LISTEN
lsof -nP -iTCP:18092 -sTCP:LISTEN
```

Check Docker on remote:

```bash
ssh rislab@rislab-mini2.local 'docker ps'
ssh rislab@rislab-mini3.local 'docker ps'
```

Then restart the whole cluster:

```bash
./scripts/ctfctl.sh start
```

### Stale test containers remain

```bash
./scripts/ctfctl.sh cleanup-tests
./scripts/ctfctl.sh status
```

### DNS does not work

Use direct IP:

```text
http://10.34.204.246
```

DNS port 53 is occupied on Mac mini 1. This does not block direct-IP operation.

### Images are old on remote machines

Run:

```bash
./scripts/ctfctl.sh start
```

The startup script compares Docker image IDs and syncs changed images automatically.

## 13. Files Added or Changed for Operations

| File | Purpose |
|---|---|
| `scripts/ctfctl.sh` | Operator command wrapper |
| `scripts/start-macmini-cluster.sh` | Full cluster start/sync command |
| `target-plugin/templates/architecture.html` | Architecture page UI |
| `infra/instance_manager/app.py` | Manager scheduling, architecture API, allocation lock |
| `infra/instance_manager/worker_agent.py` | Worker inventory and resilient heartbeat |
| `infra/instance_manager/docker_worker.py` | Serialized Docker create operation |
| `challenges/parrot/Dockerfile` | Reduced Kali image size |
| `challenges/vbank-ctf/Dockerfile` | Reduced vBank image size |

## 14. Daily Event Checklist

Before event:

```bash
cd /Users/rislab/CTF-Main
./scripts/ctfctl.sh start
./scripts/ctfctl.sh doctor
```

During event:

- Keep Architecture open: `http://10.34.204.246/plugins/ctfd-target/admin/architecture`
- Keep Operations open: `http://10.34.204.246/plugins/ctfd-target/admin/ops`

After event:

```bash
./scripts/ctfctl.sh cleanup-tests
```

Stop player instances from the Operations page if needed.
