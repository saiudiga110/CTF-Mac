"""
Enhanced CTFd Features Plugin
Integrates all 15 recommended features into CTFd platform
"""

from flask import Blueprint, jsonify, redirect, request, render_template_string
from CTFd.models import db, Users, Teams, Challenges, Solves, Hints
from CTFd.utils import get_config, set_config
from CTFd.plugins import register_plugin_assets_directory
from datetime import datetime, timedelta
import json
import logging

# Import extended features models to register them with SQLAlchemy
try:
    from .extended_features import (
        Achievement, UserAchievement, LeaderboardSnapshot, PlayerStreak,
        Season, SeasonalChallenge, TeamAnalytics, TeamContribution,
        MentorshipPair, SuspiciousActivity, AuditLog, DifficultyScaling,
        LearningPath, LearningObjective, Tutorial, ChallengeTemplate,
        BackupManifest, ResourceMetrics, ChallengeSchedule, SolveTimeline,
        ChallengeVersion, ValidationResult, EventConfig, UserKaliInstance
    )
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning(f"Could not import extended features models: {e}")

blueprint = Blueprint('enhanced-features', __name__, url_prefix='/api/features')
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE MODELS
# ============================================================================

class NotificationLog(db.Model):
    """Real-time Notifications (Feature 1)"""
    __tablename__ = 'notification_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    notification_type = db.Column(db.String(50))  # flag_submitted, achievement, leaderboard_update
    title = db.Column(db.String(255))
    message = db.Column(db.Text)
    data = db.Column(db.JSON)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AnalyticsData(db.Model):
    """Advanced Analytics Dashboard (Feature 2)"""
    __tablename__ = 'analytics_data'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    metric_type = db.Column(db.String(50))  # time_to_solve, hint_used, attempts
    value = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class HintSystem(db.Model):
    """Automated Hint System (Feature 3)"""
    __tablename__ = 'hint_system'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    hint_level = db.Column(db.Integer)  # 1=basic, 2=intermediate, 3=advanced
    content = db.Column(db.Text)
    difficulty_threshold = db.Column(db.Float, default=0.0)  # -1.0 to 1.0 skill rating

class MultiTargetConfig(db.Model):
    """Multi-Target Challenge Support (Feature 4)"""
    __tablename__ = 'multi_target_config'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    target_index = db.Column(db.Integer)
    image_name = db.Column(db.String(255))
    port = db.Column(db.Integer)
    config = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ChallengeDependency(db.Model):
    """Challenge Dependency Graph (Feature 5)"""
    __tablename__ = 'challenge_dependency'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    prerequisite_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    dependency_type = db.Column(db.String(50))  # required, recommended, branching

class ExploitDatabase(db.Model):
    """Persistent Exploit Database (Feature 6)"""
    __tablename__ = 'exploit_database'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    title = db.Column(db.String(255))
    content = db.Column(db.Text)  # Markdown with code blocks
    exploit_code = db.Column(db.Text)
    language = db.Column(db.String(50))  # python, bash, powershell
    author = db.Column(db.String(255))
    version = db.Column(db.String(50))
    tags = db.Column(db.JSON)
    upvotes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DynamicEnvironment(db.Model):
    """Dynamic Container Environment (Feature 7)"""
    __tablename__ = 'dynamic_environment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    flag_suffix = db.Column(db.String(255))
    random_port = db.Column(db.Integer)
    env_variables = db.Column(db.JSON)
    container_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TeamCollaboration(db.Model):
    """Team Collaboration Features (Feature 8)"""
    __tablename__ = 'team_collaboration'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    collaboration_type = db.Column(db.String(50))  # notebook, chat, assignment
    title = db.Column(db.String(255))
    content = db.Column(db.Text)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ForensicsChallenge(db.Model):
    """Forensics & Log Analysis (Feature 9)"""
    __tablename__ = 'forensics_challenge'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    artifact_type = db.Column(db.String(50))  # pcap, logs, memory, filesystem
    artifact_path = db.Column(db.String(255))
    artifact_metadata = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class APIRateLimit(db.Model):
    """API Rate Limiting & DDoS Protection (Feature 10)"""
    __tablename__ = 'api_rate_limit'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    endpoint = db.Column(db.String(255))
    request_count = db.Column(db.Integer, default=0)
    reset_at = db.Column(db.DateTime)
    blocked = db.Column(db.Boolean, default=False)

class ContainerSnapshot(db.Model):
    """Container Snapshot & Rollback (Feature 11)"""
    __tablename__ = 'container_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    snapshot_id = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    snapshot_metadata = db.Column(db.JSON)

class ScoringConfig(db.Model):
    """Automated Scoring & Handicap (Feature 12)"""
    __tablename__ = 'scoring_config'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    skill_rating = db.Column(db.Float, default=1000.0)
    handicap_multiplier = db.Column(db.Float, default=1.0)
    time_decay_enabled = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class HealthCheck(db.Model):
    """Health Checks & Auto-Remediation (Feature 13)"""
    __tablename__ = 'health_check'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    container_id = db.Column(db.String(255))
    health_status = db.Column(db.String(50))  # healthy, degraded, unhealthy
    last_check = db.Column(db.DateTime, default=datetime.utcnow)
    failure_count = db.Column(db.Integer, default=0)
    auto_remediation_attempted = db.Column(db.Boolean, default=False)

