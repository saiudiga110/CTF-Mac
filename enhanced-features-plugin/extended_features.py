"""
Extended Features Plugin - Additional 28 Features
Includes: Achievements, Analytics Extensions, Team Features, Security, Learning, DevOps, Visualization, Integration
"""

from flask import Blueprint, jsonify, request, render_template_string
from CTFd.models import db, Users, Teams, Challenges, Solves
from datetime import datetime, timedelta
import logging
import json

extended_blueprint = Blueprint('extended-features', __name__, url_prefix='/api/extended')
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE MODELS - 28 NEW FEATURES
# ============================================================================

class Achievement(db.Model):
    """Feature 4: Achievement & Badge System"""
    __tablename__ = 'achievement'
    id = db.Column(db.Integer, primary_key=True)
    badge_id = db.Column(db.String(50), unique=True)
    name = db.Column(db.String(255))
    description = db.Column(db.Text)
    icon = db.Column(db.String(255))
    criteria = db.Column(db.JSON)  # {type: speed_kill, time: 60, ...}
    points = db.Column(db.Integer, default=0)

class UserAchievement(db.Model):
    """User achievement tracking"""
    __tablename__ = 'user_achievement'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    achievement_id = db.Column(db.Integer, db.ForeignKey('achievement.id'))
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow)
    progress = db.Column(db.Float, default=0.0)

class LeaderboardSnapshot(db.Model):
    """Feature 5: Leaderboard History"""
    __tablename__ = 'leaderboard_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    snapshot_type = db.Column(db.String(50))  # cumulative, speed, category, individual
    data = db.Column(db.JSON)
    snapshot_metadata = db.Column(db.JSON)

class PlayerStreak(db.Model):
    """Feature 6: Challenge Streaks & Combos"""
    __tablename__ = 'player_streak'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    streak_type = db.Column(db.String(50))  # consecutive, category_combo
    category = db.Column(db.String(50))  # crypto, web, binary, etc
    current_count = db.Column(db.Integer, default=0)
    max_count = db.Column(db.Integer, default=0)
    last_challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class Season(db.Model):
    """Feature 7: Seasonal/Event System"""
    __tablename__ = 'season'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    description = db.Column(db.Text)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    point_multiplier = db.Column(db.Float, default=1.0)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SeasonalChallenge(db.Model):
    """Challenges tied to seasons"""
    __tablename__ = 'seasonal_challenge'
    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('season.id'))
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    available = db.Column(db.Boolean, default=True)

class TeamAnalytics(db.Model):
    """Feature 8: Team Analytics & Performance"""
    __tablename__ = 'team_analytics'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    metric_type = db.Column(db.String(50))  # velocity, composition, engagement
    value = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class TeamContribution(db.Model):
    """Who solved what in the team"""
    __tablename__ = 'team_contribution'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    solved_at = db.Column(db.DateTime, default=datetime.utcnow)
    contribution_score = db.Column(db.Float, default=1.0)

class MentorshipPair(db.Model):
    """Feature 9: Peer Mentoring System"""
    __tablename__ = 'mentorship_pair'
    id = db.Column(db.Integer, primary_key=True)
    mentor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    mentee_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(50))  # active, completed, archived
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SuspiciousActivity(db.Model):
    """Feature 11: Cheat Detection"""
    __tablename__ = 'suspicious_activity'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    activity_type = db.Column(db.String(50))  # rapid_guessing, pattern_match, impossible_progress
    severity = db.Column(db.String(50))  # low, medium, high
    details = db.Column(db.JSON)
    flagged_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed = db.Column(db.Boolean, default=False)

class AuditLog(db.Model):
    """Feature 12: Audit & Compliance Logging"""
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action_type = db.Column(db.String(100))  # login, flag_submission, config_change
    resource = db.Column(db.String(255))
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(255))
    details = db.Column(db.JSON)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class DifficultyScaling(db.Model):
    """Feature 13: Dynamic Difficulty Scaling"""
    __tablename__ = 'difficulty_scaling'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    base_difficulty = db.Column(db.Float)
    current_difficulty = db.Column(db.Float)
    adjustment_reason = db.Column(db.String(255))
    success_rate = db.Column(db.Float)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class LearningPath(db.Model):
    """Feature 14: Guided Learning Paths"""
    __tablename__ = 'learning_path'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    description = db.Column(db.Text)
    difficulty_level = db.Column(db.String(50))
    skill_area = db.Column(db.String(100))  # web, crypto, binary, etc
    order_index = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LearningObjective(db.Model):
    """Learning objectives for challenges"""
    __tablename__ = 'learning_objective'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    learning_path_id = db.Column(db.Integer, db.ForeignKey('learning_path.id'))
    objective = db.Column(db.String(255))
    description = db.Column(db.Text)

