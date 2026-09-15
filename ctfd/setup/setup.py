#!/usr/bin/env python3
"""
vBank CTF â€” One-shot CTFd setup script.
Runs once at first deploy, creates admin + the 7 visible challenges from challenge-catalog.json.
Safe to re-run: skips if CTFd is already configured.
"""
import os
import sys
import time
import requests
import pymysql
from passlib.hash import bcrypt_sha256

CTFD_URL        = os.environ.get("CTFD_URL") or "http://ctfd:8000"
ADMIN_NAME      = os.environ.get("ADMIN_NAME") or "admin"
ADMIN_EMAIL     = os.environ.get("ADMIN_EMAIL") or "admin@vbank.ctf"
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD") or "vBankAdmin2024!"
CTF_NAME        = os.environ.get("CTF_NAME") or "Lloyds vBank CTF"
CTF_DESCRIPTION = os.environ.get("CTF_DESCRIPTION", "Break into vBank â€” 7 web security challenges across customer and staff portals.")
VBANK_URL       = os.environ.get("VBANK_URL") or "http://localhost:9000"
DB_HOST         = os.environ.get("DB_HOST") or "db"
MYSQL_DATABASE  = os.environ.get("MYSQL_DATABASE") or "ctfd"
MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD") or ""

SESSION = requests.Session()

from challenge_catalog import challenges as catalog_challenges

_CATALOG_ROWS = catalog_challenges()
CHALLENGES = [
    (
        row["name"],
        row["category"],
        row["value"],
        row["flag_key"],
        row.get("description_md") or row.get("description") or "",
        [(h["text"], h["cost"]) for h in row.get("hints", [])],
    )
    for row in _CATALOG_ROWS
]

