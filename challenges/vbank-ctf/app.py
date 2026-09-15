#!/usr/bin/env python3
"""
vBank — Online Banking Portal
Release: v2.4.1 | Sprint 11 | Internal use only

Dev handover notes (Jake Thompson, contractor):
  - Customer login: string-formatted SQL still in place for legacy compat — TD-289
  - Staff portal: WAF only covers emp_id field, not password — TD-412
  - Maintenance API: must disable before prod — ticket #4421
  - Analytics merge proxies to internal Node service, not validated
"""

import os
import hashlib
import hmac as _hmac
import sqlite3
import subprocess
import base64
import pickle
import json
import threading
import time as _time
from datetime import timedelta

from lxml import etree
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

import jwt
import requests as http_requests

from flask import (
    Flask, request, session, redirect, url_for,
    render_template, send_from_directory, g, jsonify, abort, make_response,
    render_template_string
)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('FLASK_SECRET', 'vbank_portal_dev_key_2024_xK9')
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,
)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

# JWT — API v2. TODO: Rotate to RSA keypair before prod.
JWT_SECRET = os.environ.get('JWT_SECRET', 'vbank2024')

# AES key for receipt encryption — 16 bytes.
AES_KEY = b'vbank_security!!'  # exactly 16 bytes

# Per-team dynamic flag generation
FLAG_SECRET = os.environ.get('FLAG_SECRET', 'vbank_ctf_default_2024')

ANALYTICS_BASE_URL = os.environ.get('ANALYTICS_BASE_URL', 'http://vbank-analytics:3000').rstrip('/')
CHALLENGE_KEY = os.environ.get('CHALLENGE_KEY', '').strip()
TARGET_PROFILE = os.environ.get('TARGET_PROFILE', 'all').strip() or 'all'

from isolation import path_allowed as _path_allowed


def make_flag(challenge: str) -> str:
    team_id = os.environ.get('TEAM_ID', '0')
    h = _hmac.new(
        FLAG_SECRET.encode('utf-8'),
        f"{team_id}:{challenge}".encode('utf-8'),
        hashlib.sha256
    )
    flag = f"LYD{{{h.hexdigest()[:24]}}}"
    if CHALLENGE_KEY and challenge != CHALLENGE_KEY:
        return 'LYD{redacted}'
    return flag


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        try:
            g.db.execute('PRAGMA journal_mode=WAL')
        except Exception:
            pass
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def check_customer():
    """Validate customer portal session."""
    user = session.get('customer_user')
    if not user:
        return redirect('/customer/login')
    return user


def check_session():
    """Validate staff/employee portal session."""
    user = session.get('login_user')
    if not user:
        return redirect('/staff-portal')
    return user


def check_admin():
    """Returns username if staff user is in admin table, else None."""
    login_user = session.get('login_user')
    if not login_user:
        return None
    db = get_db()
    row = db.execute("SELECT username FROM admin WHERE username = ?", (login_user,)).fetchone()
    return row['username'] if row else None


@app.before_request
def make_session_permanent():
    session.permanent = True


@app.before_request
def enforce_challenge_isolation():
    if request.endpoint == 'static':
        return None
    if not _path_allowed(request.path, CHALLENGE_KEY, TARGET_PROFILE):
        abort(404)
    return None


def analytics_request(method, path, **kwargs):
    try:
        resp = http_requests.request(
            method,
            f"{ANALYTICS_BASE_URL}{path}",
            timeout=kwargs.pop('timeout', 5),
            **kwargs,
        )
    except http_requests.RequestException as exc:
        return jsonify({'error': 'Analytics service unavailable', 'details': str(exc)}), 502
    try:
        payload = resp.json()
    except ValueError:
        payload = {'raw': resp.text}
    return jsonify(payload), resp.status_code


# ===========================================================================
# LANDING PAGE
# ===========================================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/robots.txt')
def robots():
    content = (
        "User-agent: *\n"
        "Disallow: /staff-portal/\n"
        "Disallow: /staff/\n"
        "Disallow: /api/internal/\n"
        "Disallow: /api/v2/corporate/\n"
    )
    return make_response(content, 200, {'Content-Type': 'text/plain'})


# ===========================================================================
# CUSTOMER AUTH
# ===========================================================================

@app.route('/customer/login', methods=['GET', 'POST'])
def customer_login():
    """
    Customer portal login.
    INTENTIONALLY VULNERABLE: SQL injection in username field.
    INTENTIONALLY VULNERABLE: Unvalidated ?next= redirect parameter (open redirect).
    Password MD5-hashed, username raw string-formatted.
    FIXME: Parameterize — Tech Debt TD-289 (Jake)
    FIXME: Validate next= against allowlist — Tech Debt TD-614 (Jake)
    """
    error = ''
    next_url = request.args.get('next', '') or request.form.get('next', '')
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        pwd_md5  = hashlib.md5(password.encode()).hexdigest()

        # FIXME: string-formatted SQL — legacy compat, parameterize before prod
        sql = ("SELECT id, username, full_name, account_number, acct_user_id, balance "
               "FROM customers WHERE username='%s' AND password='%s'" % (username, pwd_md5))

        db = get_db()
        try:
            rows = db.execute(sql).fetchall()
            if rows:
                session.permanent = True
                session['customer_user']     = rows[0]['username']
                session['customer_name']     = rows[0]['full_name']
                session['customer_acct']     = rows[0]['account_number']
                session['customer_acct_uid'] = rows[0]['acct_user_id']
                session['customer_balance']  = rows[0]['balance']
                # INTENTIONALLY VULNERABLE: next= is not validated — open redirect
                # FIXME: TD-614 — must allowlist /account/dashboard and relative paths only
                if next_url:
                    resp = make_response('', 302)
                    resp.headers['Location'] = next_url
                    # External redirect detected — include audit token in body (visible in proxy tools)
                    if next_url.startswith('http') or next_url.startswith('//'):
                        flag_val = make_flag('open_redirect')
                        resp.set_data(
                            f'<html><body>Redirecting to external destination.<br>'
                            f'<!-- vBank-Security-Audit: {flag_val} --></body></html>'
                        )
                    return resp
                return redirect('/account/dashboard')
            else:
                error = 'Invalid username or password. Please try again.'
        except Exception as e:
            print(f"[CUSTOMER-AUTH-DEBUG] {e}")
            error = 'Authentication service error. Please try again.'

    return render_template('customer_login.html', error=error, next_url=next_url)


@app.route('/customer/logout')
def customer_logout():
    session.pop('customer_user', None)
    session.pop('customer_name', None)
    session.pop('customer_acct', None)
    session.pop('customer_acct_uid', None)
    session.pop('customer_balance', None)
    return redirect('/')


# ===========================================================================
# CUSTOMER DASHBOARD — Flag 2: SQL Injection Login Bypass
# ===========================================================================

@app.route('/account/dashboard')
def customer_dashboard():
    result = check_customer()
    if not isinstance(result, str):
        return result

    customer = {
        'username':    session.get('customer_user'),
        'name':        session.get('customer_name', result),
        'account':     session.get('customer_acct', 'VBK-XXXXX'),
        'acct_uid':    session.get('customer_acct_uid', 0),
        'balance':     session.get('customer_balance', 0.0),
    }
    return render_template('customer_dashboard.html', customer=customer, flag2=make_flag('sqli_login'))


# ===========================================================================
# CUSTOMER SEARCH — Flag: reflected_xss (X-Internal-Note header)
# ===========================================================================