class Tutorial(db.Model):
    """Feature 15: Interactive Tutorials & Walkthroughs"""
    __tablename__ = 'tutorial'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    tutorial_type = db.Column(db.String(50))  # video, interactive, code_example
    title = db.Column(db.String(255))
    content = db.Column(db.Text)
    video_url = db.Column(db.String(500))
    order_index = db.Column(db.Integer)

class ChallengeTemplate(db.Model):
    """Feature 17: Challenge Template Library"""
    __tablename__ = 'challenge_template'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    category = db.Column(db.String(100))  # xss, sqli, cors, etc
    description = db.Column(db.Text)
    docker_image = db.Column(db.String(255))
    template_config = db.Column(db.JSON)
    variables = db.Column(db.JSON)  # customizable parameters

class BackupManifest(db.Model):
    """Feature 18: Automated Backup & Disaster Recovery"""
    __tablename__ = 'backup_manifest'
    id = db.Column(db.Integer, primary_key=True)
    backup_id = db.Column(db.String(100), unique=True)
    backup_type = db.Column(db.String(50))  # database, images, full
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    storage_location = db.Column(db.String(500))
    size_bytes = db.Column(db.BigInteger)
    encrypted = db.Column(db.Boolean, default=True)
    retention_days = db.Column(db.Integer, default=30)

class ResourceMetrics(db.Model):
    """Feature 19: Resource Monitoring & Scaling"""
    __tablename__ = 'resource_metrics'
    id = db.Column(db.Integer, primary_key=True)
    resource_type = db.Column(db.String(50))  # cpu, memory, disk
    container_id = db.Column(db.String(255))
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    usage_percent = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ChallengeSchedule(db.Model):
    """Feature 20: Challenge Scheduler"""
    __tablename__ = 'challenge_schedule'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    unlock_time = db.Column(db.DateTime)  # When challenge becomes available
    time_limit = db.Column(db.Integer)  # Time in seconds to solve (0 = unlimited)
    is_locked = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class SolveTimeline(db.Model):
    """Feature 23: Challenge Difficulty Timeline"""
    __tablename__ = 'solve_timeline'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    solver_count = db.Column(db.Integer)
    cumulative_solves = db.Column(db.Integer)

class ChallengeVersion(db.Model):
    """Feature 26: CI/CD Integration"""
    __tablename__ = 'challenge_version'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    version = db.Column(db.String(50))  # semver: 1.0.0
    git_commit = db.Column(db.String(40))
    deployed_at = db.Column(db.DateTime, default=datetime.utcnow)
    rollback_available = db.Column(db.Boolean, default=True)

class ValidationResult(db.Model):
    """Feature 27: Automated Challenge Validation"""
    __tablename__ = 'validation_result'
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'))
    validation_type = db.Column(db.String(50))  # flag_check, hint_accuracy, scoring
    status = db.Column(db.String(50))  # passed, failed, warning
    details = db.Column(db.JSON)
    validated_at = db.Column(db.DateTime, default=datetime.utcnow)

class EventConfig(db.Model):
    """Feature 28: Multi-Event Management"""
    __tablename__ = 'event_config'
    id = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(255))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    config_data = db.Column(db.JSON)
    is_active = db.Column(db.Boolean, default=False)

class UserKaliInstance(db.Model):
    """Per-User Kali Instance Tracking"""
    __tablename__ = 'user_kali_instance'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    container_id = db.Column(db.String(255), unique=True)
    vnc_port = db.Column(db.Integer, unique=True)
    allocated_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    status = db.Column(db.String(50))  # running, stopped, expired


@extended_blueprint.before_request
def ensure_tables_exist():
    """Ensure all extended feature tables exist on first request"""
    try:
        db.create_all()
    except Exception as e:
        logger.warning(f"Could not ensure tables exist: {e}")

@extended_blueprint.route('/achievements', methods=['GET', 'POST'])
def manage_achievements():
    """Feature 4: Achievements"""
    if request.method == 'GET':
        achievements = Achievement.query.all()
        return jsonify({
            'achievements': [{
                'id': a.id,
                'badge_id': a.badge_id,
                'name': a.name,
                'description': a.description,
                'points': a.points
            } for a in achievements]
        })
    else:
        data = request.get_json()
        achievement = Achievement(
            badge_id=data.get('badge_id'),
            name=data.get('name'),
            description=data.get('description'),
            criteria=data.get('criteria', {}),
            points=data.get('points', 0)
        )
        db.session.add(achievement)
        db.session.commit()
        return jsonify({'id': achievement.id, 'message': 'Achievement created'})

