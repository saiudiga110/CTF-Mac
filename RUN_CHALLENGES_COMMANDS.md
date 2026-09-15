# Commands to Run the Challenges

This repository has one CTF platform, 10 grouped vBank target images, and an analytics sidecar:

- 10 profile images built from `challenges/vbank-ctf`: `vbank-auth:1`, `vbank-idor:1`, `vbank-logic:1`, `vbank-xss:1`, `vbank-staff-auth:1`, `vbank-staff-deep:1`, `vbank-crypto:1`, `vbank-integration:1`, `vbank-analytics-gateway:1`, `vbank-template:1`.
- `vbank-ctf`: all-profile image used for Global instance mode.
- `vbank-analytics`: sidecar used only by **Shared State** (`proto_pollution`).
- `kali`: the browser-based attack workstation built from `challenges/parrot`.

The 17 CTFd challenge cards each get their own container/port. Related cards may share an image, but `CHALLENGE_KEY` hides sibling flags.

## Run the Full CTF Platform

Use this when you want CTFd, the vBank target, analytics, DNS, proxy, database, Redis, setup, and the attack workstation image.

### Windows PowerShell

```powershell
# Start everything
.\start.ps1

# Start everything and follow logs
.\start.ps1 -Logs

# Restart after re-detecting your LAN IP
.\start.ps1 -Restart

# Stop everything
.\start.ps1 -Down
```

### Direct Docker Compose

```powershell
# Build all images
docker compose build

# Start all services
docker compose up -d

# Run the one-shot CTFd setup service again if needed
docker compose up setup

# Show running services
docker compose ps

# Follow all logs
docker compose logs -f

# Follow one service log
docker compose logs -f ctfd
docker compose logs -f vbank-ctf
docker compose logs -f vbank-analytics

# Stop services
docker compose down

# Stop services and remove volumes
docker compose down -v --remove-orphans
```

### Mac, Linux, or WSL

```bash
# Start everything
make start

# Stop everything
make stop

# Restart
make restart

# Follow logs
make logs

# Show status
make status

# Stop and remove CTF containers, networks, and volumes
make clean
```

## Build the 10 grouped target images

```powershell
docker compose build vbank-auth vbank-idor vbank-logic vbank-xss vbank-staff-auth vbank-staff-deep vbank-crypto vbank-integration vbank-analytics-gateway vbank-template vbank-analytics
```

## Run a single profile image for local testing

Use this for local testing of one grouped profile without CTFd.

```powershell
docker build -t vbank-analytics .\challenges\vbank-analytics
docker build --build-arg TARGET_PROFILE=auth -t vbank-auth:1 .\challenges\vbank-ctf

docker run --rm -d --name vbank-auth-test -p 8080:80 `
  -e FLAG_SECRET=vbank_ctf_default_2024 `
  -e TEAM_ID=0 `
  -e CHALLENGE_KEY=sqli_login `
  -e TARGET_PROFILE=auth `
  vbank-auth:1
```

Stop the standalone containers:

```powershell
docker stop vbank-auth-test
```

## Run Each Challenge Service from Compose

These commands run the service images defined in `docker-compose.yml`.

```powershell
# vBank all-profile image (Global mode)
docker compose up -d vbank-ctf

# Analytics sidecar (Shared State only)
docker compose up -d vbank-analytics

# Attack workstation image
docker compose build kali
docker compose up kali
```

## Run the Attack Workstation Standalone

```powershell
# Build the workstation image
docker build --platform linux/amd64 -t kali-ctf:latest .\challenges\parrot

# Run it against a standalone vBank target on localhost:8080
docker run --rm -it `
  --name kali-ctf `
  -p 6901:6901 `
  -e TARGET_URL=http://host.docker.internal:8080 `
  kali-ctf:latest

# Open noVNC
# http://localhost:6901
```

## Current Visible Challenge Map

All of these are available after `vbank-ctf` starts:

| # | Challenge | Runtime service |
|---|-----------|-----------------|
| 1 | Trust, Not Verified | `vbank-ctf` |
| 2 | Someone Else's Numbers | `vbank-ctf` |
| 3 | The Account No One Touches | `vbank-ctf` |
| 4 | Faster Than Careful | `vbank-ctf` |
| 6 | Word For Word | `vbank-ctf` |
| 7 | No Handle on Your Side | `vbank-ctf` |
| 8 | Who's Really Typing | `vbank-ctf` |

The hands-on solving guide for these cards is in [CHALLENGE_SOLVING_STEPS.md](CHALLENGE_SOLVING_STEPS.md).

## Useful Checks

```powershell
# Check containers
docker ps

# Check compose status
docker compose ps

# Check vBank logs
docker logs vbank-ctf
docker compose logs -f vbank-ctf

# Check analytics logs
docker logs vbank-analytics
docker compose logs -f vbank-analytics
```
