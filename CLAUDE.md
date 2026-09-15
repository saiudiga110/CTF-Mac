# Lloyds CTF — Project Context for Claude

## What This Is
A self-hosted CTF (Capture The Flag) platform built on CTFd 3.8.6. Players attack an intentionally vulnerable banking app called **vBank**. Every user gets an isolated Docker target; optional teams provide collaboration without changing individual flags or scoring. Runs on a single Linux/Mac/Windows machine on a LAN.

---

## Architecture Overview

```
Players (browser)
    │
    ▼
nginx-proxy :80  ──── ctfd.lab ──────► CTFd :8000
    │                 {user}.lab ─────► vbank-ctf container (per team)
    │                 kali-{user}.lab ► parrot-ctf container (per team)
    │
dnsmasq :53  ── resolves *.lab → HOST_IP (LAN IP)

CTFd container
  ├── target-plugin       ← manages per-user Docker containers
  ├── enhanced-features   ← teammate notifications, UI features
  ├── vbank_flags         ← HMAC flag verification
  └── whale-plugin        ← (legacy, mostly unused)

Per-user stack (spun up on demand):
  ctfd-target-{user}           Global mode: vbank-ctf image, port 80 → HOST_IP:RANDOM_PORT
  ctfd-chal-{user}-c{id}       Per-challenge mode: grouped profile image (17 configs, 10 tags)
  ctfd-analytics-{user}-c{id}  vbank-analytics sidecar (Shared State only)
  ctfd-net-{user}              private bridge network
```

---

## Key Files

| File | Purpose |
|------|---------|
| `challenge-catalog.json` | Authoritative 17-challenge catalog (names, keys, images, docs) |
| `docker-compose.yml` | All services: proxy, dnsmasq, ctfd, db, cache, 10 profile images, vbank-analytics, parrot, setup |
| `.env` | Runtime secrets + HOST_IP (auto-set by start scripts) |
| `.env.example` | Template for fresh deploys — no real secrets |
| `start.sh` | Mac/Linux/WSL2 one-command installer + launcher |
| `start.ps1` | Windows PowerShell installer + launcher |
| `start.bat` | Windows double-click → calls start.ps1 |
| `Makefile` | `make` shortcut on Mac/Linux |
| `target-plugin/__init__.py` | Core plugin: container lifecycle, Flask routes, flag type |
| `target-plugin/assets/target-inject.js` | Challenges page UI (banner, timers, launch/destroy buttons) |
| `target-plugin/assets/cyber-theme.css` | Cyberpunk dark theme injected into all CTFd pages |
| `enhanced-features-plugin/__init__.py` | Teammate solve toasts, extended UI features |
| `challenges/vbank-ctf/app.py` | The intentionally vulnerable Flask banking app |
| `challenges/vbank-analytics/app.js` | Node.js analytics sidecar (SSRF target) |
| `challenges/parrot/Dockerfile` | Parrot OS + noVNC Pwn Machine image |
| `ctfd/setup/setup.py` | One-shot: creates admin + the 7 visible challenges on first boot |
| `dnsmasq/Dockerfile` | Alpine + dnsmasq; wildcard *.lab → HOST_IP |
| `nginx/proxy-settings.conf` | Timeout/size settings for nginx-proxy |

---

## Environment Variables (.env)

| Variable | Purpose |
|----------|---------|
| `HOST_IP` | **Auto-set by start scripts** — machine's LAN IP (e.g. 192.168.0.8) |
| `FLAG_SECRET` | HMAC key; flags = `CTF{md5(vbank:{secret}:{username})[:16]}` |
| `JWT_SECRET` | vBank JWT signing key (API Portal challenge) |
| `CTFD_SECRET_KEY` | Flask session secret |
| `MYSQL_PASSWORD` | MariaDB password |
| `ADMIN_NAME/EMAIL/PASSWORD` | CTFd admin credentials |
| `LAB_DOMAIN` | Subdomain suffix (default: `lab`) |

---

## Target Container Lifecycle

1. Player clicks **Launch Target** on `/challenges`
2. `POST /plugins/ctfd-target/target/start` → `_start_target_stack(username)`
3. Plugin creates: isolated bridge network `ctfd-net-{user}` + analytics sidecar + vbank-ctf container
4. vbank-ctf binds **random host port** → URL = `http://{HOST_IP}:{PORT}/`
5. UI shows link labelled **`vbank.lab`** (href = real HOST_IP:PORT)
6. Auto-expires after `instance_lifetime` seconds (default 3600)
7. Auto-extends at 15 min remaining if player is active
8. Cleanup loop runs every 30s; `_cleanup_loop()` removes expired containers

---

## Flag System

```python
digest = hmac_sha256(FLAG_SECRET, f"{owner_id}:{flag_key}")[:24]
flag = f"LYD{{{digest}}}"
```

Owner is `user.id` (users mode) or `team_id` (teams mode). CTFd flags are type `vbank_dynamic` with `content` = flag key (e.g. `sqli_login`). The Flask app `make_flag()` and `vbank_flags` plugin use the same formula.

## INTENTIONAL VULNERABILITIES IN vbank-ctf — DO NOT FIX