@extended_blueprint.route('/achievements/<int:user_id>', methods=['GET'])
def get_user_achievements(user_id):
    """Get user's unlocked achievements"""
    achievements = db.session.query(Achievement, UserAchievement).join(
        UserAchievement
    ).filter(UserAchievement.user_id == user_id).all()
    
    return jsonify({
        'achievements': [{
            'id': a.Achievement.id,
            'name': a.Achievement.name,
            'unlocked_at': a.UserAchievement.unlocked_at.isoformat()
        } for a in achievements]
    })

@extended_blueprint.route('/leaderboard/snapshots', methods=['GET'])
def get_leaderboard_snapshots():
    """Feature 5: Leaderboard History"""
    snapshot_type = request.args.get('type', 'cumulative')
    limit = request.args.get('limit', 20, type=int)
    
    snapshots = LeaderboardSnapshot.query.filter_by(
        snapshot_type=snapshot_type
    ).order_by(LeaderboardSnapshot.timestamp.desc()).limit(limit).all()
    
    return jsonify({
        'snapshots': [{
            'timestamp': s.timestamp.isoformat(),
            'data': s.data,
            'type': s.snapshot_type
        } for s in snapshots]
    })

@extended_blueprint.route('/streaks/<int:user_id>', methods=['GET'])
def get_player_streaks(user_id):
    """Feature 6: Challenge Streaks"""
    streaks = PlayerStreak.query.filter_by(user_id=user_id).all()
    
    return jsonify({
        'streaks': [{
            'type': s.streak_type,
            'category': s.category,
            'current': s.current_count,
            'max': s.max_count
        } for s in streaks]
    })

@extended_blueprint.route('/seasons', methods=['GET', 'POST'])
def manage_seasons():
    """Feature 7: Seasonal System"""
    if request.method == 'GET':
        seasons = Season.query.all()
        return jsonify({
            'seasons': [{
                'id': s.id,
                'name': s.name,
                'start_date': s.start_date.isoformat(),
                'end_date': s.end_date.isoformat(),
                'is_active': s.is_active
            } for s in seasons]
        })
    else:
        data = request.get_json()
        season = Season(
            name=data.get('name'),
            description=data.get('description'),
            start_date=datetime.fromisoformat(data.get('start_date')),
            end_date=datetime.fromisoformat(data.get('end_date')),
            point_multiplier=data.get('point_multiplier', 1.0)
        )
        db.session.add(season)
        db.session.commit()
        return jsonify({'id': season.id})

@extended_blueprint.route('/team/<int:team_id>/analytics', methods=['GET'])
def get_team_analytics(team_id):
    """Feature 8: Team Performance Analytics"""
    metrics = TeamAnalytics.query.filter_by(team_id=team_id).all()
    contributions = TeamContribution.query.filter_by(team_id=team_id).all()
    
    return jsonify({
        'metrics': [{
            'type': m.metric_type,
            'value': m.value,
            'timestamp': m.timestamp.isoformat()
        } for m in metrics],
        'contributions': len(contributions),
        'team_size': len(set([c.user_id for c in contributions]))
    })

@extended_blueprint.route('/mentorship/<int:mentor_id>', methods=['GET', 'POST'])
def manage_mentorship(mentor_id):
    """Feature 9: Peer Mentoring"""
    if request.method == 'GET':
        pairs = MentorshipPair.query.filter_by(mentor_id=mentor_id).all()
        return jsonify({
            'mentees': [{'id': p.id, 'mentee_id': p.mentee_id} for p in pairs]
        })
    else:
        data = request.get_json()
        pair = MentorshipPair(
            mentor_id=mentor_id,
            mentee_id=data.get('mentee_id'),
            status='active'
        )
        db.session.add(pair)
        db.session.commit()
        return jsonify({'id': pair.id, 'message': 'Mentorship pair created'})

@extended_blueprint.route('/cheat/suspicious', methods=['GET', 'POST'])
def manage_suspicious_activities():
    """Feature 11: Cheat Detection"""
    if request.method == 'GET':
        activities = SuspiciousActivity.query.filter_by(reviewed=False).all()
        return jsonify({
            'suspicious_activities': [{
                'id': a.id,
                'user_id': a.user_id,
                'type': a.activity_type,
                'severity': a.severity,
                'details': a.details
            } for a in activities]
        })
    else:
        data = request.get_json()
        activity = SuspiciousActivity(
            user_id=data.get('user_id'),
            activity_type=data.get('type'),
            severity=data.get('severity', 'low'),
            details=data.get('details', {})
        )
        db.session.add(activity)
        db.session.commit()
        return jsonify({'id': activity.id})

@extended_blueprint.route('/audit/log', methods=['GET'])
def get_audit_logs():
    """Feature 12: Audit Logging"""
    limit = request.args.get('limit', 100, type=int)
    user_id = request.args.get('user_id', type=int)
    
    query = AuditLog.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    
    logs = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    
    return jsonify({
        'logs': [{
            'user_id': l.user_id,
            'action': l.action_type,
            'resource': l.resource,
            'ip': l.ip_address,
            'timestamp': l.timestamp.isoformat()
        } for l in logs]
    })