@app.route('/search')
def customer_search():
    """INTENTIONALLY VULNERABLE: reflected XSS via unsanitised q."""
    result = check_customer()
    if not isinstance(result, str):
        return result
    q = request.args.get('q', '')
    resp = make_response(render_template('search.html', q=q))
    lowered = q.lower()
    if '<script' in lowered or 'javascript:' in lowered or 'onerror=' in lowered or 'onload=' in lowered:
        resp.headers['X-Internal-Note'] = make_flag('reflected_xss')
    return resp


# ===========================================================================
# CUSTOMER WIRE TRANSFER — Flag 3: IDOR (double base64 account encoding)
# ===========================================================================

@app.route('/account/transfer')
def customer_transfer():
    result = check_customer()
    if not isinstance(result, str):
        return result
    return render_template('customer_transfer.html')


@app.route('/account/transfer/confirm', methods=['GET', 'POST'])
def customer_transfer_confirm():
    result = check_customer()
    if not isinstance(result, str):
        return result

    acct = request.args.get('acct', '') or request.form.get('acct', '')
    recipient = (request.form.get('recipient', '') or request.args.get('recipient', '')).strip()
    compliance = (request.form.get('compliance_status', '') or request.args.get('compliance_status', '')).strip().lower()

    valid_accounts = [
        'TVRnMU5EVTJNVFk0TVRJPQ==',
        'TVRnMU9ESXhNakU0TVRVPQ==',
        'TVRNMk9EVTNPRGs0TVRnPQ==',
        'TVRjNE5URTFOalE0TnpjPQ==',
    ]
    flagged_account = 'TVRneU5EVXpOamN4TUE9PQ=='

    message    = ''
    alert_type = 'success'

    if recipient.upper() == 'KADABOOM' and compliance in ('approved', 'accepted'):
        alert_type = 'danger'
        message = (
            "COMPLIANCE ALERT: Transfer to flagged account KADABOOM processed. "
            f"Transaction flagged for AML review. Audit token: {make_flag('idor_transfer')}"
        )
    elif acct in valid_accounts:
        try:
            decoded = base64.b64decode(base64.b64decode(acct)).decode()
        except Exception:
            decoded = acct
        message = f"Wire transfer confirmed. Funds dispatched to account {decoded}."
    elif acct == flagged_account:
        try:
            decoded = base64.b64decode(base64.b64decode(acct)).decode()
        except Exception:
            decoded = acct
        alert_type = 'danger'
        message = (
            f"COMPLIANCE ALERT: Transfer to flagged account {decoded} processed. "
            f"Transaction flagged for AML review. Audit token: {make_flag('idor_transfer')}"
        )
    else:
        alert_type = 'warning'
        message = "Transfer failed: beneficiary account not recognised. Please verify account details."

    return render_template('customer_transfer_result.html', message=message, alert_type=alert_type)


# ===========================================================================
# CUSTOMER STATEMENTS — Flag 4 (IDOR): no ownership check on /api/v1/accounts
# ===========================================================================

@app.route('/account/statements')
def customer_statements():
    result = check_customer()
    if not isinstance(result, str):
        return result
    acct_uid = session.get('customer_acct_uid', 0)
    return render_template('customer_statements.html', acct_uid=acct_uid)


