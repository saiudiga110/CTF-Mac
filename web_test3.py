import requests
import re
import sys

base = 'http://154.57.164.80:31506'

# Register and login
print("=== REGISTERING AND LOGGING IN ===")
s = requests.Session()
r = s.post(f'{base}/register', data={'username': 'hacktest77', 'password': 'pass123'}, allow_redirects=False)
print(f"Register: {r.status_code}")

r = s.post(f'{base}/login', data={'username': 'hacktest77', 'password': 'pass123'}, allow_redirects=False)
print(f"Login: {r.status_code} -> {r.headers.get('Location','')}")

# Get inbox
r = s.get(f'{base}/')
print(f"\n=== INBOX ===")
print(r.text[:500])

# Send a message to self
r = s.post(f'{base}/messages', data={'to_username': 'hacktest77', 'content': 'hello test'}, allow_redirects=False)
print(f"\nSend message: {r.status_code}")

# Get inbox again
r = s.get(f'{base}/')
ids = re.findall(r'/messages/(\d+)', r.text)
print(f"Message IDs: {ids}")

# Read message
if ids:
    r = s.get(f'{base}/messages/{ids[0]}')
    print(f"\n=== MESSAGE {ids[0]} FULL HTML ===")
    print(r.text)

# Try common usernames
print("\n\n=== USERNAME ENUMERATION ===")
common_users = ['admin', 'root', 'administrator', 'flag', 'rookery', 'raven', 'crow']
for user in common_users:
    r = s.post(f'{base}/messages', data={'to_username': user, 'content': 'ping'}, allow_redirects=False)
    if r.status_code == 302:
        loc = r.headers.get('Location', '')
        r2 = s.get(f'{base}{loc}' if loc.startswith('/') else f'{base}/')
        if 'error' in r2.text.lower() or 'not found' in r2.text.lower() or 'does not exist' in r2.text.lower():
            print(f"  {user}: NOT FOUND")
        else:
            print(f"  {user}: MIGHT EXIST (redirect to {loc})")
    else:
        print(f"  {user}: status={r.status_code}")
        if r.status_code != 200:
            print(f"    Body: {r.text[:200]}")

sys.stdout.flush()
