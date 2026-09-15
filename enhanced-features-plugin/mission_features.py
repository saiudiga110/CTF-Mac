"""
Mission Features  —  4 player/organiser quality-of-life upgrades
  1. Player Mission Dashboard    /api/mission/dashboard  (data) + /mission
  2. Container Health + Self-Heal /api/mission/container/*
  3. Organizer Incident Panel    /api/mission/admin/incidents  + /mission/incident-panel
  4. Team Collaboration Feed     /api/mission/team/feed  + /mission/team-feed
"""
import os
import time
import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, render_template
from CTFd.models import db, Users, Teams, Challenges, Solves
from CTFd.utils.user import get_current_user
from CTFd.utils.decorators import admins_only, authed_only

logger = logging.getLogger(__name__)

mission_bp = Blueprint('mission', __name__, url_prefix='/api/mission')


# ─────────────────────────────────────────────────────────────────────────────
# DB MODEL  — notes in the team collaboration feed
# ─────────────────────────────────────────────────────────────────────────────

class TeamFeedNote(db.Model):
    __tablename__ = 'mission_team_feed_note'
    id          = db.Column(db.Integer, primary_key=True)
    team_id     = db.Column(db.Integer, db.ForeignKey('teams.id'), index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'))
    username    = db.Column(db.String(128))
    content     = db.Column(db.Text)
    pinned      = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    return ''.join(c if c.isalnum() or c == '-' else '-' for c in name.lower()).strip('-')


def _docker():
    import docker
    return docker.from_env(timeout=8)


def _container_snapshot(username: str, role: str) -> dict:
    """Return a health snapshot for one container, reading Docker directly."""
    prefix  = 'ctfd-target-' if role == 'target' else 'ctfd-kali-'
    name    = f'{prefix}{_safe(username)}'
    host_ip = os.environ.get('HOST_IP', '127.0.0.1')
    base    = {
        'status': 'not_found', 'url': None,
        'expires_at': 0, 'remaining_sec': 0,
        'restart_count': 0, 'health': 'none', 'role': role, 'name': name,
    }
    try:
        c      = _docker().containers.get(name)
        labels = c.labels
        exp    = float(labels.get('ctfd_target_expires', 0))
        remaining = max(0, int(exp - time.time()))

        url = None
        for _p, bindings in (c.ports or {}).items():
            if bindings:
                url = f'http://{host_ip}:{bindings[0]["HostPort"]}/'
                break

        health = (c.attrs.get('State', {}).get('Health') or {}).get('Status', 'none')
        status = c.status
        if status == 'running' and health not in ('healthy', 'none', ''):
            status = f'running:{health}'

        return {**base,
                'status': status, 'url': url,
                'expires_at': exp, 'remaining_sec': remaining,
                'restart_count': c.attrs.get('RestartCount', 0),
                'health': health}
    except Exception:
        return base


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1  —  PLAYER MISSION DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@mission_bp.route('/dashboard')
@authed_only
def mission_dashboard_page():
    return render_template('mission-dashboard.html')


@mission_bp.route('/dashboard/data')
@authed_only
def dashboard_data():
    u = get_current_user()

    # ── challenge progress ────────────────────────────────────────────────────
    all_chals   = Challenges.query.filter_by(state='visible').order_by(
                      Challenges.category, Challenges.value).all()
    my_solves   = {s.challenge_id for s in Solves.query.filter_by(user_id=u.id).all()}
    team_solves = set()
    if u.team_id:
        team_solves = {s.challenge_id
                       for s in Solves.query.filter_by(team_id=u.team_id).all()}

    cats: dict = {}
    for c in all_chals:
        cat = c.category
        if cat not in cats:
            cats[cat] = {'total': 0, 'solved_by_me': 0, 'solved_by_team': 0}
        cats[cat]['total'] += 1
        if c.id in my_solves:   cats[cat]['solved_by_me']   += 1
        if c.id in team_solves: cats[cat]['solved_by_team'] += 1

    challenges = [
        {'id': c.id, 'name': c.name, 'category': c.category, 'value': c.value,
         'solved_by_me': c.id in my_solves, 'solved_by_team': c.id in team_solves}
        for c in all_chals
    ]

    # ── hints used ────────────────────────────────────────────────────────────
    hints = []
    try:
        from .ctf_features import HintUsageLog
        rows = (HintUsageLog.query.filter_by(user_id=u.id)
                .order_by(HintUsageLog.unlocked_at.desc()).limit(10).all())
        hints = [{'challenge': r.challenge_name, 'challenge_id': r.challenge_id,
                  'cost': r.cost, 'ts': int(r.unlocked_at.timestamp())} for r in rows]
    except Exception:
        pass

    # ── recent flag attempts ──────────────────────────────────────────────────
    attempts = []
    try:
        from .ctf_features import FlagAttemptLog
        rows = (FlagAttemptLog.query.filter_by(user_id=u.id)
                .order_by(FlagAttemptLog.created_at.desc()).limit(15).all())
        attempts = [{'challenge': r.challenge_name, 'challenge_id': r.challenge_id,
                     'correct': r.correct, 'ts': int(r.created_at.timestamp())} for r in rows]
    except Exception:
        pass

    # ── team activity ─────────────────────────────────────────────────────────
    team_activity = []
    if u.team_id:
        try:
            from CTFd.models import Users as _U
            rows = (db.session.query(Solves, Challenges, _U)
                    .join(Challenges, Challenges.id == Solves.challenge_id)
                    .join(_U, _U.id == Solves.user_id)
                    .filter(Solves.team_id == u.team_id)
                    .order_by(Solves.date.desc()).limit(25).all())
            for sv, ch, solver in rows:
                team_activity.append({
                    'type': 'solve',
                    'username': solver.name,
                    'by_me': solver.id == u.id,
                    'challenge': ch.name,
                    'category': ch.category,
                    'value': ch.value,
                    'ts': int(sv.date.timestamp()) if sv.date else 0,
                })
        except Exception:
            pass

    return jsonify({
        'user': {'id': u.id, 'name': u.name},
        'target': _container_snapshot(u.name, 'target'),
        'kali':   _container_snapshot(u.name, 'kali'),
        'progress': {
            'total': len(all_chals),
            'solved': len(my_solves),
            'team_solved': len(team_solves),
            'categories': [{'name': k, **v} for k, v in sorted(cats.items())],
        },
        'challenges':    challenges,
        'hints':         hints,
        'attempts':      attempts,
        'team_activity': team_activity,
        'ts': int(time.time()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2  —  CONTAINER HEALTH + SELF-HEAL
# ─────────────────────────────────────────────────────────────────────────────

@mission_bp.route('/container/status')
@authed_only
def container_status():
    u = get_current_user()
    return jsonify({
        'target': _container_snapshot(u.name, 'target'),
        'kali':   _container_snapshot(u.name, 'kali'),
    })


@mission_bp.route('/container/repair', methods=['POST'])
@authed_only
def container_repair():
    """Remove a crashed/unhealthy container so the player can restart it cleanly."""
    u    = get_current_user()
    data = request.get_json(silent=True) or {}
    role = data.get('role', 'target')
    if role not in ('target', 'kali'):
        return jsonify({'error': 'role must be target or kali'}), 400

    prefix = 'ctfd-target-' if role == 'target' else 'ctfd-kali-'
    name   = f'{prefix}{_safe(u.name)}'
    try:
        client = _docker()
        try:
            client.containers.get(name).remove(force=True)
            logger.info(f'[Repair] removed {name} for {u.name}')
        except Exception:
            pass  # already gone — that's fine

        start_url = (
            '/plugins/ctfd-target/target/start'
            if role == 'target' else
            '/plugins/ctfd-target/kali/start'
        )
        return jsonify({
            'success': True,
            'message': f'{role.capitalize()} container cleared. Click Launch to start fresh.',
            'status':    _container_snapshot(u.name, role),
            'start_url': start_url,
        })
    except Exception as e:
        logger.error(f'[Repair] {u.name}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 3  —  ORGANIZER INCIDENT PANEL
# ─────────────────────────────────────────────────────────────────────────────

@mission_bp.route('/incident-panel')
@admins_only
def incident_panel_page():
    return render_template('admin/incident_panel.html')


@mission_bp.route('/admin/incidents')
@admins_only
def incidents():
    stuck_min   = request.args.get('stuck_min',        30,  type=int)
    spam_window = request.args.get('spam_window',      300, type=int)
    spam_thresh = request.args.get('spam_threshold',   8,   type=int)
    loop_thresh = request.args.get('restart_threshold', 3,  type=int)

    return jsonify({
        'stuck_players':  _stuck_players(stuck_min),
        'flag_spammers':  _flag_spammers(spam_window, spam_thresh),
        'dead_containers': _dead_containers(),
        'restart_loops':  _restart_loops(loop_thresh),
        'ts': int(time.time()),
        'thresholds': {
            'stuck_min': stuck_min,
            'spam_window_sec': spam_window,
            'spam_threshold': spam_thresh,
            'restart_threshold': loop_thresh,
        },
    })


@mission_bp.route('/admin/incidents/resolve', methods=['POST'])
@admins_only
def resolve_incident():
    data   = request.get_json(silent=True) or {}
    action = data.get('action')
    name   = data.get('container_name', '')

    if action == 'kill_container' and name:
        try:
            _docker().containers.get(name).remove(force=True)
            return jsonify({'success': True, 'message': f'Killed {name}'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'error': 'unknown action'}), 400


def _stuck_players(threshold_min: int = 30) -> list:
    try:
        cutoff     = datetime.utcnow() - timedelta(minutes=threshold_min)
        all_users  = Users.query.filter(
            Users.type == 'user', Users.banned == False, Users.hidden == False
        ).all()
        solved_ids = {s.user_id for s in
                      Solves.query.with_entities(Solves.user_id).all()}
        result = []
        for u in all_users:
            if u.id in solved_ids:
                continue
            created = getattr(u, 'created', None)
            if created and created > cutoff:
                continue
            ago = int((datetime.utcnow() - created).total_seconds() / 60) if created else 0
            result.append({
                'user_id': u.id, 'username': u.name,
                'joined_min_ago': ago, 'team_id': u.team_id,
            })
        result.sort(key=lambda x: x['joined_min_ago'], reverse=True)
        return result[:50]
    except Exception as e:
        logger.debug(f'[Incidents] stuck_players: {e}')
        return []


def _flag_spammers(window_sec: int = 300, threshold: int = 8) -> list:
    try:
        from .ctf_features import FlagAttemptLog
        since = datetime.utcnow() - timedelta(seconds=window_sec)
        rows  = (db.session.query(
                     FlagAttemptLog.user_id,
                     FlagAttemptLog.username,
                     db.func.count(FlagAttemptLog.id).label('cnt'),
                 )
                 .filter(FlagAttemptLog.correct == False,
                         FlagAttemptLog.created_at >= since)
                 .group_by(FlagAttemptLog.user_id, FlagAttemptLog.username)
                 .having(db.func.count(FlagAttemptLog.id) >= threshold)
                 .order_by(db.func.count(FlagAttemptLog.id).desc())
                 .all())
        return [{'user_id': r.user_id, 'username': r.username, 'wrong_count': r.cnt}
                for r in rows]
    except Exception as e:
        logger.debug(f'[Incidents] flag_spammers: {e}')
        return []


def _dead_containers() -> list:
    try:
        client = _docker()
        dead   = []
        for prefix in ('ctfd-target-', 'ctfd-kali-'):
            for c in client.containers.list(all=True, filters={'name': prefix}):
                if not c.name.startswith(prefix):
                    continue
                if c.status != 'running':
                    lbl = c.labels
                    dead.append({
                        'name':          c.name,
                        'username':      lbl.get('ctfd_target_user', '?'),
                        'role':          'target' if prefix == 'ctfd-target-' else 'kali',
                        'status':        c.status,
                        'restart_count': c.attrs.get('RestartCount', 0),
                    })
        return dead
    except Exception as e:
        logger.debug(f'[Incidents] dead_containers: {e}')
        return []


def _restart_loops(threshold: int = 3) -> list:
    try:
        client = _docker()
        loops  = []
        for prefix in ('ctfd-target-', 'ctfd-kali-'):
            for c in client.containers.list(all=True, filters={'name': prefix}):
                if not c.name.startswith(prefix):
                    continue
                count = c.attrs.get('RestartCount', 0)
                if count >= threshold:
                    lbl = c.labels
                    loops.append({
                        'name':          c.name,
                        'username':      lbl.get('ctfd_target_user', '?'),
                        'role':          'target' if prefix == 'ctfd-target-' else 'kali',
                        'status':        c.status,
                        'restart_count': count,
                    })
        loops.sort(key=lambda x: x['restart_count'], reverse=True)
        return loops
    except Exception as e:
        logger.debug(f'[Incidents] restart_loops: {e}')
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4  —  TEAM COLLABORATION FEED
# ─────────────────────────────────────────────────────────────────────────────

@mission_bp.route('/team-feed')
@authed_only
def team_feed_page():
    return render_template('team-feed.html')


@mission_bp.route('/team/feed')
@authed_only
def team_feed():
    u = get_current_user()
    if not u.team_id:
        return jsonify({'feed': [], 'has_team': False})

    limit   = min(request.args.get('limit', 60, type=int), 200)
    since   = request.args.get('since', 0.0, type=float)
    since_dt = datetime.utcfromtimestamp(since) if since else datetime.min

    feed = _build_feed(u.team_id, u.id, since_dt, limit)
    return jsonify({'feed': feed, 'has_team': True, 'team_id': u.team_id})


@mission_bp.route('/team/note', methods=['POST'])
@authed_only
def post_note():
    u = get_current_user()
    if not u.team_id:
        return jsonify({'error': 'not on a team'}), 400

    data    = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    if len(content) > 2000:
        return jsonify({'error': 'max 2000 chars'}), 400

    note = TeamFeedNote(
        team_id=u.team_id, user_id=u.id,
        username=u.name, content=content,
        pinned=bool(data.get('pinned', False)),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify({'success': True, 'id': note.id})


@mission_bp.route('/team/note/<int:note_id>', methods=['DELETE'])
@authed_only
def delete_note(note_id):
    u    = get_current_user()
    note = TeamFeedNote.query.get(note_id)
    if not note:
        return jsonify({'error': 'not found'}), 404
    if note.user_id != u.id and u.type != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    db.session.delete(note)
    db.session.commit()
    return jsonify({'success': True})


def _build_feed(team_id: int, my_id: int, since_dt: datetime, limit: int) -> list:
    feed = []

    # ── solves ────────────────────────────────────────────────────────────────
    try:
        from CTFd.models import Users as _U
        rows = (db.session.query(Solves, Challenges, _U)
                .join(Challenges, Challenges.id == Solves.challenge_id)
                .join(_U, _U.id == Solves.user_id)
                .filter(Solves.team_id == team_id, Solves.date > since_dt)
                .order_by(Solves.date.desc()).limit(limit).all())
        for sv, ch, solver in rows:
            feed.append({
                'type': 'solve',       'id': f'solve-{sv.id}',
                'username': solver.name, 'by_me': solver.id == my_id,
                'challenge': ch.name,  'category': ch.category, 'value': ch.value,
                'ts': int(sv.date.timestamp()) if sv.date else 0,
            })
    except Exception as e:
        logger.debug(f'[Feed] solves: {e}')

    # ── hints ─────────────────────────────────────────────────────────────────
    try:
        from .ctf_features import HintUsageLog
        team = Teams.query.get(team_id)
        if team:
            mids = [m.id for m in team.members]
            rows = (HintUsageLog.query
                    .filter(HintUsageLog.user_id.in_(mids),
                            HintUsageLog.unlocked_at > since_dt)
                    .order_by(HintUsageLog.unlocked_at.desc()).limit(limit).all())
            for h in rows:
                feed.append({
                    'type': 'hint',         'id': f'hint-{h.id}',
                    'username': h.username, 'by_me': h.user_id == my_id,
                    'challenge': h.challenge_name, 'challenge_id': h.challenge_id,
                    'cost': h.cost,
                    'ts': int(h.unlocked_at.timestamp()),
                })
    except Exception as e:
        logger.debug(f'[Feed] hints: {e}')

    # ── notes ─────────────────────────────────────────────────────────────────
    try:
        notes = (TeamFeedNote.query
                 .filter_by(team_id=team_id)
                 .filter(TeamFeedNote.created_at > since_dt)
                 .order_by(TeamFeedNote.created_at.desc()).limit(limit).all())
        for n in notes:
            feed.append({
                'type': 'note',          'id': f'note-{n.id}',
                'username': n.username,  'by_me': n.user_id == my_id,
                'content': n.content,    'pinned': n.pinned,
                'note_id': n.id,
                'ts': int(n.created_at.timestamp()),
            })
    except Exception as e:
        logger.debug(f'[Feed] notes: {e}')

    # ── wrong attempts (no flag text exposed) ─────────────────────────────────
    try:
        from .ctf_features import FlagAttemptLog
        team = Teams.query.get(team_id)
        if team:
            mids = [m.id for m in team.members]
            rows = (FlagAttemptLog.query
                    .filter(FlagAttemptLog.user_id.in_(mids),
                            FlagAttemptLog.created_at > since_dt,
                            FlagAttemptLog.correct == False)
                    .order_by(FlagAttemptLog.created_at.desc()).limit(30).all())
            for a in rows:
                feed.append({
                    'type': 'attempt',       'id': f'attempt-{a.id}',
                    'username': a.username,  'by_me': a.user_id == my_id,
                    'challenge': a.challenge_name,
                    'challenge_id': a.challenge_id,
                    'ts': int(a.created_at.timestamp()),
                })
    except Exception as e:
        logger.debug(f'[Feed] attempts: {e}')

    feed.sort(key=lambda x: x['ts'], reverse=True)
    return feed[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

def init_mission_tables(app):
    with app.app_context():
        try:
            db.create_all()
            logger.info('[Mission] tables ensured')
        except Exception as e:
            logger.warning(f'[Mission] db.create_all: {e}')
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS mission_team_feed_note (
                            id         INT AUTO_INCREMENT PRIMARY KEY,
                            team_id    INT,
                            user_id    INT,
                            username   VARCHAR(128),
                            content    TEXT,
                            pinned     TINYINT(1) DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            INDEX idx_team (team_id),
                            INDEX idx_ts   (created_at)
                        ) CHARACTER SET utf8mb4
                    """))
                    conn.commit()
            except Exception as e2:
                logger.warning(f'[Mission] SQL fallback: {e2}')
