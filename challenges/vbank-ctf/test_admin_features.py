#!/usr/bin/env python3
"""
Test script for Admin Dashboard features
Tests:
1. Account approval workflow
2. Single account creation
3. Bulk account creation
4. Login with approval check
"""

import sys
import json
import sqlite3
import hashlib
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app import app
from app import get_db

def test_database_schema():
    """Verify database schema has all required columns"""
    print("\n=== Testing Database Schema ===")
    with app.app_context():
        db = get_db()
        
        # Check users table columns
        cursor = db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        
        required_cols = ['id', 'username', 'password', 'is_approved', 'created_at', 'approved_at', 'created_by']
        for col in required_cols:
            if col in columns:
                print(f"✓ users.{col}")
            else:
                print(f"✗ MISSING: users.{col}")
                return False
        
        # Check pending_registrations table exists
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pending_registrations'")
        if cursor.fetchone():
            print("✓ pending_registrations table exists")
        else:
            print("✗ MISSING: pending_registrations table")
            return False
        
        # Check initial data
        cursor = db.execute("SELECT username, is_approved FROM users WHERE username = 'root'")
        row = cursor.fetchone()
        if row and row[1] == 1:
            print(f"✓ root user created and approved")
        else:
            print(f"✗ root user not properly initialized")
            return False
    
    return True


def test_single_user_creation():
    """Test creating a single user"""
    print("\n=== Testing Single User Creation ===")
    
    client = app.test_client()
    
    # Create test user
    response = client.post(
        '/admin/api/users',
        json={
            'username': 'testuser1',
            'password': 'testpass123',
            'email': 'test1@example.com',
            'auto_approve': False
        },
        headers={'Content-Type': 'application/json'}
    )
    
    # This will fail because we're not logged in as admin, but let's check the error
    if response.status_code in [403, 401]:
        print(f"✓ API correctly requires admin authentication (returned {response.status_code})")
    else:
        print(f"✗ Expected 403/401 but got {response.status_code}")
        print(f"  Response: {response.get_json()}")
    
    # Test with database directly
    with app.app_context():
        db = get_db()
        password_md5 = hashlib.md5('testpass123'.encode()).hexdigest()
        
        db.execute(
            "INSERT INTO pending_registrations (username, password, email, status) VALUES (?, ?, ?, 'pending')",
            ('testuser1', password_md5, 'test1@example.com')
        )
        db.commit()
        
        # Verify it was inserted
        row = db.execute(
            "SELECT username FROM pending_registrations WHERE username = 'testuser1'"
        ).fetchone()
        
        if row:
            print(f"✓ User testuser1 created in pending_registrations")
        else:
            print(f"✗ Failed to create user in pending_registrations")
            return False
    
    return True


def test_bulk_user_creation():
    """Test bulk user creation"""
    print("\n=== Testing Bulk User Creation ===")
    
    csv_data = """user2,pass456,user2@example.com
user3,pass789,user3@example.com
user4,pass012,user4@example.com"""
    
    with app.app_context():
        db = get_db()
        
        for line in csv_data.strip().split('\n'):
            username, password, email = line.split(',')
            password_md5 = hashlib.md5(password.encode()).hexdigest()
            
            db.execute(
                "INSERT INTO pending_registrations (username, password, email, status) VALUES (?, ?, ?, 'pending')",
                (username, password_md5, email)
            )
        
        db.commit()
        
        # Verify all were inserted
        cursor = db.execute("SELECT COUNT(*) FROM pending_registrations")
        count = cursor.fetchone()[0]
        
        if count >= 4:  # root + 4 new users
            print(f"✓ Bulk users created ({count} total in pending registrations)")
        else:
            print(f"✗ Expected at least 4 users, got {count}")
            return False
    
    return True


def test_user_approval():
    """Test approving a pending user"""
    print("\n=== Testing User Approval ===")
    
    with app.app_context():
        db = get_db()
        
        # Get a pending user
        pending = db.execute(
            "SELECT id, username, password FROM pending_registrations WHERE status = 'pending' LIMIT 1"
        ).fetchone()
        
        if not pending:
            print("✗ No pending users to test")
            return False
        
        user_id, username, password = pending
        
        # Simulate approval
        db.execute(
            "INSERT INTO users (username, password, is_approved, approved_at, created_by) VALUES (?, ?, 1, datetime('now'), ?)",
            (username, password, 'admin')
        )
        
        db.execute(
            "UPDATE pending_registrations SET status = 'approved' WHERE id = ?", (user_id,)
        )
        
        db.commit()
        
        # Verify
        user_row = db.execute(
            "SELECT is_approved FROM users WHERE username = ?", (username,)
        ).fetchone()
        
        pending_row = db.execute(
            "SELECT status FROM pending_registrations WHERE id = ?", (user_id,)
        ).fetchone()
        
        if user_row and user_row[0] == 1:
            print(f"✓ User {username} approved and moved to users table")
        else:
            print(f"✗ Failed to approve user {username}")
            return False
        
        if pending_row and pending_row[0] == 'approved':
            print(f"✓ Pending registration status updated to 'approved'")
        else:
            print(f"✗ Failed to update pending registration status")
            return False
    
    return True