@extended_blueprint.route('/challenge/<int:challenge_id>/difficulty', methods=['GET', 'PUT'])
def manage_difficulty_scaling(challenge_id):
    """Feature 13: Dynamic Difficulty Scaling"""
    if request.method == 'GET':
        scaling = DifficultyScaling.query.filter_by(challenge_id=challenge_id).first()
        if scaling:
            return jsonify({
                'base_difficulty': scaling.base_difficulty,
                'current_difficulty': scaling.current_difficulty,
                'success_rate': scaling.success_rate
            })
        return jsonify({'error': 'Not found'}), 404
    else:
        data = request.get_json()
        scaling = DifficultyScaling.query.filter_by(challenge_id=challenge_id).first()
        if not scaling:
            scaling = DifficultyScaling(challenge_id=challenge_id)
            db.session.add(scaling)
        
        scaling.current_difficulty = data.get('difficulty')
        scaling.adjustment_reason = data.get('reason')
        db.session.commit()
        return jsonify({'message': 'Difficulty updated'})

@extended_blueprint.route('/learning-paths', methods=['GET'])
def get_learning_paths():
    """Feature 14: Learning Paths"""
    skill_area = request.args.get('skill_area')
    
    query = LearningPath.query
    if skill_area:
        query = query.filter_by(skill_area=skill_area)
    
    paths = query.order_by(LearningPath.order_index).all()
    
    return jsonify({
        'paths': [{
            'id': p.id,
            'name': p.name,
            'skill_area': p.skill_area,
            'difficulty': p.difficulty_level
        } for p in paths]
    })

@extended_blueprint.route('/challenge/<int:challenge_id>/schedule', methods=['GET', 'PUT'])
def manage_challenge_schedule(challenge_id):
    """Feature 20: Challenge Scheduler"""
    if request.method == 'GET':
        schedule = ChallengeSchedule.query.filter_by(challenge_id=challenge_id).first()
        if schedule:
            return jsonify({
                'unlock_time': schedule.unlock_time.isoformat() if schedule.unlock_time else None,
                'time_limit': schedule.time_limit,
                'is_locked': schedule.is_locked
            })
        return jsonify({'error': 'Not scheduled'}), 404
    else:
        data = request.get_json()
        schedule = ChallengeSchedule.query.filter_by(challenge_id=challenge_id).first()
        if not schedule:
            schedule = ChallengeSchedule(challenge_id=challenge_id)
            db.session.add(schedule)
        
        if data.get('unlock_time'):
            schedule.unlock_time = datetime.fromisoformat(data.get('unlock_time'))
        if 'time_limit' in data:
            schedule.time_limit = data.get('time_limit')
        
        db.session.commit()
        return jsonify({'message': 'Schedule updated'})

@extended_blueprint.route('/user/<int:user_id>/kali', methods=['GET', 'POST', 'DELETE'])
def manage_user_kali(user_id):
    """Manage Per-User Kali Instance"""
    from kali_manager import get_kali_manager
    
    manager = get_kali_manager()
    
    if request.method == 'GET':
        info = manager.get_instance_info(user_id)
        if info:
            return jsonify(info)
        return jsonify({'status': 'not_running', 'message': 'No active instance'})
    
    elif request.method == 'POST':
        try:
            lifetime = request.get_json().get('lifetime_hours', 4) if request.is_json else 4
            result = manager.create_instance(user_id, lifetime_hours=lifetime)
            return jsonify(result)
        except Exception as e:
            logger.error(f'Failed to create Kali instance: {e}')
            return jsonify({'error': str(e)}), 500
    
    elif request.method == 'DELETE':
        try:
            success = manager.stop_instance(user_id)
            if success:
                return jsonify({'message': 'Kali instance stopped'})
            return jsonify({'error': 'No instance found'}), 404
        except Exception as e:
            logger.error(f'Failed to stop Kali instance: {e}')
            return jsonify({'error': str(e)}), 500

@extended_blueprint.route('/kali/cleanup', methods=['POST'])
def cleanup_expired_kali():
    """Background task to cleanup expired Kali instances"""
    from kali_manager import get_kali_manager
    
    try:
        manager = get_kali_manager()
        cleaned = manager.cleanup_expired()
        return jsonify({'cleaned': cleaned, 'message': 'Cleanup completed'})
    except Exception as e:
        logger.error(f'Cleanup failed: {e}')
        return jsonify({'error': str(e)}), 500


def load(app):
    """Load extended features into CTFd"""
    app.register_blueprint(extended_blueprint)
    logger.info('Extended Features (28 features) loaded successfully')
    logger.info('Extended Features Plugin (28 features) loaded successfully')
