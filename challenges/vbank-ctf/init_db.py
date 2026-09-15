#!/usr/bin/env python3
"""
vBank — Online Banking Portal
Database initialisation script.
"""

import os
import sqlite3
import hashlib
import json

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

_FLAG_PLACEHOLDER = 'LYD{see_make_flag_in_app_py}'


def init_db():
    conn = sqlite3.connect(DATABASE)
    c    = conn.cursor()

    # --- Customers (customer portal) ---
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT    NOT NULL UNIQUE,
        password        TEXT    NOT NULL,
        full_name       TEXT    NOT NULL,
        account_number  TEXT    NOT NULL UNIQUE,
        acct_user_id    INTEGER NOT NULL,
        balance         REAL    DEFAULT 5000.00
    )''')

    # --- Staff (employee portal — plaintext passwords, dev oversight) ---
    c.execute('''CREATE TABLE IF NOT EXISTS staff (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id      TEXT    NOT NULL UNIQUE,
        password    TEXT    NOT NULL,
        full_name   TEXT    NOT NULL,
        role        TEXT    DEFAULT 'employee',
        department  TEXT    DEFAULT 'General'
    )''')

    # --- Users (legacy / admin panel) ---
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT    NOT NULL UNIQUE,
        password    TEXT    NOT NULL,
        is_approved INTEGER DEFAULT 0,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_at TIMESTAMP DEFAULT NULL,
        created_by  TEXT    DEFAULT NULL
    )''')

    # --- Admin accounts ---
    c.execute('''CREATE TABLE IF NOT EXISTS admin (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT    NOT NULL UNIQUE
    )''')

    # --- Pending Registrations ---
    c.execute('''CREATE TABLE IF NOT EXISTS pending_registrations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT    NOT NULL UNIQUE,
        password     TEXT    NOT NULL,
        email        TEXT    DEFAULT NULL,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status       TEXT    DEFAULT 'pending',
        notes        TEXT    DEFAULT NULL
    )''')

    # --- Account Statements (IDOR — Flag 4) ---
    c.execute('''CREATE TABLE IF NOT EXISTS statements (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        account_name TEXT    NOT NULL,
        txn_date     TEXT    NOT NULL,
        description  TEXT    NOT NULL,
        amount       TEXT    NOT NULL,
        memo         TEXT    DEFAULT ""
    )''')

    # --- Loans / Credit Applications (Blind SQLi — Flag 7) ---
    c.execute('''CREATE TABLE IF NOT EXISTS loans (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id   TEXT    NOT NULL UNIQUE,
        applicant TEXT    NOT NULL,
        amount    TEXT    NOT NULL,
        status    TEXT    NOT NULL,
        approved  INTEGER DEFAULT 0
    )''')

    # --- Secrets (PIN for blind SQLi vault) ---
    c.execute('''CREATE TABLE IF NOT EXISTS secrets (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        key   TEXT    NOT NULL UNIQUE,
        value TEXT    NOT NULL
    )''')

    # --- Support Tickets (Stored XSS — Flag 10) ---
    c.execute('''CREATE TABLE IF NOT EXISTS feedback (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username   TEXT    NOT NULL,
        message    TEXT    NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # --- Accounts (Race Condition — Flag 11) ---
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT    NOT NULL UNIQUE,
        balance  REAL    DEFAULT 1000.00
    )''')

    # --- Receipts (Broken Crypto — Flag 12) ---
    c.execute('''CREATE TABLE IF NOT EXISTS receipts (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        txn_id TEXT    NOT NULL UNIQUE,
        data   TEXT    NOT NULL
    )''')

    # --- Password Resets (Insecure Password Reset — Challenge 19) ---
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username   TEXT    NOT NULL UNIQUE,
        token      TEXT    NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # =====================================================================
    # Seed: Customers
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM customers')
    if c.fetchone()[0] == 0:
        customers = [
            ('james.h',  hashlib.md5(b'James@2024').hexdigest(),  'James Hartley', 'VBK-10101', 101, 4287.50),
            ('sarah.c',  hashlib.md5(b'Sarah@2024').hexdigest(),  'Sarah Chen',    'VBK-10102', 102, 8540.00),
            ('demo',     hashlib.md5(b'demo123').hexdigest(),      'Demo User',     'VBK-10099', 103, 1000.00),
        ]
        c.executemany(
            "INSERT INTO customers (username,password,full_name,account_number,acct_user_id,balance) "
            "VALUES (?,?,?,?,?,?)",
            customers
        )

    # =====================================================================
    # Seed: Staff (plaintext passwords — intentional dev oversight)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM staff')
    if c.fetchone()[0] == 0:
        staff = [
            ('sysadmin',    'vBank@Admin2024!', 'System Administrator', 'admin',    'IT'),
            ('j.thompson',  'JakeT@2024!',      'Jake Thompson',        'employee', 'DevOps'),
        ]
        c.executemany(
            "INSERT INTO staff (emp_id,password,full_name,role,department) VALUES (?,?,?,?,?)",
            staff
        )

    # =====================================================================
    # Seed: Users (admin panel legacy)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (username, password, is_approved, approved_at, created_by) "
            "VALUES (?, ?, 1, datetime('now'), ?)",
            ('sysadmin', hashlib.md5(b'vBank@Admin2024!').hexdigest(), 'system')
        )

    c.execute('SELECT COUNT(*) FROM admin')
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO admin (username) VALUES (?)", ('sysadmin',))

    # =====================================================================
    # Seed: Statements (IDOR target: CEO user_id=1337)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM statements')
    if c.fetchone()[0] == 0:
        stmts = [
            # James Hartley — user_id=101
            (101, 'James Hartley — Operations', '2024-01-15', 'Monthly Salary',                  '+£4,200.00', ''),
            (101, 'James Hartley — Operations', '2024-01-22', 'Office Supplies Reimbursement',   '+£85.50',    ''),
            (101, 'James Hartley — Operations', '2024-02-01', 'Travel Expenses — Manchester',    '-£312.00',   ''),
            # Sarah Chen — user_id=102
            (102, 'Sarah Chen — Finance',       '2024-01-10', 'Monthly Salary',                  '+£5,800.00', ''),
            (102, 'Sarah Chen — Finance',       '2024-01-28', 'Training Course Reimbursement',   '+£450.00',   ''),
            (102, 'Sarah Chen — Finance',       '2024-02-05', 'Corporate Amex Settlement',       '-£1,240.00', ''),
            # Demo User — user_id=103
            (103, 'Demo User — General',        '2024-02-01', 'Account Opened',                  '+£1,000.00', ''),
            # CEO account — IDOR target (user_id=1337)
            (1337, 'R. Ashworth — Group CEO',   '2024-01-01', 'Q4 Performance Bonus',            '+£500,000.00', ''),
            (1337, 'R. Ashworth — Group CEO',   '2024-01-08', 'Citadel Associates Advisory',     '-£45,000.00',  'Quarterly retainer'),
            (1337, 'R. Ashworth — Group CEO',   '2024-01-15', 'CONFIDENTIAL TRANSFER',           '+£1,000,000.00', 'FLAG: ' + _FLAG_PLACEHOLDER),
            (1337, 'R. Ashworth — Group CEO',   '2024-02-01', 'Offshore Holding Transfer',       '-£250,000.00', 'Cayman — ref: OHT-2024-019'),
            (1337, 'R. Ashworth — Group CEO',   '2024-02-14', 'Board Discretionary Fund',        '+£75,000.00',  ''),
            # AML compliance record — account ref for Challenge 4 (Flagged for Review)
            (1337, 'R. Ashworth — Group CEO',   '2024-02-20', 'WIRE TRANSFER — AML BLOCK',       '-£0.00',
             'COMPLIANCE: Transfer to acct ref TVRneU5EVXpOamN4TUE9PQ== (K. Dawood) intercepted and suspended. Contact compliance before retry.'),
        ]
        c.executemany(
            "INSERT INTO statements (user_id,account_name,txn_date,description,amount,memo) "
            "VALUES (?,?,?,?,?,?)",
            stmts
        )

    # =====================================================================
    # Seed: Loans (Blind SQLi target)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM loans')
    if c.fetchone()[0] == 0:
        loans = [
            ('VBK-CR-2401', 'Alice Thornton',    '£25,000',  'Personal Loan',            1),
            ('VBK-CR-2402', 'Robert Whitfield',  '£150,000', 'Commercial Mortgage',       1),
            ('VBK-CR-2403', 'Charlotte Nkosi',   '£10,000',  'Vehicle Finance',           0),
            ('VBK-CR-2404', 'Daniel Okafor',     '£50,000',  'Business Expansion Loan',  1),
            ('VBK-CR-2405', 'Emma Fitzgerald',   '£5,500',   'Graduate Loan',             0),
        ]
        c.executemany(
            "INSERT INTO loans (loan_id,applicant,amount,status,approved) VALUES (?,?,?,?,?)",
            loans
        )

    # =====================================================================
    # Seed: Secrets (vault PIN for blind SQLi extraction)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM secrets')
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO secrets (key,value) VALUES (?,?)", ('admin_pin', '84721'))

    # =====================================================================
    # Seed: Receipts (AES-ECB crypto challenge)
    # =====================================================================
    c.execute('SELECT COUNT(*) FROM receipts')
    if c.fetchone()[0] == 0:
        receipts = [
            ('TXN-20240101', json.dumps({
                'txn_id':    'TXN-20240101',
                'from':      'vBank Corporate Account',
                'to':        'James Hartley',
                'amount':    '£4200.00',
                'reference': 'JAN-SALARY-2024',
                'memo':      'Monthly salary payment'
            })),
            ('TXN-20240102', json.dumps({
                'txn_id':    'TXN-20240102',
                'from':      'vBank Corporate Account',
                'to':        'Sarah Chen',
                'amount':    '£5800.00',
                'reference': 'JAN-SALARY-2024',
                'memo':      'Monthly salary payment'
            })),
            ('TXN-SYSTEM', json.dumps({
                'txn_id':    'TXN-SYSTEM',
                'from':      'SYSTEM',
                'to':        'VAULT',
                'amount':    '£999,999.00',
                'reference': 'INTERNAL-CLASSIFIED',
                'memo':      _FLAG_PLACEHOLDER
            })),
        ]
        c.executemany("INSERT INTO receipts (txn_id,data) VALUES (?,?)", receipts)

    conn.commit()
    conn.close()
    print(f"[init_db] Database initialised at: {DATABASE}")


if __name__ == '__main__':
    init_db()