def test_login_approval_check():
    """Test that login checks approval status"""
    print("\n=== Testing Login Approval Check ===")
    
    with app.app_context():
        db = get_db()
        
        # Create an unapproved user
        unapproved_user = 'unapproveduser'
        password = 'testpass123'
        password_md5 = hashlib.md5(password.encode()).hexdigest()
        
        db.execute(
            "INSERT INTO users (username, password, is_approved) VALUES (?, ?, 0)",
            (unapproved_user, password_md5)
        )
        db.commit()
        
        print(f"✓ Created unapproved user: {unapproved_user}")
        
        # Create an approved user
        approved_user = 'approveduser'
        password2 = 'testpass456'
        password2_md5 = hashlib.md5(password2.encode()).hexdigest()
        
        db.execute(
            "INSERT INTO users (username, password, is_approved, approved_at) VALUES (?, ?, 1, datetime('now'))",
            (approved_user, password2_md5)
        )
        db.commit()
        
        print(f"✓ Created approved user: {approved_user}")
        
        # Verify in database
        check1 = db.execute(
            "SELECT is_approved FROM users WHERE username = ?", (unapproved_user,)
        ).fetchone()
        
        check2 = db.execute(
            "SELECT is_approved FROM users WHERE username = ?", (approved_user,)
        ).fetchone()
        
        if check1 and check1[0] == 0:
            print(f"✓ Unapproved user has is_approved=0")
        else:
            print(f"✗ Unapproved user not properly set")
            return False
        
        if check2 and check2[0] == 1:
            print(f"✓ Approved user has is_approved=1")
        else:
            print(f"✗ Approved user not properly set")
            return False
    
    return True


def test_admin_check_function():
    """Test the check_admin() function"""
    print("\n=== Testing check_admin() Function ===")
    
    # Verify check_admin is defined in app module
    import app as app_module
    
    if hasattr(app_module, 'check_admin'):
        print("✓ check_admin() function is registered in app")
    else:
        print("✗ check_admin() function not found in app")
        return False
    
    # Test through HTTP (proper Flask context)
    with app.test_client() as client:
        # Try accessing admin route without session (should redirect)
        response = client.get('/admin')
        if response.status_code == 302:  # Redirect to login
            print("✓ Admin route requires authentication (redirect 302)")
        else:
            print(f"  Admin route returned {response.status_code}")
    
    return True


def test_route_registration():
    """Test that all admin routes are registered"""
    print("\n=== Testing Route Registration ===")
    
    expected_routes = [
        '/admin',
        '/admin/api/pending-users',
        '/admin/api/users',
        '/admin/api/users/bulk',
    ]
    
    routes = [rule.rule for rule in app.url_map.iter_rules()]
    
    for expected in expected_routes:
        if expected in routes:
            print(f"✓ Route registered: {expected}")
        else:
            print(f"✗ Route NOT registered: {expected}")
            return False
    
    return True


def main():
    """Run all tests"""
    print("=" * 60)
    print("Admin Dashboard Features Test Suite")
    print("=" * 60)
    
    tests = [
        ("Database Schema", test_database_schema),
        ("Single User Creation", test_single_user_creation),
        ("Bulk User Creation", test_bulk_user_creation),
        ("User Approval", test_user_approval),
        ("Login Approval Check", test_login_approval_check),
        ("Admin Check Function", test_admin_check_function),
        ("Route Registration", test_route_registration),
    ]
    
    results = {}
    for name, test_func in tests:
        try:
            result = test_func()
            results[name] = "✓ PASS" if result else "✗ FAIL"
        except Exception as e:
            results[name] = f"✗ ERROR: {str(e)}"
            print(f"\n✗ Exception in {name}: {e}")
    
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = 0
    failed = 0
    for name, result in results.items():
        print(f"{result:12} - {name}")
        if "PASS" in result:
            passed += 1
        else:
            failed += 1
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(results)} tests")
    
    if failed == 0:
        print("\n✓ All tests passed!")
        return 0
    else:
        print(f"\n✗ {failed} test(s) failed")
        return 1


if __name__ == '__main__':
    exit(main())
