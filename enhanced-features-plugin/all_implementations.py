"""
Comprehensive Implementation of All Remaining Features
Auto-Login, Admin Dashboard, Scoring, Mobile, Logging
"""

from flask import Blueprint, request, jsonify, render_template_string, render_template, redirect
from CTFd.models import db, Users, Teams, Challenges, Solves
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils import user as current_user
from datetime import datetime, timedelta
import logging
import os

logger = logging.getLogger(__name__)
bp = Blueprint('implementations', __name__, url_prefix='/api/impl')

# Admin UI Blueprint (separate from API)
admin_bp = Blueprint('impl_admin', __name__, url_prefix='/admin')


# ============================================================================
# DATABASE MODELS
# ============================================================================

class UserApprovalRequest(db.Model):
    __tablename__ = 'user_approval_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    status = db.Column(db.String(20), default='pending')
    reason = db.Column(db.Text)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'))


class PersistentSession(db.Model):
    __tablename__ = 'persistent_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    token = db.Column(db.String(255), unique=True)
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)


class DynamicScore(db.Model):
    __tablename__ = 'dynamic_scores'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    base_points = db.Column(db.Integer)
    final_score = db.Column(db.Integer)
    solve_rank = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs_impl'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action_type = db.Column(db.String(100))
    resource_type = db.Column(db.String(100))
    ip_address = db.Column(db.String(45))
    details = db.Column(db.JSON)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class UserOnboarding(db.Model):
    __tablename__ = 'user_onboarding_impl'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    profile_completed = db.Column(db.Boolean, default=False)
    tutorial_completed = db.Column(db.Boolean, default=False)
    progress_percentage = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ============================================================================
# ACCOUNTS APPROVAL - Admin Dashboard
# ============================================================================