# No hidden extras â€” the challenge seeder purges anything outside CHALLENGES.
HIDDEN_CHALLENGES = []

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def wait_for_ctfd(max_wait=300):
    print(f"[setup] Waiting for CTFd at {CTFD_URL} ...")
    for i in range(max_wait // 5):
        try:
            r = SESSION.get(f"{CTFD_URL}/", timeout=5)
            if r.status_code in (200, 302, 301):
                print(f"[setup] CTFd is up (attempt {i+1})")
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def is_already_configured():
    try:
        r = SESSION.get(f"{CTFD_URL}/setup", allow_redirects=False, timeout=10)
        # If /setup redirects away, CTFd is already configured
        return r.status_code in (301, 302)
    except Exception:
        return False


def get_nonce(path="/setup"):
    import re
    r = SESSION.get(f"{CTFD_URL}{path}", timeout=10)
    # Match <input ... name="nonce" ... value="..."> regardless of attribute order
    m = re.search(r'<input[^>]+name="nonce"[^>]+value="([^"]+)"', r.text)
    if not m:
        m = re.search(r'<input[^>]+value="([^"]+)"[^>]+name="nonce"', r.text)
    return m.group(1) if m else ""


def db_connect():
    if not MYSQL_ROOT_PASSWORD:
        raise RuntimeError("MYSQL_ROOT_PASSWORD is empty; cannot repair CTFd database")
    return pymysql.connect(
        host=DB_HOST,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def db_set_config(cur, key, value):
    cur.execute("SELECT id FROM config WHERE `key`=%s ORDER BY id LIMIT 1", (key,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE config SET value=%s WHERE id=%s", (value, row["id"]))
    else:
        cur.execute("INSERT INTO config (`key`, value) VALUES (%s, %s)", (key, value))


def repair_admin_and_registration():
    """Repair cloned/old deployments where .env credentials differ from DB state."""
    print("[setup] Repairing admin credentials and registration config from .env ...")
    password_hash = bcrypt_sha256.hash(str(ADMIN_PASSWORD))
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM users
                WHERE email=%s OR name=%s OR type='admin'
                ORDER BY
                    CASE WHEN email=%s THEN 0 WHEN name=%s THEN 1 ELSE 2 END,
                    id
                LIMIT 1
                """,
                (ADMIN_EMAIL, ADMIN_NAME, ADMIN_EMAIL, ADMIN_NAME),
            )
            row = cur.fetchone()
            if row:
                admin_id = row["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO users
                        (name, email, password, type, hidden, banned, verified, created)
                    VALUES
                        (%s, %s, %s, 'admin', 0, 0, 1, NOW(6))
                    """,
                    (ADMIN_NAME, ADMIN_EMAIL, password_hash),
                )
                admin_id = cur.lastrowid

            cur.execute(
                "UPDATE users SET email=CONCAT('old-', id, '-', email) WHERE id<>%s AND email=%s",
                (admin_id, ADMIN_EMAIL),
            )
            cur.execute(
                "UPDATE users SET name=CONCAT('old-', id, '-', name) WHERE id<>%s AND name=%s",
                (admin_id, ADMIN_NAME),
            )
            cur.execute(
                """
                UPDATE users
                SET name=%s, email=%s, password=%s, type='admin',
                    hidden=0, banned=0, verified=1, change_password=0
                WHERE id=%s
                """,
                (ADMIN_NAME, ADMIN_EMAIL, password_hash, admin_id),
            )

            db_set_config(cur, "prevent_registration", "false")
            db_set_config(cur, "verify_emails", "false")
            # Individual scoring/access is the default. Team Hub membership is optional.
            db_set_config(cur, "user_mode", "users")
    print(f"[setup] Admin repaired: {ADMIN_NAME} / {ADMIN_EMAIL}")


def configure_ctfd():
    print("[setup] Configuring CTFd for first time ...")
    nonce = get_nonce("/setup")
    r = SESSION.post(
        f"{CTFD_URL}/setup",
        data={
            "nonce":            nonce,
            "ctf_name":         CTF_NAME,
            "ctf_description":  CTF_DESCRIPTION,
            "user_mode":        "users",
            "name":             ADMIN_NAME,
            "email":            ADMIN_EMAIL,
            "password":         ADMIN_PASSWORD,
        },
        allow_redirects=True,
        timeout=30,
    )
    if r.status_code == 200 and "dashboard" in r.url:
        print("[setup] CTFd configured successfully.")
        return True
    # If already on dashboard or setup redirected us in
    if r.status_code in (200, 302):
        print(f"[setup] Setup response: {r.status_code} â†’ {r.url}")
        return True
    print(f"[setup] Setup may have failed: {r.status_code}")
    return False


def get_api_token():
    """Log in as admin and return an API access token (or None to use session cookies)."""
    nonce = get_nonce("/login")
    SESSION.post(
        f"{CTFD_URL}/login",
        data={"name": ADMIN_NAME, "password": ADMIN_PASSWORD, "_submit": "Submit", "nonce": nonce},
        allow_redirects=True,
        timeout=15,
    )
    # Verify login by calling an authenticated endpoint
    me = SESSION.get(f"{CTFD_URL}/api/v1/users/me", timeout=10)
    try:
        me_data = me.json()
    except ValueError:
        me_data = {}
    if me.status_code != 200 or not me_data.get("success"):
        print(f"[setup] Login failed â€” check ADMIN_NAME/ADMIN_PASSWORD env vars.")
        return None
    print(f"[setup] Logged in as {ADMIN_NAME} (id={me_data['data']['id']}).")
    # Generate a fresh access token
    r = SESSION.post(
        f"{CTFD_URL}/api/v1/tokens",
        json={"description": "setup-script", "expiration": None},
        headers={"Content-Type": "application/json", "CSRF-Token": _get_csrf()},
        timeout=10,
    )
    if r.status_code == 200:
        token = r.json().get("data", {}).get("value", "")
        if token:
            print(f"[setup] API token obtained.")
            return token
    # Fallback: use session cookie directly (None signals to api() to use SESSION)
    print("[setup] Using session cookie for API calls.")
    return "session"


def _get_csrf():
    """Extract CSRF nonce from CTFd settings page."""
    import re
    r = SESSION.get(f"{CTFD_URL}/settings", timeout=10)
    m = re.search(r"'csrfNonce':\s*\"([^\"]+)\"", r.text)
    return m.group(1) if m else ""


def api(method, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Content-Type"] = "application/json"
    if token and token != "session":
        headers["Authorization"] = f"Token {token}"
    else:
        headers["CSRF-Token"] = _get_csrf()
    fn = getattr(SESSION, method.lower())
    r = fn(f"{CTFD_URL}/api/v1{path}", headers=headers, timeout=15, **kwargs)
    return r


def challenge_exists(name, token):
    r = api("GET", "/challenges?view=admin", token=token)
    if r.status_code == 200:
        for c in r.json().get("data", []):
            if c["name"] == name:
                return c["id"]
    return None


def purge_noncanonical_challenges(token):
    """Delete every CTFd challenge that is not in the current 7-card set."""
    keep = {name for (name, *_rest) in CHALLENGES}
    r = api("GET", "/challenges?view=admin", token=token)
    if r.status_code != 200:
        print(f"[setup] Could not list challenges for purge: {r.status_code}")
        return
    purged = 0
    for c in r.json().get("data", []):
        if c["name"] in keep:
            continue
        dr = api("DELETE", f"/challenges/{c['id']}", token=token)
        if dr.status_code in (200, 204):
            print(f"[setup]   PURGED: {c['name']} (id={c['id']})")
            purged += 1
        else:
            print(f"[setup]   PURGE FAIL {c['name']}: {dr.status_code} {dr.text[:160]}")
    print(f"[setup] Purged {purged} non-canonical challenge(s); keeping {len(keep)}.")


def create_challenges(token):
    purge_noncanonical_challenges(token)
    print(f"[setup] Creating {len(CHALLENGES)} challenges ...")
    for (name, category, value, flag_key, description, hints) in CHALLENGES:
        existing_id = challenge_exists(name, token)
        if existing_id:
            print(f"[setup]   SKIP (exists): {name}")
            continue

        # Create challenge
        r = api("POST", "/challenges", token=token, json={
            "name":        name,
            "category":    category,
            "description": description,
            "value":       value,
            "type":        "standard",
            "state":       "visible",
        })
        if r.status_code != 200:
            print(f"[setup]   FAIL creating {name}: {r.status_code} {r.text[:200]}")
            continue

        chal_id = r.json()["data"]["id"]
        print(f"[setup]   OK challenge [{chal_id}]: {name}")

        # Create dynamic flag
        fr = api("POST", "/flags", token=token, json={
            "challenge": chal_id,
            "type":      "vbank_dynamic",
            "content":   flag_key,
            "data":      "",
        })
        if fr.status_code == 200:
            print(f"[setup]       flag: vbank_dynamic:{flag_key}")
        else:
            print(f"[setup]       flag FAILED: {fr.status_code} {fr.text[:200]}")

        # Create hints
        for (hint_text, cost) in hints:
            hr = api("POST", "/hints", token=token, json={
                "challenge": chal_id,
                "content":   hint_text,
                "cost":      cost,
            })
            status = "OK" if hr.status_code == 200 else f"FAIL {hr.status_code}"
            print(f"[setup]       hint ({cost}pts): {status}")


def create_hidden_challenges(token):
    print(f"[setup] Creating {len(HIDDEN_CHALLENGES)} hidden challenges ...")
    for (name, category, value, flag_key, description, hints) in HIDDEN_CHALLENGES:
        existing_id = challenge_exists(name, token)
        if existing_id:
            print(f"[setup]   SKIP (exists): {name}")
            continue

        r = api("POST", "/challenges", token=token, json={
            "name":        name,
            "category":    category,
            "description": description,
            "value":       value,
            "type":        "standard",
            "state":       "hidden",
        })
        if r.status_code != 200:
            print(f"[setup]   FAIL creating {name}: {r.status_code} {r.text[:200]}")
            continue

        chal_id = r.json()["data"]["id"]
        print(f"[setup]   OK hidden challenge [{chal_id}]: {name}")

        fr = api("POST", "/flags", token=token, json={
            "challenge": chal_id,
            "type":      "vbank_dynamic",
            "content":   flag_key,
            "data":      "",
        })
        if fr.status_code != 200:
            api("POST", "/flags", token=token, json={
                "challenge": chal_id,
                "type":      "static",
                "content":   f"LYD{{placeholder_{flag_key}}}",
                "data":      "",
            })
            print(f"[setup]   Flag: static placeholder")
        else:
            print(f"[setup]   Flag: vbank_dynamic({flag_key})")

        for hint_text, cost in hints:
            api("POST", "/hints", token=token, json={
                "challenge": chal_id,
                "content":   hint_text,
                "cost":      cost,
                "type":      "standard",
            })


def set_ctf_config(token):
    """Set CTF start/end times and other settings."""
    flag_secret = os.environ.get("FLAG_SECRET", "vbank_ctf_hmac_2024_change_me")
    configs = [
        {"key": "start",                    "value": ""},
        {"key": "end",                      "value": ""},
        {"key": "hide_scores",              "value": "false"},
        {"key": "prevent_registration",     "value": "false"},
        {"key": "team_size",                "value": "5"},
        # Target-plugin reads this to set FLAG_SECRET on spawned containers
        {"key": "ctfd_target:flag_secret",  "value": flag_secret},
    ]
    for cfg in configs:
        api("PATCH", f"/configs/{cfg['key']}", token=token, json={"value": cfg["value"]})
    print(f"[setup] CTF config applied (flag_secret set).")


def main():
    if not wait_for_ctfd():
        print("[setup] CTFd did not start in time â€” aborting.")
        sys.exit(1)

    if is_already_configured():
        print("[setup] CTFd already configured â€” running challenge sync only.")
        token = get_api_token()
        if token is None:
            repair_admin_and_registration()
            SESSION.cookies.clear()
            token = get_api_token()
        if token is None:
            print("[setup] Could not authenticate â€” skipping challenge sync.")
            sys.exit(1)
        create_challenges(token)
        create_hidden_challenges(token)
        set_ctf_config(token)
        sys.exit(0)

    if not configure_ctfd():
        print("[setup] CTFd configuration failed â€” aborting.")
        sys.exit(1)

    time.sleep(3)  # Let CTFd settle after setup

    token = get_api_token()
    if token is None:
        repair_admin_and_registration()
        SESSION.cookies.clear()
        token = get_api_token()
    if token:
        create_challenges(token)
        create_hidden_challenges(token)
        set_ctf_config(token)
    else:
        print("[setup] WARNING: No API token â€” challenges not created. Run again or create manually.")

    print("[setup] Done. CTFd is ready.")
    print(f"[setup]   Admin URL:  {CTFD_URL}")
    print(f"[setup]   Admin user: {ADMIN_NAME} / {ADMIN_PASSWORD}")
    print(f"[setup]   vBank URL:  {VBANK_URL}")


if __name__ == "__main__":
    main()

