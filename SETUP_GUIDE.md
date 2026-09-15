# Lloyds CTF — Complete Setup & Deployment Guide

> **Production-Ready** • Cross-Platform (Windows/Mac/Linux) • HTTPS Support • Auto-Scaling

## Table of Contents

1. [Quick Start](#quick-start)
2. [System Requirements](#system-requirements)
3. [Installation](#installation)
4. [HTTPS & Certificate Setup](#https--certificate-setup)
5. [Player Access](#player-access)
6. [Troubleshooting](#troubleshooting)
7. [macOS Specific Notes](#macos-specific-notes)

---

## Quick Start

### Option A: Linux/macOS/WSL2
```bash
git clone https://github.com/saiudiga110/CTF-v1.3.git
cd CTF-v1.3
cp .env.example .env
chmod +x start.sh
./start.sh
```

### Option B: Windows PowerShell
```powershell
git clone https://github.com/saiudiga110/CTF-v1.3.git
cd CTF-v1.3
Copy-Item .env.example .env
.\start.ps1
```

### Option C: Windows (Double-click)
1. Right-click `start.bat`
2. Select "Run as Administrator"
3. Follow the prompts

---

## System Requirements

### Host Machine
- **OS**: Windows 10/11, macOS 11+, or Linux (Ubuntu 20.04+)
- **CPU**: 4+ cores (8+ recommended)
- **RAM**: 8 GB minimum (16 GB recommended for production)
- **Disk**: 50 GB free space (SSD recommended)
- **Network**: Static LAN IP or DHCP reservation

### Docker
- **Docker Desktop 4.0+** (Windows/Mac)
- **Docker Engine 20.10+** (Linux)
- **Docker Compose v2+** (included with Docker Desktop)

### Network
- **All players must be on the same LAN**
- DNS server must be set to the host machine's IP
- Ports 80 (HTTP), 443 (HTTPS), 53 (DNS) must be open

---

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/saiudiga110/CTF-v1.3.git
cd CTF-v1.3
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and update:
```bash
# Change these to secure random values:
ADMIN_PASSWORD=YourSecurePassword2024!
FLAG_SECRET=your-random-hmac-secret-here
CTFD_SECRET_KEY=your-ctfd-session-secret
JWT_SECRET=your-jwt-secret
MYSQL_PASSWORD=your-mysql-password
```

### 3. Start the Platform

#### macOS / Linux / WSL2:
```bash
chmod +x start.sh start.ps1
./start.sh
```

#### Windows PowerShell:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\start.ps1
```

The script will:
- ✅ Detect your LAN IP automatically
- ✅ Install Docker if missing
- ✅ Generate TLS certificates
- ✅ Build all container images
- ✅ Start all services
- ✅ Wait for healthy status

### 4. Access the Admin Panel

Once the script completes, open:

```
http://lloydsctf.lab    (if DNS is configured)
http://[YOUR_LAN_IP]     (direct IP access)
https://lloydsctf.lab   (HTTPS, requires CA cert trust)
```

Default admin credentials:
```
Username: admin
Password: [as specified in .env]
```

---

## HTTPS & Certificate Setup

### Completely Automatic

The platform now generates all TLS certificates **automatically** on first startup:

1. When you run `./start.sh` or `docker compose up -d`
2. The nginx proxy container auto-detects missing certificates
3. Generates self-signed CA and server certificates
4. Configures HTTPS with TLS 1.2+
5. **No manual work required!**

### Certificate Details

- **CA Certificate**: `/nginx/certs/ca.crt` (install on player machines)
- **Server Certificate**: `/nginx/certs/default.crt` (used by nginx)
- **Server Key**: `/nginx/certs/default.key` (used by nginx)
- **Validity**: 10 years (CA) / 2 years (Server)
- **Coverage**: `*.lab`, `ctfd.lab`, `lloydsctf.lab`, `localhost`

### For Players: Installing the CA Certificate

#### Option A: Download from Web (Easiest)

1. Visit: `http://lloydsctf.lab/plugins/ctfd-target/certificate-guide`
2. Click "Download CA Certificate"
3. Install using platform-specific instructions below

#### Option B: Manual Installation

**Windows**
1. Download `ca.crt` from the guide above
2. Right-click → "Install Certificate"
3. Select "Local Machine"
4. Choose "Trusted Root Certification Authorities"
5. Complete the wizard

**macOS**
```bash
# After downloading ca.crt:
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ~/Downloads/ca.crt
```

**Linux (Ubuntu/Debian)**
```bash
sudo cp ca.crt /usr/local/share/ca-certificates/lloyds-ctf.crt
sudo update-ca-certificates
```

**Linux (Fedora/RHEL)**
```bash
sudo cp ca.crt /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

**Chrome/Chromium (All OS)**
1. Settings → Privacy & Security → Manage Certificates
2. Authorities tab → Import
3. Select `ca.crt`
4. Check all boxes → OK

---

## Player Access

### Setup Instructions for Players

1. **Configure DNS** (one of these):
   - Set DNS server to: `[CTF_HOST_IP]:53`
   - Or set your router DHCP DNS option to `[CTF_HOST_IP]` so all LAN clients receive it automatically
   - Or add to `/etc/hosts` (Linux/Mac):
     ```
     192.168.x.x lloydsctf.lab
     192.168.x.x ctfd.lab
     ```

2. **Install CA Certificate** (see above)

3. **Access CTF**:
   ```
   http://lloydsctf.lab
   ```

### Subdomain Routing

Each user gets their own subdomain, whether or not they join an optional team:
- vBank target: `https://[username].lab` or `http://[username].lab`
- Kali machine: `https://kali-[username].lab` or `http://kali-[username].lab`

---

## Troubleshooting

### Problem: "CTFd failed to start"

**Solution**:
```bash
# Check logs
docker compose logs ctfd

# Restart
docker compose down
docker compose up -d

# Check health
docker compose ps
```

### Problem: macOS - "Containers keep pausing"

**Why**: Docker Desktop has resource optimization that pauses idle containers.

**Solution**: The platform auto-resumes paused containers when accessed. If you want to disable this:

```bash
# Disable Docker Desktop resource saver
docker run --name prevent-pause -d --restart unless-stopped busybox tail -f /dev/null
```

### Problem: "Certificate verification failed"

**Solution**:
1. Download the CA certificate from `http://lloydsctf.lab/plugins/ctfd-target/certificate-guide`
2. Install it in your system's certificate store
3. Restart your browser
4. Clear browser cache

### Problem: "DNS not resolving *.lab"

**Solution**:
```bash
# Check dnsmasq is running
docker compose ps dnsmasq

# Check DNS
nslookup lloydsctf.lab 192.168.x.x    # macOS/Linux
nslookup lloydsctf.lab [CTF_HOST_IP]   # Windows PowerShell

# Restart DNS
docker compose restart dnsmasq
```

### Problem: "Target/Kali container won't start on macOS"

**Solution**:
1. Check Docker Desktop is running and not out of disk space
2. Increase Docker Desktop resources in Preferences
3. Try clearing unused containers:
   ```bash
   docker system prune -a --volumes
   ```

### Problem: "Port already in use"

**Solution**:
```bash
# Find process using port 80/443
sudo lsof -i :80
sudo lsof -i :443

# Kill process (replace PID with actual process ID)
kill -9 <PID>

# Or change ports in .env:
NGINX_PORT_HTTP=8080
NGINX_PORT_HTTPS=8443
```

---

## macOS Specific Notes

### Docker Desktop Optimization

macOS Docker Desktop has resource conservation that **pauses containers when idle** and **limits memory usage**. This is normal and expected behavior.

**Automatic resumption**: The CTF platform detects paused containers and automatically resumes them when players access targets. No manual intervention needed.

### Performance Tips

1. **Allocate sufficient resources**:
   - Docker Desktop → Preferences → Resources
   - CPUs: at least 4
   - Memory: at least 8 GB
   - Swap: 1 GB minimum

2. **Use Apple Silicon optimization**:
   - Docker Desktop → Preferences → General
   - Enable "Use native Apple Silicon"

3. **Monitor resource usage**:
   ```bash
   docker stats
   ```

### Network Issues

If DNS isn't working on macOS:

```bash
# Flush DNS cache
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder

# Verify DNS server
networksetup -getdnsservers Wi-Fi

# Set DNS temporarily
networksetup -setdnsservers Wi-Fi 192.168.x.x
```

---

## Administration

### View Live Container Dashboard

```
http://lloydsctf.lab/admin -> Plugin Menu -> Target Machine
```

### Manage Admin Settings

```
http://lloydsctf.lab/plugins/ctfd-target/admin/settings
```

### Team Join Approvals

The platform runs in individual-user mode: every registered user can access
challenges and score without creating a team. Teams are an optional collaboration
layer. Players use **Optional Teams → Team Hub** (`/api/ctf/team-hub`) to browse,
create, join, or leave one. Joining an existing team creates a pending request.
The captain receives a native CTFd notification and approves or declines it in
the same Team Hub. Flags and scores remain owned by each individual user.

### Database Backups

Automatic compressed MariaDB dumps are stored in:

```text
.data/CTFd/backups/ctfd-<timestamp>-<reason>.sql.gz
```

A backup is created before a full scoreboard reset or challenge deletion, and
when the configured CTF/event end time is detected. Destructive actions are
cancelled if their required backup fails. Admins can also use **Data Eraser →
Full Reset → Create Backup Only**.

Restore an automatic dump from the host:

```bash
python scripts/restore_ctfd.py .data/CTFd/backups/ctfd-<timestamp>-<reason>.sql.gz
```

### Check Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f ctfd
docker compose logs -f proxy
docker compose logs -f dnsmasq
```

### Stop Everything

```bash
./start.sh --down    # Linux/Mac
.\start.ps1 -Down    # Windows
```

### Restart After IP Change

```bash
./start.sh --restart    # Linux/Mac
.\start.ps1 -Restart    # Windows
```

---

## Production Deployment Checklist

- [ ] Update all `.env` secrets to strong, unique values
- [ ] Configure external domain instead of `.lab` (optional)
- [ ] Enable HTTPS on production domain
- [ ] Set up regular backups of `.data/` directory
- [ ] Monitor disk space and container resource usage
- [ ] Document your deployment IP and access procedures
- [ ] Create backup of `.env` file (store securely)
- [ ] Test player access from multiple devices/OSes
- [ ] Verify certificate installation works for all browsers

---

## Support

For issues, errors, or questions:

1. Check the troubleshooting section above
2. Review container logs: `docker compose logs -f`
3. File an issue on GitHub: https://github.com/saiudiga110/CTF-v1.3/issues

---

**Last Updated**: May 2026  
**Version**: 1.3 (Production)