@bp.route('/admin/approvals', methods=['GET'])
@admins_only
def get_approvals():
    """Get all approval requests with full user data for admin dashboard"""
    try:
        all_approvals = UserApprovalRequest.query.order_by(
            UserApprovalRequest.requested_at.desc()
        ).all()

        result = []
        for a in all_approvals:
            user_obj = Users.query.get(a.user_id)
            result.append({
                'id': a.id,
                'user_id': a.user_id,
                'username': user_obj.name if user_obj else 'Deleted User',
                'email': user_obj.email if user_obj else 'N/A',
                'status': a.status,
                'requested_at': a.requested_at.isoformat() if a.requested_at else None,
                'reason': a.reason or '',
            })

        return jsonify({
            'approvals': result,
            'pending': sum(1 for a in result if a['status'] == 'pending'),
            'approved': sum(1 for a in result if a['status'] == 'approved'),
            'rejected': sum(1 for a in result if a['status'] == 'rejected'),
        })
    except Exception as e:
        logger.error(f"Error fetching approvals: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/admin/approvals/<int:approval_id>/approve', methods=['GET', 'POST'])
@admins_only
def approve_registration(approval_id):
    """Approve a pending user — unhide them and mark approved"""
    try:
        approval = UserApprovalRequest.query.get(approval_id)
        if not approval:
            return jsonify({'error': 'Not found'}), 404

        user_obj = Users.query.get(approval.user_id)
        if user_obj:
            user_obj.hidden = False
            user_obj.banned = False
            user_obj.verified = True
            if getattr(user_obj, 'team', None):
                user_obj.team.hidden = False
                user_obj.team.banned = False

        approval.status = 'approved'
        try:
            from CTFd.utils.user import get_current_user as _gcu
            admin = _gcu()
            approval.reviewed_by = admin.id if admin else None
        except Exception:
            pass

        db.session.commit()
        logger.info(f'[Approval] User {approval.user_id} approved')
        return jsonify({'success': True, 'message': 'User approved'})
    except Exception as e:
        logger.error(f'[Approval] approve error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/admin/approvals/<int:approval_id>/reject', methods=['GET', 'POST'])
@admins_only
def reject_registration(approval_id):
    """Reject a pending user — ban them and mark rejected"""
    try:
        approval = UserApprovalRequest.query.get(approval_id)
        if not approval:
            return jsonify({'error': 'Not found'}), 404

        data = request.get_json(silent=True) or {}
        reason = data.get('reason', 'Registration not approved by administrator.')

        user_obj = Users.query.get(approval.user_id)
        if user_obj:
            user_obj.banned = True
            user_obj.hidden = True
            if getattr(user_obj, 'team', None):
                user_obj.team.banned = True
                user_obj.team.hidden = True

        approval.status = 'rejected'
        approval.reason = reason
        try:
            from CTFd.utils.user import get_current_user as _gcu
            admin = _gcu()
            approval.reviewed_by = admin.id if admin else None
        except Exception:
            pass

        db.session.commit()
        logger.info(f'[Approval] User {approval.user_id} rejected')
        return jsonify({'success': True, 'message': 'User rejected'})
    except Exception as e:
        logger.error(f'[Approval] reject error: {e}')
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUTO-LOGIN & SESSIONS
# ============================================================================

@bp.route('/session/enable-autologin', methods=['POST'])
@authed_only
def enable_autologin():
    """Enable auto-login token for current user"""
    try:
        import secrets
        token = secrets.token_urlsafe(32)
        
        current = current_user()
        session = PersistentSession(
            user_id=current.id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(days=30)
        )
        db.session.add(session)
        db.session.commit()
        
        return jsonify({'success': True, 'token': token})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# DYNAMIC SCORING
# ============================================================================

@bp.route('/scoring/calculate', methods=['POST'])
def calculate_dynamic_score():
    """Calculate dynamic score for challenge solve"""
    try:
        data = request.json
        challenge_id = data.get('challenge_id')
        user_id = data.get('user_id')
        
        challenge = Challenges.query.get(challenge_id)
        if not challenge:
            return jsonify({'error': 'Challenge not found'}), 404
        
        solve_count = Solves.query.filter_by(challenge_id=challenge_id).count()
        base_points = challenge.points or 100
        
        # Decay formula: points = base_points * (1 - (solves/total)^2)
        total_users = Users.query.count()
        decay = 1 - ((solve_count / max(total_users, 1)) ** 2)
        final_score = max(int(base_points * decay), 10)
        
        score_record = DynamicScore(
            challenge_id=challenge_id,
            user_id=user_id,
            base_points=base_points,
            final_score=final_score,
            solve_rank=solve_count + 1
        )
        db.session.add(score_record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'base_points': base_points,
            'final_score': final_score,
            'solve_rank': solve_count + 1
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ADMIN DASHBOARD
# ============================================================================

@bp.route('/admin/dashboard', methods=['GET'])
@admins_only
def admin_dashboard():
    """Get admin dashboard data"""
    try:
        return jsonify({
            'stats': {
                'total_users': Users.query.count(),
                'total_teams': Teams.query.count(),
                'total_challenges': Challenges.query.count(),
                'total_solves': Solves.query.count()
            },
            'recent_solves': [{
                'user': s.user.name if s.user else 'Unknown',
                'challenge': s.challenge.name if s.challenge else 'Unknown',
                'timestamp': s.date.isoformat() if s.date else None
            } for s in Solves.query.order_by(Solves.date.desc()).limit(10).all()]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUDIT LOGGING
# ============================================================================

def log_audit(action_type, resource_type, user_id=None, details=None):
    """Log audit event"""
    try:
        current = current_user() if not user_id else None
        audit = AuditLog(
            user_id=user_id or (current.id if current else None),
            action_type=action_type,
            resource_type=resource_type,
            ip_address=request.remote_addr if request else None,
            details=details
        )
        db.session.add(audit)
        db.session.commit()
    except Exception as e:
        logger.error(f"Audit log error: {e}")


@bp.route('/admin/audit-logs', methods=['GET'])
@admins_only
def get_audit_logs():
    """Get audit logs"""
    try:
        logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()
        return jsonify({
            'logs': [{
                'action': log.action_type,
                'resource': log.resource_type,
                'timestamp': log.timestamp.isoformat()
            } for log in logs]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# USER ONBOARDING
# ============================================================================

@bp.route('/onboarding/status', methods=['GET'])
@authed_only
def onboarding_status():
    """Get user onboarding status"""
    try:
        current = current_user()
        onb = UserOnboarding.query.filter_by(user_id=current.id).first()
        if not onb:
            onb = UserOnboarding(user_id=current.id)
            db.session.add(onb)
            db.session.commit()
        
        return jsonify({
            'profile_completed': onb.profile_completed,
            'tutorial_completed': onb.tutorial_completed,
            'progress_percentage': onb.progress_percentage
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/onboarding/mark/<step>', methods=['POST'])
@authed_only
def mark_onboarding(step):
    """Mark onboarding step as complete"""
    try:
        current = current_user()
        onb = UserOnboarding.query.filter_by(user_id=current.id).first()
        if not onb:
            onb = UserOnboarding(user_id=current.id)
            db.session.add(onb)
        
        if step == 'profile':
            onb.profile_completed = True
        elif step == 'tutorial':
            onb.tutorial_completed = True
        
        # Calculate progress
        completed = sum([onb.profile_completed, onb.tutorial_completed])
        onb.progress_percentage = (completed / 2) * 100
        
        db.session.commit()
        return jsonify({'success': True, 'progress': onb.progress_percentage})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# MOBILE API
# ============================================================================

@bp.route('/mobile/challenges', methods=['GET'])
def mobile_challenges():
    """Get challenges for mobile app"""
    try:
        challenges = Challenges.query.filter_by(hidden=False).all()
        return jsonify({
            'challenges': [{
                'id': c.id,
                'name': c.name,
                'points': c.points,
                'category': c.category
            } for c in challenges]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/mobile/leaderboard', methods=['GET'])
def mobile_leaderboard():
    """Get leaderboard for mobile"""
    try:
        top_users = Users.query.filter_by(hidden=False, banned=False)\
            .order_by(Users.score.desc()).limit(50).all()
        
        return jsonify({
            'leaderboard': [{
                'rank': i+1,
                'name': u.name,
                'score': u.score
            } for i, u in enumerate(top_users)]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# CHALLENGE VALIDATION
# ============================================================================

@bp.route('/admin/validate-challenge/<int:challenge_id>', methods=['POST'])
@admins_only
def validate_challenge(challenge_id):
    """Validate a challenge before publication"""
    try:
        challenge = Challenges.query.get(challenge_id)
        if not challenge:
            return jsonify({'error': 'Challenge not found'}), 404
        
        validations = []
        
        if not challenge.flags:
            validations.append({'type': 'flags', 'status': 'failed'})
        else:
            validations.append({'type': 'flags', 'status': 'passed'})
        
        if not challenge.description or len(challenge.description) < 20:
            validations.append({'type': 'description', 'status': 'warning'})
        else:
            validations.append({'type': 'description', 'status': 'passed'})
        
        is_valid = all(v['status'] != 'failed' for v in validations)
        
        return jsonify({
            'success': True,
            'is_valid': is_valid,
            'validations': validations
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ADMIN UI PAGES
# ============================================================================

@admin_bp.route('/features-dashboard', methods=['GET'])
@admins_only
def admin_features_dashboard():
    """Admin dashboard showing all implemented features"""
    try:
        # Get template path
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        template_file = os.path.join(template_dir, 'admin_features.html')
        
        if os.path.exists(template_file):
            with open(template_file, 'r') as f:
                template_content = f.read()
            return render_template_string(template_content)
        else:
            # Fallback if template not found
            return render_template_string('''
            {% extends "base.html" %}
            {% block content %}
            <div class="container mt-5">
                <div class="alert alert-warning">
                    <h4>Admin Features Dashboard</h4>
                    <p>Template file not found. Please verify the installation.</p>
                </div>
            </div>
            {% endblock %}
            ''')
    except Exception as e:
        logger.error(f"Error rendering admin dashboard: {e}")
        return render_template_string('''
        {% extends "base.html" %}
        {% block content %}
        <div class="container mt-5">
            <div class="alert alert-danger">
                <h4>Error Loading Admin Dashboard</h4>
                <p>{{ error }}</p>
            </div>
        </div>
        {% endblock %}
        ''', error=str(e)), 500


@admin_bp.route('/accounts-approval', methods=['GET'])
@admins_only
def admin_accounts_approval():
    """Dedicated accounts approval page"""
    return redirect('/api/ctf/admin/users/dashboard')
    return render_template_string('''
    {% extends "base.html" %}
    {% block content %}
    <div class="container mt-4">
        <div class="row mb-4">
            <div class="col-md-12">
                <h1><i class="fas fa-user-check"></i> Account Approvals</h1>
                <p class="text-muted">Manage pending user registrations</p>
            </div>
        </div>

        <div class="row">
            <div class="col-md-3">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 id="pending-count" class="text-warning">0</h3>
                        <p class="card-text">Pending</p>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 id="approved-count" class="text-success">0</h3>
                        <p class="card-text">Approved</p>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 id="rejected-count" class="text-danger">0</h3>
                        <p class="card-text">Rejected</p>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 id="total-count" class="text-info">0</h3>
                        <p class="card-text">Total</p>
                    </div>
                </div>
            </div>
        </div>

        <div class="row mt-4">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header">
                        <h5 class="card-title mb-0">
                            <i class="fas fa-list"></i> Pending Approvals
                            <button class="btn btn-sm btn-primary float-right" onclick="loadApprovals()">
                                <i class="fas fa-sync"></i> Refresh
                            </button>
                        </h5>
                    </div>
                    <div class="card-body">
                        <table class="table table-striped" id="approvals-table">
                            <thead>
                                <tr>
                                    <th>User ID</th>
                                    <th>Username</th>
                                    <th>Email</th>
                                    <th>Requested</th>
                                    <th>Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="approvals-tbody">
                                <tr><td colspan="6" class="text-center">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <style>
        .card { border: none; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .btn-sm { font-size: 12px; }
    </style>

    <script>
        function getCsrf() {
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.getAttribute('content')) return meta.getAttribute('content');
            if (window.init && window.init.csrfNonce) return window.init.csrfNonce;
            return '';
        }

        async function loadApprovals() {
            try {
                const response = await fetch('/api/impl/admin/approvals');
                if (response.ok) {
                    const data = await response.json();
                    const approvals = data.approvals || [];
                    
                    document.getElementById('pending-count').textContent = 
                        approvals.filter(a => a.status === 'pending').length;
                    document.getElementById('approved-count').textContent = 
                        approvals.filter(a => a.status === 'approved').length;
                    document.getElementById('rejected-count').textContent = 
                        approvals.filter(a => a.status === 'rejected').length;
                    document.getElementById('total-count').textContent = approvals.length;

                    const tbody = document.getElementById('approvals-tbody');
                    if (approvals.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="6" class="text-center">No pending approvals</td></tr>';
                    } else {
                        tbody.innerHTML = approvals.map(a => `
                            <tr>
                                <td>${a.user_id}</td>
                                <td>${a.username || 'N/A'}</td>
                                <td>${a.email || 'N/A'}</td>
                                <td>${new Date(a.requested_at).toLocaleString()}</td>
                                <td>
                                    <span class="badge badge-${a.status === 'pending' ? 'warning' : a.status === 'approved' ? 'success' : 'danger'}">
                                        ${a.status}
                                    </span>
                                </td>
                                <td>
                                    <button class="btn btn-sm btn-success" onclick="approveUser(${a.user_id})"
                                        ${a.status !== 'pending' ? 'disabled' : ''}>Approve</button>
                                    <button class="btn btn-sm btn-danger" onclick="rejectUser(${a.user_id})"
                                        ${a.status !== 'pending' ? 'disabled' : ''}>Reject</button>
                                </td>
                            </tr>
                        `).join('');
                    }
                }
            } catch (error) {
                alert('Error loading approvals: ' + error);
            }
        }

        async function approveUser(id) {
            if (!confirm('Approve this user?')) return;
            try {
                const response = await fetch(`/api/ctf/admin/users/${id}/approval/approved`, {
                    method: 'GET',
                    credentials: 'same-origin'
                });
                if (response.ok) {
                    alert('User approved!');
                    loadApprovals();
                } else {
                    alert('Approve failed: ' + await response.text());
                }
            } catch (error) {
                alert('Error: ' + error);
            }
        }

        async function rejectUser(id) {
            if (!confirm('Reject this user?')) return;
            try {
                const response = await fetch(`/api/ctf/admin/users/${id}/approval/rejected`, {
                    method: 'GET',
                    credentials: 'same-origin'
                });
                if (response.ok) {
                    alert('User rejected!');
                    loadApprovals();
                } else {
                    alert('Reject failed: ' + await response.text());
                }
            } catch (error) {
                alert('Error: ' + error);
            }
        }

        // Load on page load
        document.addEventListener('DOMContentLoaded', loadApprovals);
        setInterval(loadApprovals, 30000); // Auto-refresh every 30 seconds
    </script>
    {% endblock %}
    ''')
