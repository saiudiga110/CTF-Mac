# LAN Production And Load Balancing

This repo supports two production shapes:

- **Single node:** one Windows, macOS, or Linux host runs the full stack.
- **Four Mac mini cluster:** one front-door load balancer sends players to four backend Mac minis.

The vBank targets use the same backend IP as the CTF node plus a fixed host-port range. They do not need a second IP.

## Single Node

Run one command on the host:

```bash
./start-production.sh
```

On Windows, run PowerShell as Administrator:

```powershell
.\start-production.ps1
```

The script detects the LAN IP, writes `.env`, starts Docker Compose, and prints the exact URLs.

Open these inbound ports on the host:

```text
80/tcp
443/tcp
53/udp and 53/tcp if this host provides DNS
20000-20999/tcp for vBank targets
21000-21999/tcp for Pwn Machines
```

DNS:

```text
lloydsctf.lab -> HOST_IP
*.lab         -> HOST_IP
```

Players open:

```text
http://lloydsctf.lab
```

Launched vBank targets open as:

```text
http://HOST_IP:20xxx/
```

## Start

For a four-Mac cluster, choose one front-door IP and four backend IPs:

```env
LB_IP=192.168.0.10
BACKEND_1=192.168.0.11
BACKEND_2=192.168.0.12
BACKEND_3=192.168.0.13
BACKEND_4=192.168.0.14
```

On each backend Mac mini, run:

```bash
./start-production.sh
```

Make sure every backend has identical values for:

```text
CTFD_SECRET_KEY
FLAG_SECRET
JWT_SECRET
MYSQL_PASSWORD
```

Then run the front door:

```bash
docker compose --env-file .env.lb -f docker-compose.lb.yml up -d
```

Set LAN clients, or your router DHCP DNS option, to use `LB_IP` as DNS. Then `http://lloydsctf.lab/` resolves to the load balancer.

HAProxy assigns new browsers to the least busy healthy backend and inserts a sticky cookie so a player keeps returning to the Mac mini that owns their vBank containers.

Open these ports:

```text
Front-door Mac:
  80/tcp
  443/tcp
  53/udp and 53/tcp

Each backend Mac:
  80/tcp
  443/tcp
  20000-20999/tcp
  21000-21999/tcp
```

## Important

For a real scored event, do not run four totally separate CTFd databases behind the load balancer. Users, solves, sessions, uploads, and challenge state must be shared. The production-grade architecture is:

```text
front-door HAProxy/dnsmasq
  -> backend Mac mini 1: CTFd + vBank containers
  -> backend Mac mini 2: CTFd + vBank containers
  -> backend Mac mini 3: CTFd + vBank containers
  -> backend Mac mini 4: CTFd + vBank containers

shared MariaDB
shared Redis
shared upload storage
same secrets on every backend
```

Without shared MariaDB/Redis/uploads, the load balancer can still spread traffic, but each backend behaves like a separate event. That is not suitable for one 300-user scoreboard.

For a smaller event on one host, prefer vertical scaling first:

```bash
docker compose up -d --scale ctfd=4
```

The included nginx upstream uses `least_conn`, so multiple local `ctfd` replicas can be spread automatically by Docker DNS/nginx when the rest of the stack is still single-host.