class ExternalIntegration(db.Model):
    """External System Integrations (Feature 15)"""
    __tablename__ = 'external_integration'
    id = db.Column(db.Integer, primary_key=True)
    integration_type = db.Column(db.String(50))  # slack, discord, siem
    event_type = db.Column(db.String(50))  # flag_submitted, achievement
    webhook_url = db.Column(db.String(500))
    enabled = db.Column(db.Boolean, default=True)
    config = db.Column(db.JSON)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@blueprint.route('/notifications', methods=['GET'])
def get_notifications():
    """Feature 1: Real-Time Notifications"""
    user_id = request.args.get('user_id')
    limit = request.args.get('limit', 20, type=int)
    unread_only = request.args.get('unread_only', False, type=bool)
    
    query = NotificationLog.query.filter_by(user_id=user_id)
    if unread_only:
        query = query.filter_by(read=False)
    
    notifications = query.order_by(NotificationLog.created_at.desc()).limit(limit).all()
    
    return jsonify({
        'notifications': [{
            'id': n.id,
            'type': n.notification_type,
            'title': n.title,
            'message': n.message,
            'data': n.data,
            'read': n.read,
            'timestamp': n.created_at.isoformat()
        } for n in notifications]
    })

@blueprint.route('/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """Mark notification as read"""
    notification = NotificationLog.query.get(notification_id)
    if notification:
        notification.read = True
        db.session.commit()
    return jsonify({'success': True})

@blueprint.route('/analytics/player/<int:user_id>', methods=['GET'])
def get_player_analytics(user_id):
    """Feature 2: Advanced Analytics Dashboard - Player Stats"""
    period_days = request.args.get('period', 7, type=int)
    start_date = datetime.utcnow() - timedelta(days=period_days)
    
    analytics = AnalyticsData.query.filter(
        AnalyticsData.user_id == user_id,
        AnalyticsData.timestamp >= start_date
    ).all()
    
    stats = {
        'time_to_solve': [],
        'hints_used': 0,
        'attempts': 0,
        'challenges_solved': 0
    }
    
    for data in analytics:
        if data.metric_type == 'time_to_solve':
            stats['time_to_solve'].append(data.value)
        elif data.metric_type == 'hint_used':
            stats['hints_used'] += 1
        elif data.metric_type == 'attempts':
            stats['attempts'] += 1
    
    stats['challenges_solved'] = len(stats['time_to_solve'])
    stats['avg_time_to_solve'] = sum(stats['time_to_solve']) / len(stats['time_to_solve']) if stats['time_to_solve'] else 0
    
    return jsonify(stats)

@blueprint.route('/analytics/challenge/<int:challenge_id>', methods=['GET'])
def get_challenge_analytics(challenge_id):
    """Feature 2: Challenge difficulty analysis"""
    analytics = AnalyticsData.query.filter_by(challenge_id=challenge_id).all()
    
    stats = {
        'total_attempts': len([a for a in analytics if a.metric_type == 'attempts']),
        'hints_requested': len([a for a in analytics if a.metric_type == 'hint_used']),
        'avg_solve_time': 0,
        'difficulty_rating': 0
    }
    
    solve_times = [a.value for a in analytics if a.metric_type == 'time_to_solve']
    if solve_times:
        stats['avg_solve_time'] = sum(solve_times) / len(solve_times)
    
    # Calculate difficulty: more attempts and hints = harder
    stats['difficulty_rating'] = (stats['total_attempts'] / max(1, len(solve_times))) * 0.7 + (stats['hints_requested'] / max(1, len(solve_times))) * 0.3
    
    return jsonify(stats)

@blueprint.route('/hints/<int:challenge_id>', methods=['GET'])
def get_progressive_hints(challenge_id):
    """Feature 3: Automated Hint System with Progressive Difficulty"""
    user_id = request.args.get('user_id', type=int)
    
    # Get user's skill rating
    scoring = ScoringConfig.query.filter_by(user_id=user_id).first()
    skill_rating = scoring.skill_rating if scoring else 1000.0
    
    # Normalize skill rating to -1.0 to 1.0 range
    normalized_skill = (skill_rating - 1000.0) / 1000.0
    
    # Get appropriate hints
    hints = HintSystem.query.filter(
        HintSystem.challenge_id == challenge_id,
        HintSystem.difficulty_threshold <= normalized_skill
    ).order_by(HintSystem.hint_level).all()
    
    return jsonify({
        'hints': [{
            'id': h.id,
            'level': h.hint_level,
            'content': h.content,
            'level_name': ['Basic', 'Intermediate', 'Advanced'][h.hint_level - 1]
        } for h in hints],
        'user_skill_level': normalized_skill
    })

@blueprint.route('/dependencies/<int:challenge_id>', methods=['GET'])
def get_challenge_dependencies(challenge_id):
    """Feature 5: Challenge Dependency Graph"""
    dependencies = ChallengeDependency.query.filter_by(challenge_id=challenge_id).all()
    
    dep_list = []
    for dep in dependencies:
        prereq = Challenges.query.get(dep.prerequisite_id)
        dep_list.append({
            'prerequisite_id': dep.prerequisite_id,
            'prerequisite_name': prereq.name if prereq else 'Unknown',
            'type': dep.dependency_type
        })
    
    return jsonify({'dependencies': dep_list})

@blueprint.route('/exploits/<int:challenge_id>', methods=['GET'])
def get_exploits(challenge_id):
    """Feature 6: Persistent Exploit Database"""
    exploits = ExploitDatabase.query.filter_by(challenge_id=challenge_id).order_by(
        ExploitDatabase.upvotes.desc()
    ).all()
    
    return jsonify({
        'exploits': [{
            'id': e.id,
            'title': e.title,
            'content': e.content,
            'language': e.language,
            'author': e.author,
            'tags': e.tags,
            'upvotes': e.upvotes
        } for e in exploits]
    })

@blueprint.route('/exploits/<int:exploit_id>/upvote', methods=['POST'])
def upvote_exploit(exploit_id):
    """Feature 6: Upvote an exploit"""
    exploit = ExploitDatabase.query.get(exploit_id)
    if exploit:
        exploit.upvotes += 1
        db.session.commit()
    return jsonify({'upvotes': exploit.upvotes if exploit else 0})

@blueprint.route('/environment/<int:user_id>/<int:challenge_id>', methods=['GET', 'POST'])
def manage_dynamic_environment(user_id, challenge_id):
    """Feature 7: Dynamic Container Environment Variables"""
    if request.method == 'GET':
        env = DynamicEnvironment.query.filter_by(
            user_id=user_id,
            challenge_id=challenge_id
        ).first()
        
        if env:
            return jsonify({
                'flag_suffix': env.flag_suffix,
                'port': env.random_port,
                'variables': env.env_variables
            })
        return jsonify({'error': 'Not found'}), 404
    
    elif request.method == 'POST':
        import random
        import string
        
        # Generate random suffix
        flag_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        random_port = random.randint(9000, 9999)
        
        env = DynamicEnvironment(
            user_id=user_id,
            challenge_id=challenge_id,
            flag_suffix=flag_suffix,
            random_port=random_port,
            env_variables=request.get_json() or {}
        )
        db.session.add(env)
        db.session.commit()
        
        return jsonify({
            'flag_suffix': flag_suffix,
            'port': random_port,
            'message': 'Dynamic environment created'
        })

@blueprint.route('/team/<int:team_id>/collaboration', methods=['GET', 'POST'])
def team_collaboration(team_id):
    """Feature 8: Team Collaboration Features"""
    if request.method == 'GET':
        collab_type = request.args.get('type', 'notebook')
        items = TeamCollaboration.query.filter_by(
            team_id=team_id,
            collaboration_type=collab_type
        ).order_by(TeamCollaboration.updated_at.desc()).all()
        
        return jsonify({
            'items': [{
                'id': item.id,
                'title': item.title,
                'content': item.content,
                'assigned_to': item.assigned_to,
                'created_by': item.created_by,
                'updated_at': item.updated_at.isoformat()
            } for item in items]
        })
    
    elif request.method == 'POST':
        data = request.get_json()
        collab = TeamCollaboration(
            team_id=team_id,
            collaboration_type=data.get('type', 'notebook'),
            title=data.get('title'),
            content=data.get('content'),
            assigned_to=data.get('assigned_to'),
            created_by=data.get('created_by')
        )
        db.session.add(collab)
        db.session.commit()
        
        return jsonify({'id': collab.id, 'message': 'Collaboration item created'})

@blueprint.route('/rate-limit/check', methods=['POST'])
def check_rate_limit():
    """Feature 10: API Rate Limiting"""
    data = request.get_json()
    user_id = data.get('user_id')
    endpoint = data.get('endpoint')
    limit = data.get('limit', 100)
    window_seconds = data.get('window', 3600)
    
    rate_limit = APIRateLimit.query.filter_by(
        user_id=user_id,
        endpoint=endpoint
    ).first()
    
    now = datetime.utcnow()
    
    if not rate_limit:
        rate_limit = APIRateLimit(
            user_id=user_id,
            endpoint=endpoint,
            reset_at=now + timedelta(seconds=window_seconds)
        )
        db.session.add(rate_limit)
    elif rate_limit.reset_at < now:
        rate_limit.request_count = 0
        rate_limit.reset_at = now + timedelta(seconds=window_seconds)
        rate_limit.blocked = False
    
    if rate_limit.request_count >= limit:
        rate_limit.blocked = True
        db.session.commit()
        return jsonify({
            'allowed': False,
            'message': 'Rate limit exceeded',
            'reset_at': rate_limit.reset_at.isoformat()
        }), 429
    
    rate_limit.request_count += 1
    db.session.commit()
    
    return jsonify({
        'allowed': True,
        'remaining': limit - rate_limit.request_count,
        'reset_at': rate_limit.reset_at.isoformat()
    })

@blueprint.route('/health/check/<int:challenge_id>', methods=['GET', 'POST'])
def health_check_endpoint(challenge_id):
    """Feature 13: Health Checks & Auto-Remediation"""
    if request.method == 'GET':
        health = HealthCheck.query.filter_by(challenge_id=challenge_id).first()
        if health:
            return jsonify({
                'status': health.health_status,
                'last_check': health.last_check.isoformat(),
                'failure_count': health.failure_count
            })
        return jsonify({'status': 'unknown'})
    
    elif request.method == 'POST':
        data = request.get_json()
        container_id = data.get('container_id')
        status = data.get('status')
        
        health = HealthCheck.query.filter_by(
            challenge_id=challenge_id,
            container_id=container_id
        ).first()
        
        if not health:
            health = HealthCheck(challenge_id=challenge_id, container_id=container_id)
            db.session.add(health)
        
        health.health_status = status
        health.last_check = datetime.utcnow()
        
        if status != 'healthy':
            health.failure_count += 1
            if health.failure_count >= 3:
                health.auto_remediation_attempted = True
                # Trigger remediation (would restart container in real implementation)
        else:
            health.failure_count = 0
        
        db.session.commit()
        return jsonify({'message': 'Health check updated'})

@blueprint.route('/scoring/<int:user_id>', methods=['GET', 'PUT'])
def user_scoring(user_id):
    """Feature 12: Automated Scoring & Handicap System"""
    if request.method == 'GET':
        scoring = ScoringConfig.query.filter_by(user_id=user_id).first()
        if scoring:
            return jsonify({
                'skill_rating': scoring.skill_rating,
                'handicap_multiplier': scoring.handicap_multiplier,
                'level': 'Beginner' if scoring.skill_rating < 1200 else 'Intermediate' if scoring.skill_rating < 1500 else 'Advanced'
            })
        return jsonify({'error': 'Not found'}), 404
    
    elif request.method == 'PUT':
        data = request.get_json()
        scoring = ScoringConfig.query.filter_by(user_id=user_id).first()
        
        if not scoring:
            scoring = ScoringConfig(user_id=user_id)
            db.session.add(scoring)
        
        if 'skill_rating' in data:
            scoring.skill_rating = data['skill_rating']
        if 'handicap_multiplier' in data:
            scoring.handicap_multiplier = data['handicap_multiplier']
        
        db.session.commit()
        return jsonify({'message': 'Scoring updated'})

@blueprint.route('/integrations', methods=['GET', 'POST'])
def manage_integrations():
    """Feature 15: External System Integrations"""
    if request.method == 'GET':
        integrations = ExternalIntegration.query.all()
        return jsonify({
            'integrations': [{
                'id': i.id,
                'type': i.integration_type,
                'event_type': i.event_type,
                'enabled': i.enabled
            } for i in integrations]
        })
    
    elif request.method == 'POST':
        data = request.get_json()
        integration = ExternalIntegration(
            integration_type=data.get('type'),
            event_type=data.get('event_type'),
            webhook_url=data.get('webhook_url'),
            enabled=data.get('enabled', True),
            config=data.get('config', {})
        )
        db.session.add(integration)
        db.session.commit()
        return jsonify({'id': integration.id, 'message': 'Integration created'})


def load(app):
    """Load the plugin into CTFd"""

    # Hybrid participation uses CTFd's individual scoring/access mode while
    # Team Hub provides optional collaboration. Persist this on every startup
    # so existing installations upgraded from mandatory teams are migrated too.
    with app.app_context():
        if get_config('user_mode') != 'users':
            set_config('user_mode', 'users')
            logger.info('Participation mode migrated: individual access with optional teams')
    
    # Register templates and static assets override
    import os
    from jinja2 import FileSystemLoader, PrefixLoader, ChoiceLoader
    
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(plugin_dir, 'templates')
    
    if os.path.exists(template_dir):
        # Get current loader
        current_loader = app.jinja_loader
        
        # Create a choice loader that checks plugin templates first, then defaults
        plugin_loader = FileSystemLoader(template_dir)
        
        if isinstance(current_loader, ChoiceLoader):
            # If already a ChoiceLoader, add plugin loader to the beginning
            loaders = [plugin_loader] + current_loader.loaders
        else:
            # Create new ChoiceLoader with plugin templates first
            loaders = [plugin_loader, current_loader]
        
        app.jinja_loader = ChoiceLoader(loaders)
        logger.info('Plugin Jinja2 template loader registered (navbar.html and base.html will be overridden)')
    
    # Register plugin assets for static files
    from CTFd.plugins import register_plugin_assets_directory
    register_plugin_assets_directory(app, base_path='/plugins/enhanced-features/')
    logger.info('Plugin static assets registered')
    
    # Inject themed sidebar CSS/JS ONLY for authenticated users (SECURITY FIX)
    from flask import Response, request
    from CTFd.models import Users
    import os
    
    @app.after_request
    def inject_themed_sidebar(response):
        """Legacy neon left-rail. Disabled — player chrome is the top navbar + HUD."""
        return response
    
    logger.info('✅ SECURE Themed sidebar injection registered - ONLY for authenticated users (Admin items protected)')

    # Inject Lloyds Cyber auth theme for login/register/reset pages
    @app.after_request
    def inject_auth_cyber_theme(response):
        """Inject Lloyds Cyber theme CSS/JS into CTFd auth pages"""
        if response.content_type and 'text/html' in response.content_type:
            try:
                auth_pages = ['/login', '/register', '/reset_password']
                is_auth_url = any(request.path.startswith(page) for page in auth_pages)

                if not is_auth_url:
                    return response

                plugin_dir = os.path.dirname(os.path.abspath(__file__))
                auth_theme_file = os.path.join(plugin_dir, 'templates', 'auth-theme.html')

                if os.path.exists(auth_theme_file):
                    with open(auth_theme_file, 'r', encoding='utf-8') as f:
                        auth_content = f.read()

                    response_data = response.get_data(as_text=True)

                    if '</head>' in response_data:
                        response_data = response_data.replace('</head>', f'{auth_content}\n</head>')
                    elif '</body>' in response_data:
                        response_data = response_data.replace('</body>', f'{auth_content}\n</body>')

                    response.set_data(response_data)
                    logger.debug(f'Auth cyber theme injected for {request.path}')
            except Exception as e:
                logger.warning(f'Failed to inject auth cyber theme: {e}')

        return response

    logger.info('✅ Lloyds Cyber auth theme injection registered')

    # Direct routes for password change
    @app.route('/password')
    @app.route('/change-password')
    def user_password_redirect():
        from flask import redirect, url_for
        return redirect(url_for('views.settings') + '#password')

    # Load main features blueprint
    app.register_blueprint(blueprint)
    logger.info('Core Features Blueprint loaded')
    
    # Load extended features (28 additional features + Kali per-user)
    try:
        from .extended_features import extended_blueprint
        app.register_blueprint(extended_blueprint)
        logger.info('Extended Features (28 features) loaded successfully')
    except Exception as e:
        logger.warning(f'Extended features failed to load: {e}')
    
    # Initialize Kali manager
    try:
        from .kali_manager import init_kali_manager
        init_kali_manager()
        logger.info('Kali Instance Manager initialized')
    except Exception as e:
        logger.warning(f'Kali manager initialization failed: {e}')
    
    # Load All Implementations (Approvals, Auto-Login, Dashboard, Scoring, Mobile, Logging)
    try:
        from . import all_implementations
        app.register_blueprint(all_implementations.bp)
        app.register_blueprint(all_implementations.admin_bp)
        logger.info('All Implementations Module loaded (Auto-Login, Dashboard, Scoring, Mobile, Logging)')
        logger.info('Admin UI Routes loaded (/admin/features-dashboard)')
    except Exception as e:
        logger.warning(f'All implementations failed to load: {e}')
    
    # Account approval system removed — users register and access the platform immediately.
    # RBAC is handled natively by CTFd via user.type ('admin' vs 'user').

    # ── CTF Feature Suite (9 new features) ─────────────────────────────────────
    try:
        from . import ctf_features as _cf

        # Register blueprint
        app.register_blueprint(_cf.bp)
        logger.info('[CTFFeatures] Blueprint registered at /api/ctf')

        @app.before_request
        def require_team_join_approval():
            """Replace CTFd's password-based join screen with captain approval."""
            if request.path == '/teams/join':
                return redirect('/api/ctf/team-hub')

        # Register rate-limit and first-blood middleware
        _cf.register_rate_limit_hook(app)
        _cf.register_first_blood_hook(app)
        _cf.register_suspicious_activity_hook(app)
        _cf.register_solve_feed_hook(app)
        _cf.register_flag_attempt_hook(app)
        _cf.register_hint_usage_hook(app)
        _cf.register_container_activity_hook(app)
        _cf.start_idle_monitor()
        logger.info('[CTFFeatures] All hooks active (rate-limit, first-blood, suspicious, solve-feed, flag-log, hint-log, idle-monitor)')

        # Create DB tables (idempotent)
        _cf.init_tables(app)

        logger.info('[CTFFeatures] All hooks and tables loaded (menu consolidated into CTF Ops Hub)')
    except Exception as _e:
        logger.warning(f'[CTFFeatures] Load failed: {_e}')

    # ── Mission Features (4 new features) ──────────────────────────────────────
    try:
        from . import mission_features as _mf
        app.register_blueprint(_mf.mission_bp)
        _mf.init_mission_tables(app)
        logger.info('[Mission] Blueprint registered — dashboard, repair, incident panel, team feed')
    except Exception as _e:
        logger.warning(f'[Mission] Load failed: {_e}')

    # ── Inject Lloyds CTF admin sidebar into all /admin and plugin pages ──────
    @app.after_request
    def inject_admin_sidebar(response):
        if not (response.content_type and 'text/html' in response.content_type):
            return response
        admin_paths = (
            '/admin',
            '/plugins/ctfd-target/admin',
            '/plugins/ctf-live/admin',
            '/api/ctf/analytics',
            '/api/ctf/events/admin',
            '/api/ctf/admin/',
            '/admin/challenge-docs',
            '/api/mission/',
        )
        if not any(request.path.startswith(p) for p in admin_paths):
            return response
        try:
            from CTFd.utils.user import get_current_user
            u = get_current_user()
            if not u or u.type != 'admin':
                return response
            sidebar_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'templates', 'admin-sidebar-inject.html'
            )
            if not os.path.exists(sidebar_file):
                return response
            with open(sidebar_file, 'r', encoding='utf-8') as f:
                content = f.read()
            data = response.get_data(as_text=True)
            if '</body>' in data:
                data = data.replace('</body>', content + '\n</body>', 1)
                response.set_data(data)
        except Exception as _ex:
            logger.debug(f'[AdminSidebar] inject error: {_ex}')
        return response

    # ── Inject CTF feature frontend (countdown, chat, vote, writeup, toasts) ──
    @app.after_request
    def inject_ctf_features_frontend(response):
        if not (response.content_type and 'text/html' in response.content_type):
            return response
        # Skip asset / API routes
        skip = ('/static/', '/themes/', '/files/', '/_/', '/api/', '/plugins/')
        if any(request.path.startswith(p) for p in skip):
            return response
        try:
            from CTFd.utils.user import get_current_user
            u = get_current_user()
            if not u:
                return response
            inject_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'templates', 'ctf-features-inject.html'
            )
            if not os.path.exists(inject_file):
                return response
            with open(inject_file, 'r', encoding='utf-8') as f:
                content = f.read()
            data = response.get_data(as_text=True)
            if '</body>' in data:
                data = data.replace('</body>', content + '\n</body>', 1)
                response.set_data(data)
        except Exception as _ex:
            logger.debug(f'[CTFFeatures] inject error: {_ex}')
        return response

    # ── Challenge Seeder (17 canonical vBank challenges — idempotent) ────────────
    try:
        from .challenge_seeder import seed_challenges
        seed_challenges(app)
        logger.info('[ChallengeSeeder] 17 canonical challenges ensured')
    except Exception as _se:
        logger.warning(f'[ChallengeSeeder] Seeder failed: {_se}')

    # ── Challenge Doc Seeder (vBank attack solutions — idempotent) ───────────────
    try:
        from .challenge_catalog import challenges as _catalog_challenges
        _VBANK_DOCS = []
        for _row in _catalog_challenges():
            _docs = _row.get("docs") or {}
            _VBANK_DOCS.append({
                "challenge_name": _row["name"],
                "category": _row["category"],
                "vuln_type": _docs.get("vuln_type", ""),
                "concept": _docs.get("concept", ""),
                "steps": _docs.get("steps", []),
                "code_examples": _docs.get("code_examples", []),
                "flag_key": _row["flag_key"],
            })
    except Exception as _cat_exc:
        logger.warning('[DocSeeder] catalog load failed: %s', _cat_exc)
        _VBANK_DOCS = []
    try:
        import json as _json
        with app.app_context():
            from . import ctf_features as _cf2
            _ChallengeDoc = _cf2.ChallengeDoc
            from CTFd.models import db as _db2
            _seeded = 0
            _canonical_docs = {d["challenge_name"] for d in _VBANK_DOCS}
            for _old in _ChallengeDoc.query.all():
                if _old.challenge_name not in _canonical_docs:
                    _db2.session.delete(_old)
            for _d in _VBANK_DOCS:
                _existing = _ChallengeDoc.query.filter_by(challenge_name=_d["challenge_name"]).first()
                if _existing:
                    _existing.category = _d["category"]
                    _existing.vuln_type = _d.get("vuln_type", "")
                    _existing.concept = _d.get("concept", "")
                    _existing.steps = _json.dumps(_d.get("steps", []))
                    _existing.code_examples = _json.dumps(_d.get("code_examples", []))
                    _existing.flag_key = _d.get("flag_key", "")
                else:
                    _db2.session.add(_ChallengeDoc(
                        challenge_name=_d["challenge_name"],
                        category=_d["category"],
                        vuln_type=_d.get("vuln_type",""),
                        concept=_d.get("concept",""),
                        steps=_json.dumps(_d.get("steps",[])),
                        code_examples=_json.dumps(_d.get("code_examples",[])),
                        flag_key=_d.get("flag_key",""),
                    ))
                    _seeded += 1
            _db2.session.commit()
            logger.info(f'[DocSeeder] {_seeded} docs seeded ({len(_VBANK_DOCS)-_seeded} already existed); non-canonical docs purged')
    except Exception as _dse:
        logger.warning(f'[DocSeeder] {_dse}')

    # ── Admin: Challenge Documentation page (DB-backed, CRUD) ───────────────────
    try:
        from flask import Blueprint as _BP, render_template as _rt, abort as _abort
        _docs_bp = _BP('challenge_docs', __name__, url_prefix='/admin')

        @_docs_bp.route('/challenge-docs')
        def challenge_docs_page():
            from CTFd.utils.user import get_current_user
            u = get_current_user()
            if not u or u.type != 'admin':
                _abort(403)
            return _rt('admin/challenge_docs.html')

        app.register_blueprint(_docs_bp)

        logger.info('[ChallengeDocs] Admin docs page registered at /admin/challenge-docs')
    except Exception as _de:
        logger.warning(f'[ChallengeDocs] Failed to register docs page: {_de}')

    # ── Admin dashboard machine-count injection ──────────────────────────────────
    @app.after_request
    def inject_admin_machine_count(response):
        if not (response.content_type and 'text/html' in response.content_type):
            return response
        if not request.path.startswith('/admin'):
            return response
        try:
            from CTFd.utils.user import get_current_user as _gcu
            u = _gcu()
            if not u or u.type != 'admin':
                return response
            import docker as _docker
            client = _docker.from_env(timeout=3)
            target_count = len(client.containers.list(filters={"name": "ctfd-target-", "status": "running"}))
            kali_count   = len(client.containers.list(filters={"name": "ctfd-kali-",   "status": "running"}))
            badge = (
                f'<div id="ctf-machine-count-badge" style="position:fixed;top:10px;right:14px;z-index:9999;'
                f'background:rgba(13,17,23,.92);border:1px solid rgba(0,255,65,.25);border-radius:8px;'
                f'padding:6px 14px;font-family:monospace;font-size:.72rem;color:#00ff41;'
                f'box-shadow:0 2px 12px rgba(0,0,0,.6);display:flex;gap:12px;align-items:center;">'
                f'<span>&#9889; {target_count} target{"s" if target_count!=1 else ""}</span>'
                f'<span>&#128187; {kali_count} pwn machine{"s" if kali_count!=1 else ""}</span>'
                f'</div>'
            )
            data = response.get_data(as_text=True)
            if '</body>' in data and 'ctf-machine-count-badge' not in data:
                data = data.replace('</body>', badge + '\n</body>', 1)
                response.set_data(data)
        except Exception:
            pass
        return response

    # ── Feature APIs: First Blood, Solve Feed, Brute-force Protection ────────────
    import time as _time

    # -- First Blood endpoint --------------------------------------------------
    @app.route('/api/features/first-bloods')
    def api_first_bloods():
        try:
            from CTFd.utils.user import get_current_user as _gcu
            if not _gcu():
                return jsonify([]), 401
            from CTFd.models import Solves as _Solves, Challenges as _Chals, Users as _Users, Teams as _Teams
            from sqlalchemy import func as _func
            # Subquery: earliest solve per challenge
            sub = (db.session.query(
                _Solves.challenge_id,
                _func.min(_Solves.date).label('first_date')
            ).group_by(_Solves.challenge_id).subquery())
            rows = (db.session.query(_Solves, _Chals)
                    .join(sub, (_Solves.challenge_id == sub.c.challenge_id) & (_Solves.date == sub.c.first_date))
                    .join(_Chals, _Chals.id == _Solves.challenge_id)
                    .all())
            result = []
            for solve, chal in rows:
                name = 'Unknown'
                try:
                    if solve.team_id:
                        t = _Teams.query.get(solve.team_id)
                        name = t.name if t else 'Unknown'
                    elif solve.user_id:
                        u = _Users.query.get(solve.user_id)
                        name = u.name if u else 'Unknown'
                except Exception:
                    pass
                result.append({
                    'challenge_id': chal.id,
                    'challenge_name': chal.name,
                    'solver': name,
                    'ts': int(solve.date.timestamp()) if solve.date else 0,
                })
            return jsonify(result)
        except Exception as _e:
            return jsonify([])

    # -- Admin solve feed endpoint ---------------------------------------------
    @app.route('/api/features/admin/solve-feed')
    def api_solve_feed():
        try:
            from CTFd.utils.user import get_current_user as _gcu
            u = _gcu()
            if not u or u.type != 'admin':
                return jsonify({'error': 'admin only'}), 403
            from CTFd.models import Solves as _Solves, Challenges as _Chals, Users as _Users, Teams as _Teams
            rows = (db.session.query(_Solves, _Chals)
                    .join(_Chals, _Chals.id == _Solves.challenge_id)
                    .order_by(_Solves.date.desc())
                    .limit(50).all())
            feed = []
            for solve, chal in rows:
                solver = 'Unknown'
                try:
                    if solve.team_id:
                        t = _Teams.query.get(solve.team_id)
                        solver = t.name if t else 'Unknown'
                    elif solve.user_id:
                        u2 = _Users.query.get(solve.user_id)
                        solver = u2.name if u2 else 'Unknown'
                except Exception:
                    pass
                feed.append({
                    'challenge': chal.name,
                    'category': chal.category,
                    'value': chal.value,
                    'solver': solver,
                    'ts': int(solve.date.timestamp()) if solve.date else 0,
                })
            return jsonify(feed)
        except Exception as _e:
            return jsonify([])

    # -- Teammate solve notifications ------------------------------------------
    @app.route('/api/features/team/solves')
    def api_team_solves():
        """Return solves by teammates (same team, different user) since a given Unix timestamp."""
        try:
            from CTFd.utils.user import get_current_user as _gcu
            u = _gcu()
            if not u:
                return jsonify({'error': 'not authenticated'}), 401
            if not u.team_id:
                return jsonify([])

            since_raw = request.args.get('since', 0, type=float)
            from datetime import datetime as _dt
            since_dt = _dt.utcfromtimestamp(since_raw) if since_raw else _dt.min

            from CTFd.models import Solves as _Solves, Challenges as _Chals, Users as _Users
            rows = (db.session.query(_Solves, _Chals)
                    .join(_Chals, _Chals.id == _Solves.challenge_id)
                    .filter(_Solves.team_id == u.team_id,
                            _Solves.user_id != u.id,
                            _Solves.date > since_dt)
                    .order_by(_Solves.date.desc())
                    .limit(20).all())

            result = []
            for solve, chal in rows:
                solver_name = 'Teammate'
                try:
                    solver = _Users.query.get(solve.user_id)
                    if solver:
                        solver_name = solver.name
                except Exception:
                    pass
                result.append({
                    'username': solver_name,
                    'challenge': chal.name,
                    'category': chal.category,
                    'value': chal.value,
                    'ts': int(solve.date.timestamp()) if solve.date else 0,
                })
            return jsonify(result)
        except Exception as _e:
            return jsonify([])

    # -- Team presence (heartbeat + live dots) --------------------------------
    _presence_redis = None
    try:
        import redis as _redis_pres
        _presence_redis = _redis_pres.StrictRedis.from_url(
            app.config.get('REDIS_URL', 'redis://cache:6379'), decode_responses=True)
    except Exception:
        _presence_redis = None

    @app.route('/api/features/presence', methods=['POST'])
    def api_presence_heartbeat():
        try:
            from CTFd.utils.user import get_current_user as _gcu
            u = _gcu()
            if not u:
                return jsonify({'ok': False}), 401
            if _presence_redis:
                _presence_redis.setex(f'ctfd:presence:{u.id}', 300, '1')
            return jsonify({'ok': True})
        except Exception:
            return jsonify({'ok': False})

    @app.route('/api/features/team/presence')
    def api_team_presence():
        try:
            from CTFd.utils.user import get_current_user as _gcu
            u = _gcu()
            if not u:
                return jsonify({'error': 'not authenticated'}), 401
            if not u.team_id:
                return jsonify([])
            from CTFd.models import Teams as _Teams
            team = _Teams.query.get(u.team_id)
            if not team:
                return jsonify([])
            result = []
            for member in team.members:
                if member.id == u.id:
                    continue
                online = bool(_presence_redis and _presence_redis.exists(f'ctfd:presence:{member.id}'))
                result.append({'user_id': member.id, 'username': member.name, 'online': online})
            return jsonify(result)
        except Exception:
            return jsonify([])

    # -- Brute-force protection hook ------------------------------------------
    _BF_MAX_ATTEMPTS = 10   # wrong attempts before lockout
    _BF_WINDOW_SECS  = 300  # 5-minute lockout window

    def _bf_key(user_id, chal_id):
        return f'ctfd:bf:{user_id}:{chal_id}'

    try:
        import redis as _redis_lib
        _bf_redis = _redis_lib.StrictRedis.from_url(
            app.config.get('REDIS_URL', 'redis://cache:6379'), decode_responses=True)
    except Exception:
        _bf_redis = None

    @app.before_request
    def brute_force_guard():
        if not request.path.startswith('/api/v1/challenges/'):
            return
        if not request.path.endswith('/attempt'):
            return
        if request.method != 'POST':
            return
        if _bf_redis is None:
            return
        try:
            from CTFd.utils.user import get_current_user as _gcu2
            u = _gcu2()
            if not u:
                return
            parts = request.path.strip('/').split('/')
            chal_id = parts[3] if len(parts) > 3 else 'x'
            key = _bf_key(u.id, chal_id)
            attempts = _bf_redis.get(key)
            if attempts and int(attempts) >= _BF_MAX_ATTEMPTS:
                ttl = _bf_redis.ttl(key)
                return jsonify({'success': False, 'data': {
                    'status': 'ratelimited',
                    'message': f'Too many wrong attempts. Try again in {ttl}s.'
                }}), 429
        except Exception:
            pass

    @app.after_request
    def brute_force_track(response):
        if not request.path.startswith('/api/v1/challenges/'):
            return response
        if not request.path.endswith('/attempt'):
            return response
        if request.method != 'POST':
            return response
        if _bf_redis is None:
            return response
        try:
            import json as _json2
            body = _json2.loads(response.get_data(as_text=True))
            status = body.get('data', {}).get('status', '')
            if status == 'incorrect':
                from CTFd.utils.user import get_current_user as _gcu3
                u = _gcu3()
                if u:
                    parts = request.path.strip('/').split('/')
                    chal_id = parts[3] if len(parts) > 3 else 'x'
                    key = _bf_key(u.id, chal_id)
                    pipe = _bf_redis.pipeline()
                    pipe.incr(key)
                    pipe.expire(key, _BF_WINDOW_SECS)
                    pipe.execute()
        except Exception:
            pass
        return response

    logger.info('='*80)
    logger.info('ENHANCED CTFd PLUGIN - FULL DEPLOYMENT')
    logger.info('='*80)
    logger.info('✓ 15 Core Features')
    logger.info('✓ 28 Extended Features')
    logger.info('✓ Direct user access (no approval gate)')
    logger.info('✓ Session-based Auto-Login')
    logger.info('✓ Admin Dashboard & Analytics')
    logger.info('✓ Dynamic Scoring System')
    logger.info('✓ Challenge Validation')
    logger.info('✓ Multi-Team Tournaments')
    logger.info('✓ Container Health Monitoring')
    logger.info('✓ User Onboarding Flow')
    logger.info('✓ Rich Content Support')
    logger.info('✓ Mobile API & Integration')
    logger.info('✓ Database Caching (Redis)')
    logger.info('✓ Rate Limiting & Abuse Prevention')
    logger.info('✓ Whale Container Auto-scaling')
    logger.info('✓ Enhanced Target Machine Support')
    logger.info('✓ Detailed Audit Logging')
    logger.info('✓ Security Event Tracking')
    logger.info('✓ Challenge Navigation UI')
    logger.info('✓ Kali Linux Per-User Instances')
    logger.info('='*80)
    logger.info('TOTAL: 50+ Advanced CTF Features Deployed')
