#!/bin/bash
set -e

python3 - <<'PYEOF'
import hmac, hashlib, os, sqlite3, json

secret  = os.environ.get('FLAG_SECRET', 'vbank_ctf_default_2024')
team    = os.environ.get('TEAM_ID', '0')

def make_flag(challenge):
    h = hmac.new(secret.encode(), f"{team}:{challenge}".encode(), hashlib.sha256)
    return f"LYD{{{h.hexdigest()[:24]}}}"

# Flag: rce — read via maintenance console: cat /root/flag.txt
os.makedirs('/root', exist_ok=True)
with open('/root/flag.txt', 'w') as f:
    f.write(make_flag('rce') + '\n')

# Flag: xxe — read via XXE external entity injection
with open('/etc/vbank_internal.txt', 'w') as f:
    f.write(make_flag('xxe') + '\n')

# Update DB: IDOR statements CEO memo (in case DB still has placeholder)
db_path = '/app/database.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE statements SET memo = ? WHERE user_id = 1337 AND description = 'CONFIDENTIAL TRANSFER'",
        (f"FLAG: {make_flag('idor_statements')}",)
    )
    # Update ECB receipt TXN-SYSTEM memo
    row = conn.execute("SELECT data FROM receipts WHERE txn_id = 'TXN-SYSTEM'").fetchone()
    if row:
        obj = json.loads(row[0])
        obj['memo'] = make_flag('crypto_ecb')
        conn.execute("UPDATE receipts SET data = ? WHERE txn_id = 'TXN-SYSTEM'", (json.dumps(obj),))
    conn.commit()
    conn.close()

# Flag: ssti — read via Jinja2 SSTI in /account/profile
os.makedirs('/opt/vbank', exist_ok=True)
with open('/opt/vbank/ssti_flag.txt', 'w') as f:
    f.write(make_flag('ssti') + '\n')

# Flag: path_traversal — read via /staff/files?name=....//logs/path_flag.txt
os.makedirs('/opt/vbank/reports', exist_ok=True)
os.makedirs('/opt/vbank/logs', exist_ok=True)
with open('/opt/vbank/logs/path_flag.txt', 'w') as f:
    f.write(make_flag('path_traversal') + '\n')

# Seed dummy report files
for _rpt in ['q1_audit_report.txt', 'q4_2023_review.txt', 'annual_summary_2023.txt']:
    _rpt_path = f'/opt/vbank/reports/{_rpt}'
    if not os.path.exists(_rpt_path):
        with open(_rpt_path, 'w') as f:
            f.write(f'vBank Internal Report — {_rpt}\nGenerated: 2024\nClassification: INTERNAL\n\n[Content restricted to authorised personnel]\n')

print(f"[entrypoint] Dynamic flags written for team: {team}")
PYEOF

exec python app.py
