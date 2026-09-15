import requests
import re

base = 'http://154.57.164.80:31506'

# Let's check the CSP more carefully and look for the message.js script
s = requests.Session()
s.post(f'{base}/login', data={'username': 'hacktest77', 'password': 'pass123'})

# Get message.js
r = s.get(f'{base}/assets/message.js')
print("=== message.js ===")
print(r.text)

# Check if there's a /report endpoint (common in XSS CTFs) or any bot-related endpoints  
for path in ['/report', '/report-issue', '/support', '/contact', '/admin/messages', 
             '/api/report', '/bot', '/flag', '/admin', '/admin/login']:
    r = s.get(f'{base}{path}', allow_redirects=False)
    if r.status_code != 404:
        print(f'\n{path}: status={r.status_code}')
        if r.status_code == 200:
            print(f'  Body: {r.text[:500]}')
        elif r.status_code == 302:
            print(f'  Redirect: {r.headers.get("Location", "")}')

# Also check the CSP details
r = s.get(f'{base}/')
csp = r.headers.get('Content-Security-Policy', '')
print(f'\n=== CSP ===\n{csp}')

# Check what happens when we login as admin (with no password or wrong password)
# to see error message that might reveal info
s2 = requests.Session()
r = s2.post(f'{base}/login', data={'username': 'admin', 'password': 'wrongpass'}, allow_redirects=False)
print(f'\n=== LOGIN AS ADMIN (wrong pass) ===')
print(f'Status: {r.status_code}')
print(f'Headers: {dict(r.headers)}')
print(f'Body: {r.text[:500]}')

# Check what admin's inbox looks like by trying to login with various passwords
admin_passwords = ['admin', 'password', 'flag', 'rookery', 'admin123', 'secret', 'letmein']
for pwd in admin_passwords:
    s3 = requests.Session()
    r = s3.post(f'{base}/login', data={'username': 'admin', 'password': pwd}, allow_redirects=False)
    if r.status_code == 302 and r.headers.get('Location') == '/':
        print(f'\n  ADMIN LOGIN SUCCESS with password: {pwd}')
        r2 = s3.get(f'{base}/')
        print(f'  Inbox: {r2.text[:500]}')
        break