These are the CTF challenges. Never patch them (except when aligning routes/flags with the catalog):

| Bug | Location | Challenge / key |
|-----|----------|-----------------|
| SQL injection (customer login) | `/customer/login` | Trust, Not Verified / `sqli_login` |
| IDOR statements | `/api/v1/accounts/<id>/transactions` | Someone Else's Numbers / `idor_statements` |
| Transfer authorization bypass | `/account/transfer/confirm` | The Account No One Touches / `idor_transfer` |
| Race condition | `/api/v1/transfer/express` | Faster Than Careful / `race_condition` |
| Reflected XSS | `/search` | Word For Word / `reflected_xss` |
| Staff login SQLi | `/maintenance-portal` | No Handle on Your Side / `staff_sqli` |
| Command injection | `/staff/maintenance` | Who's Really Typing / `rce` |
| Open redirect | `/customer/login?next=` | Redirected Trust / `open_redirect` |
| Stored XSS | `/account/support` → `/staff/tickets` | Feedback Loop / `xss` |
| JWT alg:none | `/api/v2/corporate/vault` | Unsigned Agreement / `jwt` |
| AES-ECB | `/api/v1/receipts/<id>` | The Repeating Pattern / `crypto_ecb` |
| SSRF | `/support/document-fetch` | Internal Affairs / `ssrf` |
| XXE | `/staff/payroll` | Document Confession / `xxe` |
| Prototype pollution | `/api/settings/merge` | Shared State / `proto_pollution` |
| SSTI | `/account/profile` | Template Escape / `ssti` |
| Path traversal | `/staff/files` | The Archivist / `path_traversal` |
| Mass assignment | `/api/v1/staff/onboard` | Privilege by Payload / `mass_assignment` |

---

## Docker Networking

```
proxy-net (bridge, external)    ← nginx-proxy + CTFd + target containers
lloydsctf_default               ← CTFd + vbank-ctf + vbank-analytics (compose default)
lloydsctf_internal              ← db + cache only (no external access)
ctfd-net-{username} (bridge)    ← per-user isolated network
```

Target containers bind `ports={"80/tcp": None}` → random host port on `0.0.0.0`.
Accessible at `http://{HOST_IP}:{PORT}/` from any machine on the LAN.

---

## Plugin Routes (target-plugin)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/plugins/ctfd-target/target/start` | user | Launch target stack |
| GET | `/plugins/ctfd-target/target/status` | user | Poll running status + URL |
| DELETE | `/plugins/ctfd-target/target/stop` | user | Destroy target |
| POST | `/plugins/ctfd-target/target/extend` | user | Extend expiry +1h |
| POST | `/plugins/ctfd-target/kali/start` | user | Launch Pwn Machine |
| GET | `/plugins/ctfd-target/kali/status` | user | Pwn Machine status |
| GET | `/plugins/ctfd-target/kali/sso-url` | user | Pre-auth noVNC URL |
| DELETE | `/plugins/ctfd-target/kali/stop` | user | Destroy Pwn Machine |
| GET | `/plugins/ctfd-target/admin/dashboard` | admin | Live container table |
| GET/POST | `/plugins/ctfd-target/admin/settings` | admin | Plugin config |

---

## Asset Versioning

CSS/JS are injected via `after_request` hook. Bump version when changing assets:
```python
# target-plugin/__init__.py ~line 831
_CSS_TAG = '...cyber-theme.css?v=18'   # increment v= on every CSS change
_JS_TAG  = '...target-inject.js?v=18'  # increment v= on every JS change
_CSS_MARKER = b"cyber-theme.css?v=18"  # must match _CSS_TAG version
```

---

## Cross-Platform Startup

`start.sh` / `start.ps1` auto-detect LAN IP using:
- **Windows**: default-route interface via `Get-NetRoute`
- **WSL2**: calls `powershell.exe` to get Windows LAN IP
- **Mac**: `route -n get 1.1.1.1` → interface → `ipconfig getifaddr`
- **Linux**: `ip route get 1.1.1.1 | grep src`

Writes detected IP to `HOST_IP=` in `.env`, exports it, then runs `docker compose up -d`.

---

## Git Remotes

| Remote | URL |
|--------|-----|
| `origin` | CTF-v1.1 (base) |
| `ctf-v12` | CTF-v1.2 |
| `ctf-v13` | CTF-v1.3 (current/latest) |

Always push to `ctf-v13` for latest: `git push ctf-v13 main`

---

## Admin Credentials (dev/test)
- CTFd: `Sai_Udiga` / `vBankAdmin2024!`
- URL: `http://192.168.0.8` (or `http://ctfd.lab` with DNS set to HOST_IP)

---

## What NOT to Do

- **Never fix** the intentional vulnerabilities in `challenges/vbank-ctf/app.py`
- **Never commit** `.env` (gitignored — contains real secrets)
- **Never commit** `.data/` (runtime database/log files)
- **Don't use subdomain URLs** for target containers — port binding is the working approach on Docker Desktop Windows/Mac
- **Don't change** `HOST_IP` manually in `.env` — always let `start.sh`/`start.ps1` set it