@app.route('/api/v1/accounts/<int:user_id>/transactions')
def api_account_transactions(user_id):
    """
    INTENTIONALLY VULNERABLE: IDOR
    Authenticated customers can query ANY user_id. No ownership check.
    CEO account (user_id=1337) contains confidential transaction memo with flag.
    """
    if not session.get('customer_user') and not session.get('login_user'):
        return jsonify({'error': 'Authentication required.'}), 401

    db  = get_db()
    rows = db.execute(
        "SELECT txn_date, description, amount, memo FROM statements WHERE user_id = ?",
        (user_id,)
    ).fetchall()

    if not rows:
        return jsonify({'user_id': user_id, 'transactions': [], 'message': 'No transactions found.'})

    acct_row = db.execute(
        "SELECT account_name FROM statements WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()

    transactions = []
    for row in rows:
        txn = {'date': row['txn_date'], 'description': row['description'], 'amount': row['amount']}
        memo = row['memo']
        if memo and memo.startswith('FLAG:'):
            memo = f"FLAG: {make_flag('idor_statements')}"
        if memo:
            txn['memo'] = memo
        transactions.append(txn)

    return jsonify({
        'user_id':      user_id,
        'account_name': acct_row['account_name'] if acct_row else 'Unknown',
        'transactions': transactions
    })


# ===========================================================================
# CUSTOMER SUPPORT TICKETS — Flag 10: Stored XSS → Cookie Theft
# ===========================================================================

@app.route('/account/support', methods=['GET', 'POST'])
def customer_support():
    result = check_customer()
    if not isinstance(result, str):
        return result

    msg = None
    if request.method == 'POST':
        message  = request.form.get('message', '')
        category = request.form.get('category', 'General')
        if message:
            db = get_db()
            db.execute(
                "INSERT INTO feedback (username, message) VALUES (?, ?)",
                (result, f"[{category}] {message}")
            )
            db.commit()
            msg = 'Ticket submitted. Our support team will review it shortly (SLA: 24h).'

    return render_template('customer_support.html', msg=msg)


# ===========================================================================
# CUSTOMER EXPRESS TRANSFER — Flag 11: Race Condition (TOCTOU)
# ===========================================================================

@app.route('/account/express-transfer')
def customer_express_transfer():
    result = check_customer()
    if not isinstance(result, str):
        return result

    db  = get_db()
    row = db.execute("SELECT balance FROM accounts WHERE username = ?", (result,)).fetchone()
    if not row:
        db.execute("INSERT INTO accounts (username, balance) VALUES (?, 1000.00)", (result,))
        db.commit()
        balance = 1000.00
    else:
        balance = row['balance']

    return render_template('customer_express.html', balance=balance)


@app.route('/api/v1/transfer/express', methods=['POST'])
def api_express_transfer():
    """INTENTIONALLY VULNERABLE: TOCTOU race condition."""
    result = check_customer()
    if not isinstance(result, str):
        return jsonify({'error': 'Not authenticated'}), 401

    username = result
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    if amount <= 0 or amount > 10000:
        return jsonify({'error': 'Amount must be between £1 and £10,000'}), 400

    db  = get_db()
    row = db.execute("SELECT balance FROM accounts WHERE username = ?", (username,)).fetchone()
    if not row:
        db.execute("INSERT INTO accounts (username, balance) VALUES (?, 1000.00)", (username,))
        db.commit()
        row = db.execute("SELECT balance FROM accounts WHERE username = ?", (username,)).fetchone()

    current = row['balance']
    if current < amount:
        return jsonify({'error': 'Insufficient funds', 'balance': current}), 400

    # INTENTIONALLY VULNERABLE: artificial delay widens TOCTOU race window.
    # Deduct from the live ledger so concurrent checks can overdraw.
    _time.sleep(0.3)

    db.execute("UPDATE accounts SET balance = balance - ? WHERE username = ?", (amount, username))
    db.commit()
    row = db.execute("SELECT balance FROM accounts WHERE username = ?", (username,)).fetchone()
    new_balance = row['balance'] if row else current - amount

    return jsonify({
        'success':          True,
        'transferred':      amount,
        'previous_balance': current,
        'current_balance':  new_balance
    })


@app.route('/api/v1/account/status')
def api_account_status():
    result = check_customer()
    if not isinstance(result, str):
        return jsonify({'error': 'Not authenticated'}), 401

    db  = get_db()
    row = db.execute("SELECT balance FROM accounts WHERE username = ?", (result,)).fetchone()
    balance = row['balance'] if row else 1000.00

    resp = {'username': result, 'balance': balance}
    if balance < 0:
        resp['flag']  = make_flag('race_condition')
        resp['alert'] = 'COMPLIANCE ALERT: Account overdrawn. Concurrent transaction anomaly detected.'
    return jsonify(resp)


@app.route('/api/v1/account/reset', methods=['POST'])
def api_account_reset():
    result = check_customer()
    if not isinstance(result, str):
        return jsonify({'error': 'Not authenticated'}), 401

    db = get_db()
    db.execute("UPDATE accounts SET balance = 1000.00 WHERE username = ?", (result,))
    db.commit()
    return jsonify({'success': True, 'balance': 1000.00})


# ===========================================================================
# CUSTOMER RECEIPTS — Flag 12: Broken Crypto (AES-ECB)
# ===========================================================================

@app.route('/account/receipts')
def customer_receipts():
    result = check_customer()
    if not isinstance(result, str):
        return result
    return render_template('customer_receipt.html')


@app.route('/api/v1/receipts/<txn_id>')
def api_get_receipt(txn_id):
    """INTENTIONALLY VULNERABLE: AES-ECB encryption."""
    if not session.get('customer_user') and not session.get('login_user'):
        return jsonify({'error': 'Not authenticated'}), 401

    db  = get_db()
    row = db.execute("SELECT data FROM receipts WHERE txn_id = ?", (txn_id,)).fetchone()
    if not row:
        return jsonify({'error': f'Receipt {txn_id} not found'}), 404

    data = row['data']
    if 'LYD{see_make_flag_in_app_py}' in data or txn_id == 'TXN-SYSTEM':
        import json as _json
        obj = _json.loads(data)
        obj['memo'] = make_flag('crypto_ecb')
        data = _json.dumps(obj)
    plaintext = data.encode('utf-8')
    cipher    = AES.new(AES_KEY, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

    return jsonify({
        'txn_id':            txn_id,
        'encrypted_receipt': base64.b64encode(encrypted).decode(),
        'algorithm':         'AES-ECB',
        'block_size':        16,
        'key_hint':          'Key is 16 bytes: bank name + underscore + "security" + two exclamation marks',
        'note':              'ECB mode chosen for processing throughput. Review in hardening sprint.'
    })


@app.route('/api/v1/receipts/decrypt', methods=['POST'])
def api_receipt_decrypt():
    if not session.get('customer_user') and not session.get('login_user'):
        return jsonify({'error': 'Not authenticated'}), 401

    ct_b64 = request.form.get('ciphertext', '')
    key    = request.form.get('key', '')

    if not ct_b64 or not key:
        return jsonify({'error': 'ciphertext and key required'}), 400

    try:
        key_bytes = key.encode('utf-8')
        if len(key_bytes) != 16:
            return jsonify({'error': 'Key must be exactly 16 bytes'}), 400
        ct = base64.b64decode(ct_b64)
        cipher = AES.new(key_bytes, AES.MODE_ECB)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return jsonify({'decrypted': pt.decode('utf-8')})
    except Exception as e:
        return jsonify({'error': f'Decryption failed: {str(e)}'}), 400


# ===========================================================================
# HIDDEN EMPLOYEE PORTAL — Flag 1: Discovering the hidden staff login page
# ===========================================================================

@app.route('/staff-portal', methods=['GET', 'POST'])
def staff_portal_login():
    """
    HIDDEN employee portal. Not linked from public pages.
    Discovery: robots.txt disallows /staff-portal/; HTML comment in index.html hints at path.

    INTENTIONALLY VULNERABLE: SQLi via password field.
    WAF covers emp_id only (Jake's oversight — TD-412).
    Staff passwords stored PLAINTEXT (dev mistake — 'will hash later').

    Flag 1 in HTML comment — awarded for finding this page.
    """
    error = ''
    if request.method == 'POST':
        emp_id   = request.form.get('emp_id', '')
        emp_pass = request.form.get('emp_pass', '') or request.form.get('password', '')

        # Naive WAF — only covers emp_id, not password field
        # FIXME: also sanitize password field — TD-412
        blocked = ['or ', 'union ', 'select ', '--', '#', '/*']
        if any(t in emp_id.lower() for t in blocked):
            error = 'Input validation error. Contact IT if this is a mistake (ext. 4400).'
        else:
            # INTENTIONALLY VULNERABLE: password field unfiltered, plaintext comparison
            # Staff passwords not hashed — "temporary" decision by Jake, ticket #TD-443
            sql = ("SELECT id, emp_id, role FROM staff "
                   "WHERE emp_id='%s' AND password='%s'" % (emp_id, emp_pass))
            db = get_db()
            try:
                rows = db.execute(sql).fetchall()
                if rows:
                    session.permanent = True
                    session['login_user'] = rows[0]['emp_id']
                    return redirect('/staff/dashboard')
                else:
                    error = 'Invalid employee ID or password.'
            except Exception as e:
                print(f"[STAFF-AUTH-DEBUG] {e}")
                error = 'Authentication service error.'

    return render_template('staff_login.html', error=error, flag1=make_flag('source_code'))


@app.route('/maintenance-portal', methods=['GET', 'POST'])
def maintenance_portal_login():
    """Staff maintenance login. INTENTIONALLY VULNERABLE: SQLi via password field."""
    error = ''
    if request.method == 'POST':
        emp_id   = request.form.get('emp_id', '')
        emp_pass = request.form.get('password', '') or request.form.get('emp_pass', '')
        blocked = ['or ', 'union ', 'select ', '--', '#', '/*']
        if any(t in emp_id.lower() for t in blocked):
            error = 'Input validation error. Contact IT if this is a mistake (ext. 4400).'
        else:
            sql = ("SELECT id, emp_id, role FROM staff "
                   "WHERE emp_id='%s' AND password='%s'" % (emp_id, emp_pass))
            db = get_db()
            try:
                rows = db.execute(sql).fetchall()
                if rows:
                    session.permanent = True
                    session['login_user'] = rows[0]['emp_id']
                    return redirect('/staff/dashboard')
                error = 'Invalid employee ID or password.'
            except Exception as e:
                print(f"[MAINT-AUTH-DEBUG] {e}")
                error = 'Authentication service error.'

    return render_template('maintenance_portal.html', error=error)


@app.route('/staff-portal/logout')
def staff_portal_logout():
    session.pop('login_user', None)
    return redirect('/staff-portal')


# ===========================================================================
# EMPLOYEE DASHBOARD
# ===========================================================================

@app.route('/staff/dashboard')
def staff_dashboard():
    result = check_session()
    if not isinstance(result, str):
        return result

    db = get_db()
    ticket_count = db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    return render_template(
        'staff_dashboard.html',
        username=result,
        ticket_count=ticket_count,
        staff_flag=make_flag('staff_sqli'),
    )


# ===========================================================================
# EMPLOYEE MAINTENANCE CONSOLE — Flag 5 (rce): reads /root/flag.txt
# ===========================================================================

@app.route('/staff/maintenance', methods=['GET', 'POST'])
def staff_maintenance():
    """
    Internal system maintenance console.
    INTENTIONALLY VULNERABLE: RCE via subprocess.check_output(shell=True).
    FIXME: Disable before production — DevOps ticket #4421
    """
    result = check_session()
    if not isinstance(result, str):
        return result

    output = ''
    cmd    = ''
    if request.method == 'POST':
        cmd = request.form.get('cmd', '')
        if cmd:
            try:
                output = subprocess.check_output(
                    cmd, shell=True, stderr=subprocess.STDOUT, timeout=10
                ).decode('utf-8', errors='replace')
            except subprocess.CalledProcessError as e:
                output = e.output.decode('utf-8', errors='replace')
            except subprocess.TimeoutExpired:
                output = 'Command timed out (10s limit).'
            except Exception as e:
                output = str(e)

    return render_template('maintenance.html', output=output, cmd=cmd)


# ===========================================================================
# EMPLOYEE API PORTAL — Flag 6 (jwt): JWT alg=none / weak secret
# ===========================================================================

@app.route('/staff/api-docs')
def staff_api_docs():
    result = check_session()
    if not isinstance(result, str):
        return result
    return render_template('api_portal.html')


@app.route('/api/v2/auth/token', methods=['POST'])
def api_v2_token():
    """Issue JWT. Weak secret 'vbank2024' — TODO: rotate before prod."""
    login_user = session.get('login_user')
    if not login_user:
        return jsonify({'error': 'Sign in required'}), 401

    payload = {'sub': login_user, 'role': 'employee', 'iss': 'vbank-api-v2', 'dept': 'general'}
    token   = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
    return jsonify({'access_token': token, 'token_type': 'Bearer',
                    'note': 'Include in Authorization header: Bearer <token>'})


@app.route('/api/v2/corporate/vault')
def api_v2_corporate_vault():
    """INTENTIONALLY VULNERABLE: JWT accepts alg=none, weak secret."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Missing Authorization header. Format: Bearer <token>'}), 401

    token = auth[7:]
    try:
        payload = jwt.decode(
            token, JWT_SECRET,
            algorithms=['HS256', 'none'],
            options={"verify_signature": False}
        )
    except jwt.InvalidTokenError as e:
        return jsonify({'error': f'Invalid token: {str(e)}'}), 401

    if payload.get('role') != 'admin':
        return jsonify({
            'error':     'Insufficient privileges. Corporate vault requires admin role.',
            'your_role': payload.get('role', 'unknown'),
            'hint':      'Escalate your token role to access this endpoint.'
        }), 403

    return jsonify({
        'status':  'authorised',
        'message': 'Corporate vault access granted.',
        'flag':    make_flag('jwt'),
        'vault_summary': {
            'total_assets_usd': '847,293,125.00',
            'classification':   'CONFIDENTIAL — BOARD ONLY',
            'last_audit':       '2024-01-15'
        }
    })


@app.route('/api/v2/profile')
def api_v2_profile():
    """Decode JWT payload (debug utility — disable in prod)."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Missing Authorization header'}), 401
    token = auth[7:]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return jsonify({'decoded_payload': payload})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ===========================================================================
# EMPLOYEE CREDIT STATUS — Flag 7 (blind_sqli): Blind Boolean SQL Injection
# ===========================================================================

@app.route('/staff/credit-status', methods=['GET', 'POST'])
def staff_credit_status():
    """INTENTIONALLY VULNERABLE: raw string-formatted SQL, blind boolean injection."""
    result = check_session()
    if not isinstance(result, str):
        return result

    status_msg = ''
    ref_input  = ''

    if request.method == 'POST':
        ref_input = request.form.get('reference_id', '')
        # FIXME: parameterize — Jake's legacy code, deferred to Q2 refactor (TD-290)
        sql = "SELECT approved FROM loans WHERE loan_id = '%s'" % ref_input
        db  = get_db()
        try:
            row = db.execute(sql).fetchone()
            if row and row['approved'] == 1:
                status_msg = 'APPROVED'
            elif row:
                status_msg = 'UNDER REVIEW'
            else:
                status_msg = 'NOT FOUND'
        except Exception as e:
            print(f"[CREDIT-DEBUG] {e}")
            status_msg = 'SERVICE ERROR'

    return render_template('loan_status.html', status_msg=status_msg, ref_input=ref_input)


@app.route('/staff/vault', methods=['GET', 'POST'])
def staff_vault():
    """Verify extracted admin PIN. Returns flag if correct."""
    result = check_session()
    if not isinstance(result, str):
        return result

    if request.method == 'POST':
        pin = request.form.get('pin', '')
        db  = get_db()
        row = db.execute("SELECT value FROM secrets WHERE key = 'admin_pin'").fetchone()
        if row and pin == row['value']:
            return render_template('pin_result.html', success=True, flag=make_flag('blind_sqli'))
        return render_template('pin_result.html', success=False, flag=None)

    return render_template('pin_verify.html')


# ===========================================================================
# EMPLOYEE SUPPORT TOOLS — Flag 8 (ssrf_deser): SSRF → Pickle Chain
# ===========================================================================

@app.route('/staff/support-tools')
def staff_support_tools():
    result = check_session()
    if not isinstance(result, str):
        return result
    return render_template('support.html')


@app.route('/support/document-fetch', methods=['POST'])
def support_document_fetch():
    """INTENTIONALLY VULNERABLE: SSRF with trivially bypassed blocklist."""
    if not session.get('login_user') and not session.get('customer_user'):
        return jsonify({'error': 'Authentication required'}), 401

    url = request.form.get('url', '')
    if not url:
        return jsonify({'error': 'url parameter is required'}), 400

    blocked   = ['localhost', '127.0.0.1']
    url_lower = url.lower()
    for b in blocked:
        if b in url_lower:
            return jsonify({'error': 'Blocked: internal addresses are not permitted.'}), 403

    try:
        resp = http_requests.get(url, timeout=5, allow_redirects=False)
        ct   = resp.headers.get('Content-Type', '')
        if 'json' in ct or resp.text.strip().startswith('{'):
            try:
                return jsonify({'url': url, 'status': resp.status_code, 'data': resp.json()})
            except Exception:
                pass
        return jsonify({'url': url, 'status': resp.status_code, 'content_type': ct, 'data': resp.text[:5000]})
    except http_requests.exceptions.ConnectionError:
        return jsonify({'error': 'Connection failed.'}), 502
    except http_requests.exceptions.Timeout:
        return jsonify({'error': 'Request timed out.'}), 504
    except Exception as e:
        return jsonify({'error': f'Request failed: {str(e)}'}), 500


@app.route('/api/internal/debug')
def internal_debug():
    """
    Internal debug endpoint — SSRF target.
    Accessible only from localhost. Leaks serialized config blob.
    FIXME: Remove before production (DevOps ticket #4421)
    """
    remote = request.remote_addr
    if remote not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        abort(403)

    config_data = {
        'version':     '2.4.1',
        'debug_mode':  True,
        'internal_api_key': 'sk-vbank-internal-4f8a2c9b',
        'admin_credentials': {
            'username':      'sysadmin',
            'password_hash': hashlib.md5(b'vBank@Sup3rS3cr3t!').hexdigest()
        },
        'flag': make_flag('ssrf'),
        'note': 'Import this config via POST /api/internal/config-import (base64 pickle format)'
    }

    pickled = base64.b64encode(pickle.dumps(config_data)).decode()
    return jsonify({
        'status':            'debug',
        'message':           'Internal debug interface — authorised personnel only',
        'server_info':       {'python': '3.9', 'framework': 'Flask 3.0', 'db': 'SQLite3'},
        'serialized_config': pickled,
        'format':            'python-pickle-base64',
        'import_endpoint':   '/api/internal/config-import'
    })


@app.route('/api/internal/config-import', methods=['POST'])
def internal_config_import():
    """INTENTIONALLY VULNERABLE: Insecure deserialization (pickle)."""
    if not session.get('login_user'):
        return jsonify({'error': 'Staff authentication required'}), 401

    config_b64 = request.form.get('config_data', '')
    if not config_b64:
        return jsonify({'error': 'config_data (base64 pickle) is required'}), 400

    try:
        raw    = base64.b64decode(config_b64)
        config = pickle.loads(raw)
        return jsonify({
            'status':  'imported',
            'message': 'Configuration applied successfully.',
            'config':  config if isinstance(config, dict) else str(config)
        })
    except Exception as e:
        return jsonify({'error': f'Import failed: {str(e)}'}), 400


# ===========================================================================
# EMPLOYEE PAYROLL IMPORT — Flag 9 (xxe): XXE Injection
# ===========================================================================

@app.route('/staff/payroll', methods=['GET', 'POST'])
def staff_payroll():
    """INTENTIONALLY VULNERABLE: XXE via lxml with resolve_entities=True."""
    result = check_session()
    if not isinstance(result, str):
        return result

    import_result = None
    if request.method == 'POST':
        xml_data = request.form.get('xml_data', '')
        if not xml_data:
            f = request.files.get('xml_file')
            if f:
                xml_data = f.read().decode('utf-8', errors='replace')

        if not xml_data:
            import_result = {'error': 'No XML payload provided'}
        else:
            try:
                # INTENTIONALLY VULNERABLE: resolve_entities=True, no_network=False
                parser = etree.XMLParser(
                    resolve_entities=True,
                    dtd_validation=False,
                    load_dtd=True,
                    no_network=False
                )
                tree    = etree.fromstring(xml_data.encode('utf-8'), parser)
                records = []
                for rec in tree.findall('.//employee'):
                    records.append({
                        'id':         rec.findtext('id', ''),
                        'name':       rec.findtext('name', ''),
                        'department': rec.findtext('department', ''),
                        'salary':     rec.findtext('salary', ''),
                        'note':       rec.findtext('note', ''),
                    })
                import_result = {'success': True, 'count': len(records), 'records': records}
            except Exception as e:
                import_result = {'error': f'XML parse error: {str(e)}'}

    return render_template('import_transactions.html', result=import_result)


# ===========================================================================
# EMPLOYEE STAFF TICKETS — Flag 10 (xss): Stored XSS → Cookie Theft
# ===========================================================================

@app.route('/staff/tickets')
def staff_tickets():
    """INTENTIONALLY VULNERABLE: Renders user-submitted content unescaped."""
    result = check_session()
    if not isinstance(result, str):
        return result

    db   = get_db()
    rows = db.execute(
        "SELECT username, message, created_at FROM feedback ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return render_template('staff_reviews.html', tickets=rows, xss_flag=make_flag('xss'))


# ===========================================================================
# EMPLOYEE SETTINGS — Flag 13 (proto): Prototype Pollution
# ===========================================================================

@app.route('/staff/settings')
def staff_settings():
    result = check_session()
    if not isinstance(result, str):
        return result
    return render_template('settings.html', analytics_url=ANALYTICS_BASE_URL)


@app.route('/api/settings/current')
def api_settings_current():
    if not session.get('login_user'):
        return jsonify({'error': 'Not authenticated'}), 401
    return analytics_request('GET', '/api/config/current')


@app.route('/api/settings/merge', methods=['POST'])
def api_settings_merge():
    """Proxies to analytics microservice. INTENTIONALLY VULNERABLE to prototype pollution."""
    if not session.get('login_user'):
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        user_data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    if not isinstance(user_data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400

    return analytics_request('POST', '/api/config/merge', json=user_data)


@app.route('/api/settings/admin-panel')
def api_settings_admin_panel():
    if not session.get('login_user'):
        return jsonify({'error': 'Not authenticated'}), 401
    return analytics_request('GET', '/api/config/admin-panel')


@app.route('/api/settings/reset', methods=['POST'])
def api_settings_reset():
    return analytics_request('POST', '/api/config/reset')


# ===========================================================================
# ADMIN DASHBOARD
# ===========================================================================

@app.route('/admin')
def admin_dashboard():
    admin_user = check_admin()
    if not admin_user:
        return redirect('/staff-portal')
    return render_template('admin_dashboard.html', admin_user=admin_user)


@app.route('/admin/api/pending-users')
def admin_get_pending_users():
    if not check_admin():
        return jsonify({'error': 'Admin access required'}), 403
    db    = get_db()
    rows  = db.execute(
        "SELECT id, username, email, requested_at, status, notes FROM pending_registrations "
        "WHERE status = 'pending' ORDER BY requested_at DESC"
    ).fetchall()
    pending = [dict(row) for row in rows]
    return jsonify({'pending_users': pending, 'count': len(pending)})


@app.route('/admin/api/users', methods=['POST'])
def admin_create_user():
    admin_user = check_admin()
    if not admin_user:
        return jsonify({'error': 'Admin access required'}), 403

    data        = request.get_json() or {}
    username    = data.get('username', '').strip()
    password    = data.get('password', '').strip()
    email       = data.get('email', '').strip()
    auto_approve = data.get('auto_approve', False)

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400
    if len(username) < 3 or len(password) < 4:
        return jsonify({'error': 'username ≥3 chars, password ≥4 chars'}), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify({'error': f'User {username} already exists'}), 409

    try:
        pwd_md5 = hashlib.md5(password.encode()).hexdigest()
        if auto_approve:
            db.execute(
                "INSERT INTO users (username, password, is_approved, approved_at, created_by) "
                "VALUES (?, ?, 1, datetime('now'), ?)",
                (username, pwd_md5, admin_user)
            )
            db.commit()
            return jsonify({'success': True, 'message': f'User {username} created and approved', 'auto_approved': True}), 201
        else:
            db.execute(
                "INSERT INTO pending_registrations (username, password, email, status) VALUES (?, ?, ?, 'pending')",
                (username, pwd_md5, email)
            )
            db.commit()
            return jsonify({'success': True, 'message': f'Pending registration created for {username}', 'requires_approval': True}), 201
    except Exception as e:
        return jsonify({'error': f'Failed: {str(e)}'}), 500


@app.route('/admin/api/users/bulk', methods=['POST'])
def admin_create_users_bulk():
    admin_user = check_admin()
    if not admin_user:
        return jsonify({'error': 'Admin access required'}), 403

    content_type = request.content_type or ''
    auto_approve = request.args.get('auto_approve', 'false').lower() == 'true'
    users_data   = []
    results      = {'created': [], 'failed': []}

    try:
        if 'application/json' in content_type:
            users_data = request.get_json() or []
            if not isinstance(users_data, list):
                return jsonify({'error': 'Expected JSON array'}), 400
        else:
            text = request.get_data(as_text=True)
            for line in text.strip().split('\n'):
                if line.strip():
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 2:
                        users_data.append({
                            'username': parts[0],
                            'password': parts[1],
                            'email':    parts[2] if len(parts) > 2 else ''
                        })

        if not users_data:
            return jsonify({'error': 'No users provided'}), 400

        db = get_db()
        for u in users_data:
            username = u.get('username', '').strip()
            password = u.get('password', '').strip()
            email    = u.get('email', '').strip()

            if not username or not password:
                results['failed'].append({'username': username or 'unknown', 'error': 'Missing fields'})
                continue
            if len(username) < 3 or len(password) < 4:
                results['failed'].append({'username': username, 'error': 'Validation failed'})
                continue

            try:
                if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
                    results['failed'].append({'username': username, 'error': 'Already exists'})
                    continue

                pwd_md5 = hashlib.md5(password.encode()).hexdigest()
                if auto_approve:
                    db.execute(
                        "INSERT INTO users (username, password, is_approved, approved_at, created_by) "
                        "VALUES (?, ?, 1, datetime('now'), ?)",
                        (username, pwd_md5, admin_user)
                    )
                else:
                    db.execute(
                        "INSERT INTO pending_registrations (username, password, email, status) "
                        "VALUES (?, ?, ?, 'pending')",
                        (username, pwd_md5, email)
                    )
                results['created'].append({'username': username, 'status': 'approved' if auto_approve else 'pending'})
            except Exception as e:
                results['failed'].append({'username': username, 'error': str(e)})

        db.commit()
        return jsonify({
            'success': True,
            'total':   len(users_data),
            'created': len(results['created']),
            'failed':  len(results['failed']),
            'results': results
        }), 201

    except Exception as e:
        return jsonify({'error': f'Bulk import failed: {str(e)}'}), 500


@app.route('/admin/api/users/<int:user_id>/approve', methods=['POST'])
def admin_approve_user(user_id):
    admin_user = check_admin()
    if not admin_user:
        return jsonify({'error': 'Admin access required'}), 403

    db      = get_db()
    pending = db.execute(
        "SELECT id, username, password, email FROM pending_registrations WHERE id = ?", (user_id,)
    ).fetchone()
    if not pending:
        return jsonify({'error': 'Pending registration not found'}), 404

    try:
        db.execute(
            "INSERT INTO users (username, password, is_approved, approved_at, created_by) VALUES (?, ?, 1, datetime('now'), ?)",
            (pending['username'], pending['password'], admin_user)
        )
        db.execute("UPDATE pending_registrations SET status = 'approved' WHERE id = ?", (user_id,))
        db.commit()
        return jsonify({'success': True, 'message': f'User {pending["username"]} approved'}), 200
    except Exception as e:
        return jsonify({'error': f'Approval failed: {str(e)}'}), 500


@app.route('/admin/api/users/<int:user_id>/reject', methods=['POST'])
def admin_reject_user(user_id):
    admin_user = check_admin()
    if not admin_user:
        return jsonify({'error': 'Admin access required'}), 403

    data    = request.get_json() or {}
    reason  = data.get('reason', 'Rejected by admin').strip()
    db      = get_db()
    pending = db.execute(
        "SELECT id, username FROM pending_registrations WHERE id = ?", (user_id,)
    ).fetchone()
    if not pending:
        return jsonify({'error': 'Pending registration not found'}), 404

    try:
        db.execute(
            "UPDATE pending_registrations SET status = 'rejected', notes = ? WHERE id = ?",
            (reason, user_id)
        )
        db.commit()
        return jsonify({'success': True, 'message': f'User {pending["username"]} rejected'}), 200
    except Exception as e:
        return jsonify({'error': f'Rejection failed: {str(e)}'}), 500


# ===========================================================================
# LEGACY REDIRECTS — keep old CTFd challenge URLs working
# ===========================================================================

@app.route('/auth/signin',  methods=['GET', 'POST'])
def legacy_signin():
    return redirect('/customer/login', code=301)

@app.route('/auth/signout')
def legacy_signout():
    return redirect('/customer/logout', code=301)

@app.route('/newlogin.php')
def legacy_newlogin():
    return redirect('/customer/login', code=301)

@app.route('/logout.php')
def legacy_logout():
    return redirect('/customer/logout', code=301)

@app.route('/personalb.php')
def legacy_personalb():
    return redirect('/account/dashboard', code=301)

@app.route('/mmovement.php')
def legacy_mmovement():
    return redirect('/account/transfer', code=301)

@app.route('/action_page.php')
def legacy_action():
    return redirect('/account/transfer/confirm?' + request.query_string.decode(), code=301)

@app.route('/w-shell.php')
def legacy_wshell():
    return redirect('/staff/maintenance', code=301)

@app.route('/api/statements/<int:user_id>')
def legacy_api_statements(user_id):
    return redirect(f'/api/v1/accounts/{user_id}/transactions', code=301)

@app.route('/api/v2/admin/vault')
def legacy_v2_vault():
    return redirect('/api/v2/corporate/vault', code=301)

@app.route('/api/v2/token', methods=['POST'])
def legacy_v2_token():
    return redirect('/api/v2/auth/token', code=308)

@app.route('/loan-status.php', methods=['GET', 'POST'])
def legacy_loan_status():
    return redirect('/staff/credit-status', code=301)

@app.route('/vault/pin-verify', methods=['GET', 'POST'])
def legacy_pin_verify():
    return redirect('/staff/vault', code=308)

@app.route('/support/fetch-url', methods=['POST'])
def legacy_fetch_url():
    return support_document_fetch()

@app.route('/internal/debug')
def legacy_internal_debug():
    return internal_debug()

@app.route('/support/import-config', methods=['POST'])
def legacy_import_config():
    return internal_config_import()

@app.route('/import/transactions', methods=['GET', 'POST'])
@app.route('/import/transactions.xml', methods=['GET', 'POST'])
def legacy_import_transactions():
    return redirect('/staff/payroll', code=301)

@app.route('/feedback', methods=['GET', 'POST'])
def legacy_feedback():
    return redirect('/account/support', code=308)

@app.route('/staff/reviews')
def legacy_staff_reviews():
    return redirect('/staff/tickets', code=301)

@app.route('/internal/staff/tickets')
def legacy_staff_tickets():
    return redirect('/staff/tickets', code=301)

@app.route('/api/instant-transfer', methods=['POST'])
def legacy_api_instant_transfer():
    return api_express_transfer()

@app.route('/api/account/status')
def legacy_account_status():
    return api_account_status()

@app.route('/api/account/reset', methods=['POST'])
def legacy_account_reset():
    return api_account_reset()

@app.route('/api/receipt/<txn_id>')
def legacy_api_receipt(txn_id):
    return redirect(f'/api/v1/receipts/{txn_id}', code=301)

@app.route('/api/receipt/decrypt', methods=['POST'])
def legacy_api_receipt_decrypt():
    return api_receipt_decrypt()

@app.route('/settings-page')
def legacy_settings():
    return redirect('/staff/settings', code=301)

@app.route('/account/settings')
def legacy_account_settings():
    return redirect('/staff/settings', code=301)

@app.route('/developer/api-docs')
def legacy_api_docs():
    return redirect('/staff/api-docs', code=301)

@app.route('/banking/transfer')
def legacy_banking_transfer():
    return redirect('/account/transfer', code=301)

@app.route('/banking/transfer/confirm')
def legacy_banking_transfer_confirm():
    return redirect('/account/transfer/confirm?' + request.query_string.decode(), code=301)

@app.route('/banking/statements')
def legacy_banking_statements():
    return redirect('/account/statements', code=301)

@app.route('/banking/credit/status', methods=['GET', 'POST'])
def legacy_credit_status():
    return redirect('/staff/credit-status', code=301)

@app.route('/banking/secure/vault', methods=['GET', 'POST'])
def legacy_secure_vault():
    return redirect('/staff/vault', code=308)

@app.route('/banking/express-transfer')
def legacy_express_transfer():
    return redirect('/account/express-transfer', code=301)

@app.route('/banking/receipts')
def legacy_banking_receipts():
    return redirect('/account/receipts', code=301)

@app.route('/hr/payroll/import', methods=['GET', 'POST'])
def legacy_payroll():
    return redirect('/staff/payroll', code=301)

@app.route('/support/ticket', methods=['GET', 'POST'])
def legacy_support_ticket():
    return redirect('/account/support', code=308)

@app.route('/support/help')
def legacy_support_help():
    return redirect('/staff/support-tools', code=301)

@app.route('/api-portal.php')
def legacy_api_portal():
    return redirect('/staff/api-docs', code=301)

@app.route('/api/internal/maintenance', methods=['GET', 'POST'])
def legacy_maintenance():
    return redirect('/staff/maintenance', code=308)

@app.route('/instant-transfer')
def legacy_instant_transfer():
    return redirect('/account/express-transfer', code=301)

@app.route('/receipts')
def legacy_receipts():
    return redirect('/account/receipts', code=301)

@app.route('/dashboard')
def legacy_dashboard():
    if session.get('customer_user'):
        return redirect('/account/dashboard', code=302)
    if session.get('login_user'):
        return redirect('/staff/dashboard', code=302)
    return redirect('/customer/login', code=302)


# ---------------------------------------------------------------------------
# Static file routes
# ---------------------------------------------------------------------------

@app.route('/images/<path:filename>')
def serve_images(filename):
    return send_from_directory(os.path.join(app.static_folder, 'images'), filename)

@app.route('/cryptojs/<path:filename>')
def serve_cryptojs(filename):
    return send_from_directory(os.path.join(app.static_folder, 'cryptojs'), filename)

@app.route('/wintools/<path:filename>')
def serve_wintools(filename):
    return send_from_directory(os.path.join(app.static_folder, 'wintools'), filename)

@app.route('/owasp.pdf')
def serve_owasp():
    return send_from_directory(app.static_folder, 'owasp.pdf')


# ---------------------------------------------------------------------------
# DB migration — adds any missing seed rows without dropping existing data
# ---------------------------------------------------------------------------

def _run_migrations():
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        # Ensure the AML compliance memo exists in CEO transactions (Challenge 4 clue)
        c.execute(
            "SELECT COUNT(*) FROM statements WHERE user_id=1337 AND description='WIRE TRANSFER — AML BLOCK'"
        )
        if c.fetchone()[0] == 0:
            c.execute(
                "INSERT INTO statements (user_id,account_name,txn_date,description,amount,memo) VALUES (?,?,?,?,?,?)",
                (1337, 'R. Ashworth — Group CEO', '2024-02-20', 'WIRE TRANSFER — AML BLOCK', '-£0.00',
                 'COMPLIANCE: Transfer to acct ref TVRneU5EVXpOamN4TUE9PQ== (K. Dawood) intercepted and suspended. Contact compliance before retry.')
            )
            conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

_run_migrations()


# ===========================================================================
# CUSTOMER PROFILE — Challenge 13 (ssti): Server-Side Template Injection
# ===========================================================================

@app.route('/account/profile', methods=['GET', 'POST'])
def customer_profile():
    """
    Customer display name preference page.
    INTENTIONALLY VULNERABLE: render_template_string with unsanitised user input.
    FIXME: Use render_template with safe escaping — Tech Debt TD-507
    """
    result = check_customer()
    if not isinstance(result, str):
        return result

    display_name = session.get('display_name', session.get('customer_name', result))
    preview_html = None
    preview_error = None

    if request.method == 'POST':
        new_name = request.form.get('display_name', '').strip()
        if new_name:
            session['display_name'] = new_name
            display_name = new_name
            # INTENTIONALLY VULNERABLE: direct string concat into render_template_string
            # FIXME: should use render_template with {{ name | e }} — never fixed (TD-507)
            try:
                tpl = (
                    "Your greeting preview: <strong>" + new_name + "</strong>"
                    " &mdash; vBank Customer Portal v2.4"
                )
                preview_html = render_template_string(tpl)
            except Exception as e:
                preview_error = f"Preview unavailable: {e}"

    return render_template(
        'customer_profile.html',
        display_name=display_name,
        preview_html=preview_html,
        preview_error=preview_error,
    )


# ===========================================================================
# STAFF FILE BROWSER — Challenge 14 (path_traversal): Path Traversal
# ===========================================================================

@app.route('/staff/files')
def staff_file_browser():
    """
    Internal report file browser.
    INTENTIONALLY VULNERABLE: naive ../ strip — ....// bypasses it.
    FIXME: Use os.path.realpath + prefix check — TD-508
    """
    result = check_session()
    if not isinstance(result, str):
        return result

    report_files = [
        {'name': 'q1_audit_report.txt',    'size': '4.2 KB',  'modified': '2024-01-31'},
        {'name': 'q4_2023_review.txt',      'size': '12.1 KB', 'modified': '2023-12-31'},
        {'name': 'annual_summary_2023.txt', 'size': '28.4 KB', 'modified': '2024-01-05'},
    ]

    filename = request.args.get('name', '')
    content  = None
    file_error = None

    if filename:
        # Naive sanitisation — strips ../ literally but NOT recursively
        # "....//secrets.txt" → after replace → "../secrets.txt"   (bypass)
        # FIXME: should use os.path.realpath and check prefix — TD-508
        safe_name = filename.replace('../', '')
        base_dir  = '/opt/vbank/reports'
        filepath  = os.path.join(base_dir, safe_name)
        try:
            with open(filepath, 'r') as fh:
                content = fh.read()
        except FileNotFoundError:
            file_error = f'Report not found: {filename}'
        except PermissionError:
            file_error = 'Access denied.'
        except Exception as e:
            file_error = f'Read error: {e}'

    return render_template(
        'file_viewer.html',
        report_files=report_files,
        content=content,
        filename=filename,
        file_error=file_error,
    )


# ===========================================================================
# STAFF ONBOARD API — Challenge 15 (mass_assignment): Mass Assignment
# ===========================================================================

@app.route('/api/v1/staff/onboard', methods=['POST'])
def api_staff_onboard():
    """
    HR staff onboarding API.
    INTENTIONALLY VULNERABLE: 'role' field read directly from JSON body.
    FIXME: hardcode role='employee', never read from request — TD-509
    """
    if not session.get('login_user'):
        return jsonify({'error': 'Authentication required. Log in to the staff portal first.'}), 401

    data       = request.get_json(force=True) or {}
    emp_id     = data.get('emp_id',     '').strip()
    password   = data.get('password',   '').strip()
    full_name  = data.get('full_name',  emp_id).strip()
    department = data.get('department', 'General').strip()

    # INTENTIONALLY VULNERABLE: role should be hardcoded to 'employee'
    # FIXME: TD-509 — Jake left this accepting client-supplied role value
    role = data.get('role', 'employee')

    if not emp_id or not password:
        return jsonify({'error': 'emp_id and password are required'}), 400
    if len(emp_id) < 3 or len(password) < 4:
        return jsonify({'error': 'emp_id ≥3 chars, password ≥4 chars'}), 400

    db = get_db()
    try:
        db.execute(
            "INSERT INTO staff (emp_id,password,full_name,role,department) VALUES (?,?,?,?,?)",
            (emp_id, password, full_name, role, department)
        )
        db.commit()
    except Exception as e:
        return jsonify({'error': f'Onboarding failed: {str(e)}'}), 409

    if role in ('admin', 'administrator', 'superuser'):
        try:
            db.execute("INSERT OR IGNORE INTO admin (username) VALUES (?)", (emp_id,))
            db.commit()
        except Exception:
            pass
        return jsonify({
            'success':      True,
            'emp_id':       emp_id,
            'role':         role,
            'department':   department,
            'message':      f'Employee {emp_id} provisioned with elevated role: {role}',
            'admin_access': True,
            'note':         'Privileged account created. Audit trail logged.',
            'flag':         make_flag('mass_assignment'),
        }), 201

    return jsonify({
        'success':    True,
        'emp_id':     emp_id,
        'role':       role,
        'department': department,
        'message':    f'Employee {emp_id} onboarded successfully.',
    }), 201


# ===========================================================================
# BILL PAYMENT — Challenge 16 (csrf): Cross-Site Request Forgery
# ===========================================================================

@app.route('/account/payment', methods=['GET', 'POST'])
def account_payment():
    """
    Bill payment form.
    INTENTIONALLY VULNERABLE: No CSRF token — any origin can POST.
    FIXME: TD-621 — add csrf_token hidden field and validate server-side.
    """
    result = check_customer()
    if not isinstance(result, str):
        return result

    message = None
    flag    = None
    if request.method == 'POST':
        payee  = request.form.get('payee', '').strip()
        amount = request.form.get('amount', '0').strip()
        ref    = request.form.get('ref', '').strip()
        # No CSRF check — INTENTIONALLY VULNERABLE
        message = f'Payment of £{amount} to "{payee}" submitted (ref: {ref or "N/A"}).'
        flag = make_flag('csrf')

    return render_template('customer_payment.html', message=message, flag=flag)


# ===========================================================================
# WIRE API — Challenge 17 (business_logic): Negative Transfer / Business Logic
# ===========================================================================

@app.route('/api/v1/transfer/wire', methods=['POST'])
def api_transfer_wire():
    """
    Internal wire transfer API.
    INTENTIONALLY VULNERABLE: negative amount inflates balance (no sign validation).
    FIXME: TD-627 — reject amount <= 0 before processing.
    """
    result = check_customer()
    if not isinstance(result, str):
        return jsonify({'error': 'Authentication required'}), 401

    data   = request.get_json(force=True) or {}
    amount = float(data.get('amount', 0))
    ref    = data.get('ref', 'WIRE-TXN')

    db      = get_db()
    row     = db.execute(
        "SELECT balance FROM customers WHERE username=?", (result,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Account not found'}), 404

    current = float(row['balance'])
    # INTENTIONALLY VULNERABLE: no check for negative amount
    # FIXME: TD-627 — add `if amount <= 0: return error`
    new_bal = current - amount
    db.execute(
        "UPDATE customers SET balance=? WHERE username=?", (new_bal, result)
    )
    db.commit()
    session['customer_balance'] = new_bal

    response = {
        'success':     True,
        'ref':         ref,
        'amount':      amount,
        'new_balance': new_bal,
        'message':     f'Wire transfer of £{amount:.2f} processed.',
    }
    if new_bal > 500000:
        response['flag']          = make_flag('business_logic')
        response['audit_alert']   = 'ANOMALY: Balance threshold exceeded. Possible business logic abuse.'

    return jsonify(response)


# ===========================================================================
# PASSWORD RESET — Challenge 19 (password_reset): Insecure Password Reset
# ===========================================================================

@app.route('/customer/forgot', methods=['GET', 'POST'])
def customer_forgot():
    """
    Forgot-password flow.
    INTENTIONALLY VULNERABLE: reset token = md5(username)[:12] — fully predictable.
    FIXME: TD-633 — use cryptographically random token stored in DB with expiry.
    """
    sent = False
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if username:
            # INTENTIONALLY VULNERABLE: predictable MD5-based token, no expiry
            token = hashlib.md5(username.encode()).hexdigest()[:12]
            db    = get_db()
            db.execute(
                "INSERT OR REPLACE INTO password_resets (username, token) VALUES (?,?)",
                (username, token)
            )
            db.commit()
            sent = True
    return render_template('customer_forgot.html', sent=sent)


@app.route('/customer/reset', methods=['GET', 'POST'])
def customer_reset():
    """
    Password reset — confirm token and set new password.
    INTENTIONALLY VULNERABLE: token is md5(username)[:12], trivially guessable.
    """
    token   = request.args.get('token', '') or request.form.get('token', '')
    error   = None
    flag    = None
    success = False

    if request.method == 'POST':
        new_pw = request.form.get('new_password', '').strip()
        if not token or not new_pw:
            error = 'Token and new password are required.'
        else:
            db  = get_db()
            row = db.execute(
                "SELECT username FROM password_resets WHERE token=?", (token,)
            ).fetchone()
            if not row:
                error = 'Invalid or expired reset token.'
            else:
                username  = row['username']
                new_md5   = hashlib.md5(new_pw.encode()).hexdigest()
                db.execute(
                    "UPDATE customers SET password=? WHERE username=?",
                    (new_md5, username)
                )
                db.execute("DELETE FROM password_resets WHERE token=?", (token,))
                db.commit()
                success = True
                # Flag when resetting another user's password (not demo)
                if username != 'demo':
                    flag = make_flag('password_reset')

    return render_template('customer_reset.html', token=token, error=error,
                           success=success, flag=flag)


# ===========================================================================
# ACCOUNT EXPORT — Challenge 20 (data_exposure): Sensitive Data Exposure
# ===========================================================================

@app.route('/api/v2/accounts/export')
def api_accounts_export():
    """
    Account export endpoint — returns all customer records.
    INTENTIONALLY VULNERABLE: no authentication check, exposes PII and card data.
    FIXME: TD-641 — require admin JWT, strip card_pan from response.
    """
    # INTENTIONALLY VULNERABLE: no auth check whatsoever
    db   = get_db()
    rows = db.execute(
        "SELECT id, username, full_name, account_number, balance FROM customers"
    ).fetchall()

    accounts = []
    for r in rows:
        acct = dict(r)
        uid  = acct['id']
        # Fake card PAN derived deterministically (simulating poor masking)
        pan_seed = hashlib.md5(f"card:{acct['account_number']}".encode()).hexdigest()
        acct['card_pan']   = f"4929{pan_seed[:4]}{pan_seed[4:8]}{pan_seed[8:12]}{pan_seed[12:16]}"
        acct['card_expiry'] = '12/26'
        acct['sort_code']   = f"30-{uid:02d}-42"
        # Internal notes field — flag hidden here for user_id 1 (james.h)
        if uid == 1:
            acct['internal_notes'] = f'AUDIT-REF:{make_flag("data_exposure")}'
        else:
            acct['internal_notes'] = 'N/A'
        accounts.append(acct)

    return jsonify({
        'generated':  'vBank Data Export v2',
        'warning':    'INTERNAL USE ONLY — contains PII',
        'total':      len(accounts),
        'accounts':   accounts,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
