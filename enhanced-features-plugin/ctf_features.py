"""
Lloyds CTF — 9-Feature Suite
─────────────────────────────
1.  First Blood Tracker
2.  Real-time Scoreboard (10-s polling)
3.  Challenge Submission Rate Limiting (Redis-backed)
4.  CTF Countdown Timer (navbar injection)
5.  Team Mode + Team Chat
6.  Admin Solve Analytics Dashboard
7.  Write-up Submission (post-solve)
8.  Container Auto-restart on Crash  (handled in target-plugin)
9.  Challenge Difficulty Voting (1–5 stars)
"""

import json
import logging
import os
import gzip
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, stream_with_context
from CTFd.models import Challenges, Notifications, Solves, Teams, Users, db
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_user

logger = logging.getLogger(__name__)

bp = Blueprint("ctf_features", __name__, url_prefix="/api/ctf")

# ─── In-memory rate-limit fallback (Redis preferred) ─────────────────────────
_rl_cache: dict = defaultdict(list)   # {(user_id, chal_id): [timestamps]}
_rl_lock = __import__("threading").Lock()

RATE_WINDOW   = 60    # seconds
RATE_MAX_TRIES = 5    # per window

# ─── SSE event bus (simple; per-process) ─────────────────────────────────────
_sse_listeners: list = []
_sse_lock = __import__("threading").Lock()


def _push_sse(event_type: str, data: dict):
    msg = f"event:{event_type}\ndata:{json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)


# ─── Redis helper ─────────────────────────────────────────────────────────────
def _redis():
    try:
        import redis as _r
        url = os.environ.get("REDIS_URL", "redis://cache:6379")
        client = _r.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


def _notify_user(user_id: int, title: str, content: str):
    """Create a notification in CTFd's native in-app notification centre."""
    db.session.add(Notifications(
        title=title,
        content=content,
        user_id=user_id,
        date=datetime.utcnow(),
    ))


def _safe_backup_label(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value or "backup").strip("-.")
    return value[:80] or "backup"


def _create_database_backup(reason: str) -> str:
    """Create a native MariaDB SQL dump through the mounted Docker socket.

    The CTFd container has the Docker SDK and socket for challenge lifecycle
    management. We use the same narrowly-scoped capability to exec
    `mariadb-dump` inside this compose project's database container. The dump is
    gzip-compressed and written to the persistent /var/backups volume.
    """
    import docker
    from urllib.parse import unquote, urlparse

    database_url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(database_url)
    if not parsed.username or not parsed.hostname or not parsed.path:
        raise RuntimeError("DATABASE_URL is unavailable; backup was not attempted")

    client = docker.from_env()
    project = None
    try:
        current = client.containers.get(os.environ.get("HOSTNAME", ""))
        project = current.labels.get("com.docker.compose.project")
    except Exception:
        pass

    filters = {"label": ["com.docker.compose.service=db"]}
    if project:
        filters["label"].append(f"com.docker.compose.project={project}")
    containers = client.containers.list(all=True, filters=filters)
    if not containers:
        raise RuntimeError("Database container was not found; destructive action cancelled")

    db_container = containers[0]
    username = unquote(parsed.username)
    password = unquote(parsed.password or "")
    database = parsed.path.lstrip("/")
    command = [
        "mariadb-dump",
        f"-u{username}",
        f"-p{password}",
        "--single-transaction",
        "--routines",
        "--events",
        "--triggers",
        database,
    ]
    result = db_container.exec_run(command, stdout=True, stderr=True)
    if result.exit_code != 0:
        detail = result.output.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"mariadb-dump failed: {detail}")

    backup_dir = os.environ.get("CTFD_BACKUP_DIR", "/var/backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    filename = f"ctfd-{stamp}-{_safe_backup_label(reason)}.sql.gz"
    path = os.path.join(backup_dir, filename)
    with gzip.open(path, "wb", compresslevel=6) as handle:
        handle.write(result.output)
    logger.warning("[DatabaseBackup] Created %s before %s", path, reason)
    return path


# =============================================================================
# DATABASE MODELS
# =============================================================================

class FirstBlood(db.Model):
    """Tracks who solved each challenge first."""
    __tablename__ = "ctf_first_blood"
    id           = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id"), unique=True, nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    team_id      = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    solved_at    = db.Column(db.DateTime, default=datetime.utcnow)


class TeamChatMessage(db.Model):
    """Team chat messages (global room if team_id=0)."""
    __tablename__ = "ctf_team_chat"
    id         = db.Column(db.Integer, primary_key=True)
    team_id    = db.Column(db.Integer, nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username   = db.Column(db.String(128), nullable=False)
    message    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Writeup(db.Model):
    """Post-solve write-up submissions."""
    __tablename__ = "ctf_writeup"
    id           = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id"), nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title        = db.Column(db.String(255), nullable=False)
    content      = db.Column(db.Text, nullable=False)
    is_public    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint("challenge_id", "user_id", name="uq_writeup_user_chal"),
    )


class ContestEvent(db.Model):
    """Admin-managed contest / event timer."""
    __tablename__ = "ctf_contest_event"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default="")
    start_ts    = db.Column(db.Integer, nullable=False)   # Unix timestamp
    end_ts      = db.Column(db.Integer, nullable=False)   # Unix timestamp
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class TeamJoinRequest(db.Model):
    """Captain-approved request from a user to join an existing team."""
    __tablename__ = "ctf_team_join_request"
    id          = db.Column(db.Integer, primary_key=True)
    team_id     = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status      = db.Column(db.String(20), nullable=False, default="pending", index=True)
    message     = db.Column(db.String(500), default="")
    decided_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at  = db.Column(db.DateTime, nullable=True)


class EventBackupRecord(db.Model):
    """Records the one automatic database backup created for an ended event."""
    __tablename__ = "ctf_event_backup_record"
    id          = db.Column(db.Integer, primary_key=True)
    event_id    = db.Column(db.Integer, nullable=False, unique=True, index=True)
    backup_path = db.Column(db.String(500), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class DifficultyVote(db.Model):
    """1–5 star difficulty rating per user per challenge."""
    __tablename__ = "ctf_difficulty_vote"
    id           = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id"), nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rating       = db.Column(db.Integer, nullable=False)   # 1–5
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint("challenge_id", "user_id", name="uq_vote_user_chal"),
    )


# =============================================================================
# MIDDLEWARE — Rate Limiting
# =============================================================================

def _check_rate_limit(user_id: int, challenge_id: int) -> tuple[bool, int]:
    """
    Returns (allowed, seconds_until_reset).
    Tries Redis first; falls back to in-memory.
    """
    r = _redis()
    key = f"ctf:rl:{user_id}:{challenge_id}"

    if r:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if count == 1:
            r.expire(key, RATE_WINDOW)
            ttl = RATE_WINDOW
        return count <= RATE_MAX_TRIES, max(0, ttl)

    # In-memory fallback
    now = time.time()
    with _rl_lock:
        timestamps = _rl_cache[(user_id, challenge_id)]
        timestamps[:] = [t for t in timestamps if now - t < RATE_WINDOW]
        if len(timestamps) >= RATE_MAX_TRIES:
            reset_in = int(RATE_WINDOW - (now - timestamps[0]))
            return False, max(0, reset_in)
        timestamps.append(now)
        return True, 0


def _record_first_blood(challenge_id: int, user_id: int):
    """Record first blood if not already set for this challenge."""
    try:
        existing = FirstBlood.query.filter_by(challenge_id=challenge_id).first()
        if existing:
            return

        user = Users.query.get(user_id)
        team_id = user.team_id if user and hasattr(user, "team_id") else None

        fb = FirstBlood(
            challenge_id=challenge_id,
            user_id=user_id,
            team_id=team_id,
        )
        db.session.add(fb)
        db.session.commit()

        chal = Challenges.query.get(challenge_id)
        _push_sse("first_blood", {
            "challenge_id":   challenge_id,
            "challenge_name": chal.name if chal else f"#{challenge_id}",
            "username":       user.name if user else "Unknown",
            "team":           user.team.name if (user and hasattr(user, "team") and user.team) else None,
            "points":         chal.value if chal else 0,
            "ts":             datetime.utcnow().isoformat(),
        })
        logger.info(f"[FirstBlood] {user.name if user else '?'} — {chal.name if chal else challenge_id}")
    except Exception as e:
        logger.warning(f"[FirstBlood] record failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


# =============================================================================
# API ROUTES
# =============================================================================

# ── Rate-limiting interceptor (registered on app, not blueprint) ───────────
def register_rate_limit_hook(app):
    @app.before_request
    def _rate_limit_attempts():
        """Block flag-guess spam: max 5 tries / 60 s per challenge."""
        if request.method != "POST":
            return
        import re
        m = re.match(r"^/api/v1/challenges/(\d+)/attempt$", request.path)
        if not m:
            return
        challenge_id = int(m.group(1))
        user = get_current_user()
        if not user or user.admin:
            return
        allowed, reset_in = _check_rate_limit(user.id, challenge_id)
        if not allowed:
            return jsonify({
                "success":   False,
                "data":      {"message": f"Too many attempts. Try again in {reset_in}s."},
            }), 429


def register_first_blood_hook(app):
    @app.after_request
    def _detect_first_blood(response):
        """After a correct flag submission, record first blood."""
        if request.method != "POST" or "attempt" not in request.path:
            return response
        if response.status_code != 200:
            return response
        try:
            import re
            m = re.match(r"^/api/v1/challenges/(\d+)/attempt$", request.path)
            if not m:
                return response
            body = response.get_json(silent=True) or {}
            if not (body.get("success") and body.get("data", {}).get("status") == "correct"):
                return response
            challenge_id = int(m.group(1))
            user = get_current_user()
            if user and not user.admin:
                _record_first_blood(challenge_id, user.id)
                # Also push scoreboard update
                _push_sse("score_update", {"ts": datetime.utcnow().isoformat()})
        except Exception as e:
            logger.debug(f"[FirstBlood hook] {e}")
        return response


# ── First Blood ────────────────────────────────────────────────────────────────
@bp.route("/first-blood")
def list_first_bloods():
    rows = (
        db.session.query(FirstBlood, Challenges, Users)
        .join(Challenges, Challenges.id == FirstBlood.challenge_id)
        .join(Users, Users.id == FirstBlood.user_id)
        .order_by(FirstBlood.solved_at)
        .all()
    )
    return jsonify([{
        "challenge_id":   fb.challenge_id,
        "challenge_name": c.name,
        "points":         c.value,
        "username":       u.name,
        "team":           (u.team.name if hasattr(u, "team") and u.team else None),
        "solved_at":      fb.solved_at.isoformat(),
    } for fb, c, u in rows])


@bp.route("/first-blood/recent")
def recent_first_bloods():
    """Last N first bloods (for toast notifications)."""
    since_str = request.args.get("since")
    query = FirstBlood.query
    if since_str:
        try:
            since = datetime.fromisoformat(since_str)
            query = query.filter(FirstBlood.solved_at > since)
        except Exception:
            pass
    rows = (
        db.session.query(FirstBlood, Challenges, Users)
        .join(Challenges, Challenges.id == FirstBlood.challenge_id)
        .join(Users, Users.id == FirstBlood.user_id)
        .filter(FirstBlood.solved_at > (datetime.utcnow() - timedelta(hours=1)))
        .order_by(FirstBlood.solved_at.desc())
        .limit(10)
        .all()
    )
    return jsonify([{
        "challenge_name": c.name,
        "points":         c.value,
        "username":       u.name,
        "solved_at":      fb.solved_at.isoformat(),
    } for fb, c, u in rows])


# ── Live Scoreboard ────────────────────────────────────────────────────────────
@bp.route("/scoreboard")
def live_scoreboard():
    """Lightweight scoreboard snapshot for live polling."""
    try:
        rows = (
            db.session.query(
                Users.id,
                Users.name,
                db.func.sum(Challenges.value).label("score"),
                db.func.max(Solves.date).label("last_solve"),
            )
            .join(Solves, Solves.user_id == Users.id)
            .join(Challenges, Challenges.id == Solves.challenge_id)
            .filter(Users.hidden == False, Users.banned == False)
            .group_by(Users.id, Users.name)
            .order_by(db.desc("score"), db.asc("last_solve"))
            .limit(50)
            .all()
        )
        return jsonify([{
            "rank":       i + 1,
            "user_id":    r.id,
            "username":   r.name,
            "score":      int(r.score or 0),
            "last_solve": r.last_solve.isoformat() if r.last_solve else None,
        } for i, r in enumerate(rows)])
    except Exception as e:
        logger.warning(f"[scoreboard] {e}")
        return jsonify([])


# ── SSE stream ─────────────────────────────────────────────────────────────────
@bp.route("/stream")
def sse_stream():
    """Server-Sent Events for first blood + score updates."""
    import queue as _queue

    q: _queue.Queue = _queue.Queue(maxsize=20)
    with _sse_lock:
        _sse_listeners.append(q)

    def generate():
        yield "retry:5000\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except Exception:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_listeners:
                    _sse_listeners.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── CTF Config (countdown end time) ───────────────────────────────────────────
@bp.route("/config")
def ctf_config():
    end_raw = get_config("end")
    start_raw = get_config("start")
    name = get_config("ctf_name") or "Lloyds CTF"
    now = int(time.time())
    if end_raw and now >= int(end_raw):
        try:
            backup_key = -int(end_raw)
            record = EventBackupRecord.query.filter_by(event_id=backup_key).first()
            if not record:
                path = _create_database_backup(f"ctf-ended-{name}")
                db.session.add(EventBackupRecord(event_id=backup_key, backup_path=path))
                db.session.commit()
        except Exception as exc:
            logger.error("[DatabaseBackup] Automatic CTF-end backup failed: %s", exc)
            db.session.rollback()
    return jsonify({
        "name":  name,
        "start": int(start_raw) if start_raw else None,
        "end":   int(end_raw)   if end_raw   else None,
        "now":   now,
    })


# ── Team Chat ─────────────────────────────────────────────────────────────────
def _team_captain_id(team):
    if team.captain_id:
        return team.captain_id
    first = Users.query.filter_by(team_id=team.id).order_by(Users.id.asc()).first()
    return first.id if first else None


def _team_directory_for(user):
    pending_ids = {
        row.team_id
        for row in TeamJoinRequest.query.filter_by(user_id=user.id, status="pending").all()
    }
    teams = Teams.query.filter_by(hidden=False, banned=False).order_by(Teams.name.asc()).all()
    result = []
    for team in teams:
        captain_id = _team_captain_id(team)
        captain = Users.query.get(captain_id) if captain_id else None
        result.append({
            "id": team.id,
            "name": team.name,
            "affiliation": team.affiliation or "",
            "country": team.country or "",
            "member_count": Users.query.filter_by(team_id=team.id).count(),
            "captain": captain.name if captain else "Unassigned",
            "pending": team.id in pending_ids,
        })
    return result


@bp.route("/team-hub")
@authed_only
def team_hub():
    user = get_current_user()
    current_team = Teams.query.get(user.team_id) if user.team_id else None
    incoming = []
    is_captain = bool(current_team and _team_captain_id(current_team) == user.id)
    if is_captain:
        rows = TeamJoinRequest.query.filter_by(
            team_id=current_team.id, status="pending"
        ).order_by(TeamJoinRequest.created_at.asc()).all()
        for row in rows:
            applicant = Users.query.get(row.user_id)
            if applicant:
                incoming.append({
                    "id": row.id,
                    "user_name": applicant.name,
                    "message": row.message,
                    "created_at": row.created_at,
                })
    return render_template(
        "team_hub.html",
        current_user=user,
        current_team=current_team,
        teams=_team_directory_for(user),
        incoming=incoming,
        members=(Users.query.filter_by(team_id=current_team.id).order_by(Users.name.asc()).all()
                 if current_team else []),
        is_captain=is_captain,
    )


@bp.route("/teams/create", methods=["POST"])
@authed_only
def create_optional_team():
    user = get_current_user()
    if user.team_id:
        return jsonify({"success": False, "message": "You are already in a team."}), 409
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if len(name) < 3 or len(name) > 128:
        return jsonify({"success": False, "message": "Team name must be 3–128 characters."}), 400
    if Teams.query.filter(db.func.lower(Teams.name) == name.lower()).first():
        return jsonify({"success": False, "message": "That team name is already in use."}), 409

    team = Teams(
        name=name,
        email=f"team-{secrets.token_hex(10)}@optional.local",
        password=secrets.token_urlsafe(32),
        affiliation=(data.get("affiliation") or "").strip()[:128],
        country=(data.get("country") or "").strip()[:32],
        hidden=False,
        banned=False,
        captain_id=user.id,
    )
    db.session.add(team)
    db.session.flush()
    user.team_id = team.id
    db.session.commit()
    return jsonify({"success": True, "team_id": team.id, "message": f"Team {team.name} created."}), 201


@bp.route("/teams/leave", methods=["POST"])
@authed_only
def leave_optional_team():
    user = get_current_user()
    if not user.team_id:
        return jsonify({"success": False, "message": "You are not in a team."}), 409
    team = Teams.query.get_or_404(user.team_id)
    if _team_captain_id(team) == user.id:
        successor = Users.query.filter(
            Users.team_id == team.id,
            Users.id != user.id,
        ).order_by(Users.id.asc()).first()
        if successor:
            team.captain_id = successor.id
            _notify_user(successor.id, "You are now team captain", f"You are now captain of **{team.name}**.")
        else:
            # Preserve historical solve references while removing the empty team
            # from the public directory.
            team.captain_id = None
            team.hidden = True
            pending = TeamJoinRequest.query.filter_by(team_id=team.id, status="pending").all()
            for row in pending:
                row.status = "rejected"
                row.decided_by = user.id
                row.decided_at = datetime.utcnow()
                _notify_user(row.user_id, "Team request closed", f"**{team.name}** was closed by its captain.")
    user.team_id = None
    db.session.commit()
    return jsonify({"success": True, "message": "You left the team. Individual participation remains active."})


@bp.route("/teams/<int:team_id>/join-requests", methods=["POST"])
@authed_only
def request_team_join(team_id):
    user = get_current_user()
    if user.team_id:
        return jsonify({"success": False, "message": "Leave your current team before requesting another."}), 409
    team = Teams.query.filter_by(id=team_id, hidden=False, banned=False).first_or_404()
    existing = TeamJoinRequest.query.filter_by(
        team_id=team.id, user_id=user.id, status="pending"
    ).first()
    if existing:
        return jsonify({"success": False, "message": "A request is already pending."}), 409

    data = request.get_json(silent=True) or {}
    join_request = TeamJoinRequest(
        team_id=team.id,
        user_id=user.id,
        message=(data.get("message") or "").strip()[:500],
    )
    captain_id = _team_captain_id(team)
    if not captain_id:
        return jsonify({"success": False, "message": "This team has no captain available."}), 409
    db.session.add(join_request)
    _notify_user(
        captain_id,
        "New team join request",
        f"**{user.name}** requested to join **{team.name}**. Review it in the Team Hub.",
    )
    db.session.commit()
    return jsonify({"success": True, "message": "Request sent to the team captain for approval."}), 201


@bp.route("/teams/join-requests/<int:request_id>/<decision>", methods=["POST"])
@authed_only
def decide_team_join(request_id, decision):
    if decision not in ("approve", "reject"):
        return jsonify({"success": False, "message": "Decision must be approve or reject."}), 400
    actor = get_current_user()
    join_request = TeamJoinRequest.query.get_or_404(request_id)
    team = Teams.query.get_or_404(join_request.team_id)
    if actor.type != "admin" and _team_captain_id(team) != actor.id:
        return jsonify({"success": False, "message": "Only the team captain can decide this request."}), 403
    if join_request.status != "pending":
        return jsonify({"success": False, "message": "This request has already been decided."}), 409

    applicant = Users.query.get_or_404(join_request.user_id)
    if decision == "approve":
        if applicant.team_id:
            join_request.status = "rejected"
            join_request.decided_by = actor.id
            join_request.decided_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"success": False, "message": "Applicant has already joined another team."}), 409
        max_members = int(get_config("team_size") or 0)
        member_count = Users.query.filter_by(team_id=team.id).count()
        if max_members and member_count >= max_members:
            return jsonify({"success": False, "message": "Team is already at the configured size limit."}), 409
        applicant.team_id = team.id
        join_request.status = "approved"
        title = "Team request approved"
        content = f"Your request to join **{team.name}** was approved. Welcome to the team."
    else:
        join_request.status = "rejected"
        title = "Team request declined"
        content = f"Your request to join **{team.name}** was declined by the team captain."

    join_request.decided_by = actor.id
    join_request.decided_at = datetime.utcnow()
    _notify_user(applicant.id, title, content)
    db.session.commit()
    return jsonify({"success": True, "status": join_request.status})


@bp.route("/chat/<int:team_id>", methods=["GET"])
@authed_only
def get_chat(team_id):
    since_id = request.args.get("since_id", 0, type=int)
    msgs = (
        TeamChatMessage.query
        .filter(TeamChatMessage.team_id == team_id, TeamChatMessage.id > since_id)
        .order_by(TeamChatMessage.created_at.asc())
        .limit(50)
        .all()
    )
    return jsonify([{
        "id":         m.id,
        "username":   m.username,
        "message":    m.message,
        "created_at": m.created_at.isoformat(),
    } for m in msgs])


@bp.route("/chat/<int:team_id>", methods=["POST"])
@authed_only
def post_chat(team_id):
    user = get_current_user()
    if not user:
        abort(401)
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    if not text or len(text) > 500:
        return jsonify({"success": False, "message": "Invalid message"}), 400
    msg = TeamChatMessage(
        team_id=team_id,
        user_id=user.id,
        username=user.name,
        message=text,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True, "id": msg.id})


# ── Write-ups ─────────────────────────────────────────────────────────────────
@bp.route("/writeup/<int:challenge_id>", methods=["GET"])
@authed_only
def get_writeup(challenge_id):
    user = get_current_user()
    wu = Writeup.query.filter_by(challenge_id=challenge_id, user_id=user.id).first()
    if wu:
        return jsonify({
            "id":      wu.id,
            "title":   wu.title,
            "content": wu.content,
            "public":  wu.is_public,
        })
    return jsonify(None)


@bp.route("/writeup/<int:challenge_id>", methods=["POST"])
@authed_only
def submit_writeup(challenge_id):
    user = get_current_user()
    if not user:
        abort(401)
    # Only allow if user has solved it
    solved = Solves.query.filter_by(user_id=user.id, challenge_id=challenge_id).first()
    if not solved:
        return jsonify({"success": False, "message": "Solve the challenge first"}), 403

    data = request.get_json(silent=True) or {}
    title   = (data.get("title") or "").strip()[:255]
    content = (data.get("content") or "").strip()
    public  = bool(data.get("is_public", True))

    if not title or not content:
        return jsonify({"success": False, "message": "Title and content required"}), 400

    wu = Writeup.query.filter_by(challenge_id=challenge_id, user_id=user.id).first()
    if wu:
        wu.title, wu.content, wu.is_public = title, content, public
    else:
        wu = Writeup(challenge_id=challenge_id, user_id=user.id,
                     title=title, content=content, is_public=public)
        db.session.add(wu)
    db.session.commit()
    return jsonify({"success": True, "id": wu.id})


@bp.route("/writeups/challenge/<int:challenge_id>/public")
def public_writeups(challenge_id):
    """Public write-ups for a challenge (visible to all)."""
    rows = (
        db.session.query(Writeup, Users)
        .join(Users, Users.id == Writeup.user_id)
        .filter(Writeup.challenge_id == challenge_id, Writeup.is_public == True)
        .order_by(Writeup.created_at.asc())
        .all()
    )
    return jsonify([{
        "id":         wu.id,
        "title":      wu.title,
        "content":    wu.content,
        "username":   u.name,
        "created_at": wu.created_at.isoformat(),
    } for wu, u in rows])


@bp.route("/writeups/admin")
@admins_only
def admin_all_writeups():
    rows = (
        db.session.query(Writeup, Challenges, Users)
        .join(Challenges, Challenges.id == Writeup.challenge_id)
        .join(Users, Users.id == Writeup.user_id)
        .order_by(Writeup.created_at.desc())
        .all()
    )
    return jsonify([{
        "id":             wu.id,
        "challenge_name": c.name,
        "username":       u.name,
        "title":          wu.title,
        "public":         wu.is_public,
        "created_at":     wu.created_at.isoformat(),
    } for wu, c, u in rows])


# ── Difficulty Voting ─────────────────────────────────────────────────────────
@bp.route("/vote/<int:challenge_id>", methods=["GET"])
def get_votes(challenge_id):
    rows = DifficultyVote.query.filter_by(challenge_id=challenge_id).all()
    if not rows:
        return jsonify({"avg": None, "count": 0, "my_vote": None})
    avg = sum(r.rating for r in rows) / len(rows)
    my_vote = None
    user = get_current_user()
    if user:
        mine = DifficultyVote.query.filter_by(challenge_id=challenge_id, user_id=user.id).first()
        my_vote = mine.rating if mine else None
    dist = {str(i): sum(1 for r in rows if r.rating == i) for i in range(1, 6)}
    return jsonify({"avg": round(avg, 1), "count": len(rows), "my_vote": my_vote, "distribution": dist})


@bp.route("/vote/<int:challenge_id>", methods=["POST"])
@authed_only
def cast_vote(challenge_id):
    user = get_current_user()
    if not user:
        abort(401)
    solved = Solves.query.filter_by(user_id=user.id, challenge_id=challenge_id).first()
    if not solved:
        return jsonify({"success": False, "message": "Solve the challenge first"}), 403

    data = request.get_json(silent=True) or {}
    rating = int(data.get("rating", 0))
    if rating not in range(1, 6):
        return jsonify({"success": False, "message": "Rating must be 1–5"}), 400

    vote = DifficultyVote.query.filter_by(challenge_id=challenge_id, user_id=user.id).first()
    if vote:
        vote.rating = rating
    else:
        vote = DifficultyVote(challenge_id=challenge_id, user_id=user.id, rating=rating)
        db.session.add(vote)
    db.session.commit()
    return jsonify({"success": True})


# ── Admin Analytics ────────────────────────────────────────────────────────────
@bp.route("/analytics")
@admins_only
def analytics_data():
    """Full analytics payload for the admin dashboard."""
    # Per-challenge solve counts + avg difficulty
    chal_rows = (
        db.session.query(
            Challenges.id,
            Challenges.name,
            Challenges.category,
            Challenges.value,
            db.func.count(Solves.id).label("solves"),
        )
        .outerjoin(Solves, Solves.challenge_id == Challenges.id)
        .group_by(Challenges.id, Challenges.name, Challenges.category, Challenges.value)
        .order_by(db.desc("solves"))
        .all()
    )

    votes_raw = db.session.query(
        DifficultyVote.challenge_id,
        db.func.avg(DifficultyVote.rating).label("avg_rating"),
        db.func.count(DifficultyVote.id).label("vote_count"),
    ).group_by(DifficultyVote.challenge_id).all()
    vote_map = {v.challenge_id: {"avg": round(float(v.avg_rating), 1), "votes": v.vote_count} for v in votes_raw}

    first_bloods = {fb.challenge_id: fb for fb in FirstBlood.query.all()}
    fb_user_map = {u.id: u.name for u in Users.query.filter(Users.id.in_([fb.user_id for fb in first_bloods.values()])).all()}

    challenges = []
    for row in chal_rows:
        fb = first_bloods.get(row.id)
        challenges.append({
            "id":           row.id,
            "name":         row.name,
            "category":     row.category,
            "points":       row.value,
            "solves":       row.solves,
            "first_blood":  fb_user_map.get(fb.user_id) if fb else None,
            "difficulty":   vote_map.get(row.id, {"avg": None, "votes": 0}),
        })

    # Top solvers
    top_solvers = (
        db.session.query(Users.name, db.func.count(Solves.id).label("cnt"))
        .join(Solves, Solves.user_id == Users.id)
        .group_by(Users.id, Users.name)
        .order_by(db.desc("cnt"))
        .limit(10)
        .all()
    )

    # Solve timeline (hourly buckets, last 24 h)
    cutoff = datetime.utcnow() - timedelta(hours=24)
    timeline_rows = Solves.query.filter(Solves.date >= cutoff).all()
    timeline_buckets: dict = defaultdict(int)
    for s in timeline_rows:
        bucket = s.date.strftime("%Y-%m-%dT%H:00")
        timeline_buckets[bucket] += 1

    # Writeup count
    writeup_count = Writeup.query.count()

    return jsonify({
        "challenges": challenges,
        "top_solvers": [{"username": r.name, "solves": r.cnt} for r in top_solvers],
        "solve_timeline": dict(sorted(timeline_buckets.items())),
        "writeup_count": writeup_count,
        "total_solves": Solves.query.count(),
        "total_users": Users.query.filter_by(hidden=False, banned=False).count(),
    })


@bp.route("/analytics/dashboard")
@admins_only
def analytics_dashboard():
    from flask import render_template
    return render_template("admin/ctf_analytics.html")


# ── Admin Challenge CRUD ──────────────────────────────────────────────────────

@bp.route("/admin/challenges", methods=["GET"])
@admins_only
def admin_list_challenges():
    """Return all challenges with solve count and first flag content."""
    cat = request.args.get("category")
    q = Challenges.query
    if cat:
        q = q.filter_by(category=cat)
    chals = q.order_by(Challenges.id).all()
    from CTFd.models import Flags, Hints

    result = []
    for c in chals:
        flag_obj = Flags.query.filter_by(challenge_id=c.id).first()
        solves = Solves.query.filter_by(challenge_id=c.id).count()
        hints = Hints.query.filter_by(challenge_id=c.id).all()
        result.append({
            "id":          c.id,
            "name":        c.name,
            "category":    c.category,
            "value":       c.value,
            "description": c.description,
            "state":       c.state,
            "flag":        flag_obj.content if flag_obj else "",
            "solves":      solves,
            "hints":       [{"id": h.id, "content": h.content, "cost": h.cost} for h in hints],
        })
    return jsonify(result)


@bp.route("/admin/challenges/<int:chal_id>", methods=["GET"])
@admins_only
def admin_get_challenge(chal_id):
    from CTFd.models import Flags, Hints
    c = Challenges.query.get_or_404(chal_id)
    flag_obj = Flags.query.filter_by(challenge_id=c.id).first()
    hints = Hints.query.filter_by(challenge_id=c.id).all()
    return jsonify({
        "id":          c.id,
        "name":        c.name,
        "category":    c.category,
        "value":       c.value,
        "description": c.description,
        "state":       c.state,
        "flag":        flag_obj.content if flag_obj else "",
        "hints":       [{"id": h.id, "content": h.content, "cost": h.cost} for h in hints],
    })


@bp.route("/admin/challenges", methods=["POST"])
@admins_only
def admin_create_challenge():
    from CTFd.models import Flags, Hints
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "Name required"}), 400

    c = Challenges(
        name=name,
        category=(data.get("category") or "Web").strip(),
        description=(data.get("description") or "").strip(),
        value=int(data.get("value", 100)),
        type="standard",
        state=data.get("state", "visible"),
    )
    db.session.add(c)
    db.session.flush()

    flag_val = (data.get("flag") or "").strip()
    if flag_val:
        db.session.add(Flags(challenge_id=c.id, type="standard", content=flag_val, data=""))

    for h in (data.get("hints") or []):
        txt = (h.get("text") or "").strip()
        if txt:
            db.session.add(Hints(challenge_id=c.id, content=txt, cost=int(h.get("cost", 0))))

    db.session.commit()
    return jsonify({"success": True, "id": c.id})


@bp.route("/admin/challenges/<int:chal_id>", methods=["PUT"])
@admins_only
def admin_update_challenge(chal_id):
    from CTFd.models import Flags, Hints
    c = Challenges.query.get_or_404(chal_id)
    data = request.get_json(silent=True) or {}

    if "name"        in data: c.name        = data["name"].strip()
    if "category"    in data: c.category    = data["category"].strip()
    if "value"       in data: c.value       = int(data["value"])
    if "description" in data: c.description = data["description"].strip()
    if "state"       in data: c.state       = data["state"]

    flag_val = (data.get("flag") or "").strip()
    if flag_val:
        flag_obj = Flags.query.filter_by(challenge_id=c.id).first()
        if flag_obj:
            flag_obj.content = flag_val
        else:
            db.session.add(Flags(challenge_id=c.id, type="standard", content=flag_val, data=""))

    if "hints" in data:
        # Replace all hints
        Hints.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
        for h in (data["hints"] or []):
            txt = (h.get("text") or "").strip()
            if txt:
                db.session.add(Hints(challenge_id=c.id, content=txt, cost=int(h.get("cost", 0))))

    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/challenges/<int:chal_id>", methods=["DELETE"])
@admins_only
def admin_delete_challenge(chal_id):
    from CTFd.models import Flags, Hints
    c = Challenges.query.get_or_404(chal_id)
    try:
        backup_path = _create_database_backup(f"before-delete-challenge-{c.id}")
    except Exception as exc:
        logger.error("[DatabaseBackup] Challenge deletion cancelled: %s", exc)
        return jsonify({"success": False, "message": f"Backup failed; deletion cancelled: {exc}"}), 503
    Hints.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
    Flags.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
    Solves.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
    db.session.delete(c)
    db.session.commit()
    return jsonify({"success": True, "backup": backup_path})


@bp.route("/admin/challenges/dashboard")
@admins_only
def challenge_crud_dashboard():
    from flask import render_template as _rt
    return _rt("admin/challenge_crud.html")


# ── Contest / Event Timer ─────────────────────────────────────────────────────

@bp.route("/events", methods=["GET"])
def list_events():
    """Public: all active events (for timer display)."""
    _purge_expired_chat()
    now = int(time.time())
    events = ContestEvent.query.filter(
        ContestEvent.is_active == True,
        ContestEvent.end_ts >= now,
    ).order_by(ContestEvent.start_ts).all()
    return jsonify([{
        "id":          e.id,
        "name":        e.name,
        "description": e.description,
        "start_ts":    e.start_ts,
        "end_ts":      e.end_ts,
        "now":         now,
    } for e in events])


@bp.route("/events/all", methods=["GET"])
@admins_only
def admin_list_events():
    events = ContestEvent.query.order_by(ContestEvent.created_at.desc()).all()
    now = int(time.time())
    return jsonify([{
        "id":          e.id,
        "name":        e.name,
        "description": e.description,
        "start_ts":    e.start_ts,
        "end_ts":      e.end_ts,
        "is_active":   e.is_active,
        "status":      "upcoming" if e.start_ts > now else ("active" if e.end_ts > now else "ended"),
    } for e in events])


@bp.route("/events", methods=["POST"])
@admins_only
def create_event():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "Name required"}), 400
    try:
        start_ts = int(data.get("start_ts", int(time.time())))
        end_ts   = int(data.get("end_ts",   start_ts + 3600))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid timestamps"}), 400

    evt = ContestEvent(
        name=name,
        description=(data.get("description") or "").strip(),
        start_ts=start_ts,
        end_ts=end_ts,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(evt)
    db.session.commit()
    return jsonify({"success": True, "id": evt.id})


@bp.route("/events/<int:event_id>", methods=["PUT"])
@admins_only
def update_event(event_id):
    evt = ContestEvent.query.get_or_404(event_id)
    data = request.get_json(silent=True) or {}
    if "name"        in data: evt.name        = data["name"].strip()
    if "description" in data: evt.description = data["description"].strip()
    if "start_ts"    in data: evt.start_ts    = int(data["start_ts"])
    if "end_ts"      in data: evt.end_ts      = int(data["end_ts"])
    if "is_active"   in data: evt.is_active   = bool(data["is_active"])
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/events/<int:event_id>", methods=["DELETE"])
@admins_only
def delete_event(event_id):
    evt = ContestEvent.query.get_or_404(event_id)
    db.session.delete(evt)
    db.session.commit()
    # Purge chat messages from ended events
    _purge_expired_chat()
    return jsonify({"success": True})


def _purge_expired_chat():
    """Delete chat messages for events that have ended."""
    try:
        now = int(time.time())
        expired = ContestEvent.query.filter(
            ContestEvent.is_active == True,
            ContestEvent.end_ts < now,
        ).all()
        if expired:
            backed_up = []
            for evt in expired:
                record = EventBackupRecord.query.filter_by(event_id=evt.id).first()
                if not record:
                    path = _create_database_backup(f"event-ended-{evt.id}-{evt.name}")
                    db.session.add(EventBackupRecord(event_id=evt.id, backup_path=path))
                    backed_up.append(path)
                evt.is_active = False
            # Delete all chat messages older than the earliest expired end
            earliest_end = min(e.end_ts for e in expired)
            cutoff = datetime.utcfromtimestamp(earliest_end)
            TeamChatMessage.query.filter(
                TeamChatMessage.created_at < cutoff
            ).delete(synchronize_session=False)
            db.session.commit()
            logger.info("[ChatPurge] Purged messages before %s; backups=%s", cutoff, backed_up)
    except Exception as e:
        logger.warning(f"[ChatPurge] Error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


@bp.route("/events/admin/dashboard")
@admins_only
def events_admin_dashboard():
    from flask import render_template as _rt
    return _rt("admin/contest_events.html")


# ── Flag submission stats ─────────────────────────────────────────────────────

@bp.route("/stats/me")
@authed_only
def my_stats():
    """Return current user's correct/wrong solve counts."""
    user = get_current_user()
    if not user:
        abort(401)
    correct = Solves.query.filter_by(user_id=user.id).count()
    # Wrong attempts: use CTFd's WrongKeys model if available
    wrong = 0
    try:
        from CTFd.models import WrongKeys
        wrong = WrongKeys.query.filter_by(user_id=user.id).count()
    except Exception:
        pass
    total = Challenges.query.filter_by(state="visible").count()
    return jsonify({
        "correct": correct,
        "wrong":   wrong,
        "total":   total,
        "percent": round((correct / total * 100) if total else 0, 1),
    })


# ── Admin User Management ────────────────────────────────────────────────────

@bp.route("/admin/users", methods=["GET"])
@admins_only
def admin_list_users():
    """List all users."""

    users = Users.query.order_by(Users.id.desc()).all()
    result = []
    for u in users:
        solves = Solves.query.filter_by(user_id=u.id).count()
        result.append({
            "id":       u.id,
            "name":     u.name,
            "email":    u.email,
            "admin":    (u.type == "admin"),
            "banned":   u.banned,
            "hidden":   u.hidden,
            "solves":   solves,
        })
    return jsonify(result)


@bp.route("/admin/users/create", methods=["POST"])
@admins_only
def admin_create_user():
    """Create a single user."""
    from CTFd.models import db
    from CTFd.utils import get_config
    data = request.get_json(silent=True) or {}
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    pwd   = (data.get("password") or "").strip()
    if not name or not email or not pwd:
        return jsonify({"success": False, "message": "name, email and password are required"}), 400

    existing = Users.query.filter((Users.name == name) | (Users.email == email)).first()
    if existing:
        return jsonify({"success": False, "message": "Username or email already in use"}), 409

    is_admin = bool(data.get("admin", False))
    u = Users(
        name=name,
        email=email,
        password=pwd,
        type="admin" if is_admin else "user",
        verified=True,
        hidden=False,
        banned=False,
    )
    db.session.add(u)
    db.session.flush()

    if get_config("user_mode") == "teams" and not is_admin:
        team = Teams(
            name=name,
            email=email,
            password=pwd,
            hidden=False,
            banned=False,
            captain_id=u.id,
        )
        db.session.add(team)
        db.session.flush()
        u.team_id = team.id

    db.session.commit()
    return jsonify({"success": True, "id": u.id})


@bp.route("/admin/users/bulk", methods=["POST"])
@admins_only
def admin_bulk_create_users():
    """Bulk-create users. Body: {users: [{name, email, password}, ...]}"""
    from CTFd.models import db
    from CTFd.utils import get_config
    data = request.get_json(silent=True) or {}
    rows = data.get("users") or []
    if not rows:
        return jsonify({"success": False, "message": "No users provided"}), 400

    created, skipped = [], []
    for row in rows:
        name  = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip().lower()
        pwd   = (row.get("password") or "").strip()
        if not name or not email or not pwd:
            skipped.append(name or "(blank)")
            continue
        if Users.query.filter((Users.name == name) | (Users.email == email)).first():
            skipped.append(name)
            continue
        u = Users(
            name=name,
            email=email,
            password=pwd,
            verified=True,
            hidden=False,
            banned=False,
        )
        db.session.add(u)
        db.session.flush()
        if get_config("user_mode") == "teams":
            team = Teams(
                name=name,
                email=email,
                password=pwd,
                hidden=False,
                banned=False,
                captain_id=u.id,
            )
            db.session.add(team)
            db.session.flush()
            u.team_id = team.id
        created.append(name)

    db.session.commit()
    return jsonify({"success": True, "created": created, "skipped": skipped})


@bp.route("/admin/users/<int:user_id>", methods=["PUT"])
@admins_only
def admin_update_user(user_id):
    """Ban/unban or toggle admin for a user."""
    from CTFd.models import db
    u = Users.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    if "banned" in data: u.banned = bool(data["banned"])
    if "admin"  in data: u.type   = "admin" if bool(data["admin"]) else "user"
    if "hidden" in data: u.hidden = bool(data["hidden"])
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/users/<int:user_id>/approval/<status>", methods=["GET", "POST"])
@admins_only
def admin_set_user_approval(user_id, status):
    """Set approval state from admin UI without relying on CSRF-sensitive PUT."""
    if status not in ("approved", "pending", "rejected"):
        return jsonify({"success": False, "message": "Invalid approval status"}), 400
    from CTFd.models import db
    u = Users.query.get_or_404(user_id)
    try:
        from .all_implementations import UserApprovalRequest
        approval = UserApprovalRequest.query.filter_by(user_id=u.id).first()
        if not approval:
            approval = UserApprovalRequest(
                user_id=u.id,
                status="pending",
                requested_at=datetime.utcnow(),
            )
            db.session.add(approval)

        approval.status = status
        if status == "approved":
            approval.reason = None
            u.hidden = False
            u.banned = False
            u.verified = True
            if getattr(u, "team", None):
                u.team.hidden = False
                u.team.banned = False
        elif status == "rejected":
            approval.reason = "Registration not approved by administrator."
            if u.type != "admin":
                u.hidden = True
                u.banned = True
                if getattr(u, "team", None):
                    u.team.hidden = True
                    u.team.banned = True
        else:
            approval.reason = None
            if u.type != "admin":
                u.hidden = True
                u.banned = False
                if getattr(u, "team", None):
                    u.team.hidden = True
                    u.team.banned = False

        try:
            from CTFd.utils.user import get_current_user
            admin = get_current_user()
            approval.reviewed_by = admin.id if admin else None
        except Exception:
            pass

        db.session.commit()
        return jsonify({"success": True, "status": status})
    except Exception as e:
        logger.warning("Approval route failed for user %s: %s", user_id, e)
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to update approval"}), 500


@bp.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admins_only
def admin_delete_user(user_id):
    """Delete a user and all their data."""
    from CTFd.models import db
    u = Users.query.get_or_404(user_id)
    if u.type == "admin":
        return jsonify({"success": False, "message": "Cannot delete admin accounts"}), 403
    team = Teams.query.get(u.team_id) if u.team_id else None
    if team and team.captain_id == u.id:
        team.captain_id = None
    u.team_id = None
    db.session.flush()
    # Clean up foreign-key-constrained data
    Solves.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    TeamChatMessage.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    FirstBlood.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    try:
        from .all_implementations import UserApprovalRequest
        UserApprovalRequest.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    except Exception:
        pass
    db.session.delete(u)
    if team and not Users.query.filter_by(team_id=team.id).first():
        db.session.delete(team)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/users/dashboard")
@admins_only
def admin_users_dashboard():
    from flask import render_template as _rt
    return _rt("admin/user_management.html")


# =============================================================================
# ANNOUNCEMENTS
# =============================================================================

class Announcement(db.Model):
    __tablename__ = "ctf_announcement"
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(255), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    pinned     = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)


@bp.route("/announcements")
def list_announcements():
    rows = Announcement.query.order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all()
    return jsonify([{
        "id":         a.id,
        "title":      a.title,
        "body":       a.body,
        "pinned":     a.pinned,
        "created_at": a.created_at.isoformat(),
    } for a in rows])


@bp.route("/admin/announcements", methods=["POST"])
@admins_only
def create_announcement():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body  = (data.get("body")  or "").strip()
    if not title or not body:
        return jsonify({"success": False, "message": "title and body required"}), 400
    user = get_current_user()
    a = Announcement(title=title, body=body,
                     pinned=bool(data.get("pinned", False)),
                     created_by=user.id if user else None)
    db.session.add(a)
    db.session.commit()
    _push_sse("announcement", {"id": a.id, "title": a.title, "body": a.body, "pinned": a.pinned})
    return jsonify({"success": True, "id": a.id})


@bp.route("/admin/announcements/<int:ann_id>", methods=["PUT"])
@admins_only
def update_announcement(ann_id):
    a = Announcement.query.get_or_404(ann_id)
    data = request.get_json(silent=True) or {}
    if "title"  in data: a.title  = data["title"].strip()
    if "body"   in data: a.body   = data["body"].strip()
    if "pinned" in data: a.pinned = bool(data["pinned"])
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/announcements/<int:ann_id>", methods=["DELETE"])
@admins_only
def delete_announcement(ann_id):
    a = Announcement.query.get_or_404(ann_id)
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/announcements/dashboard")
@admins_only
def announcements_dashboard():
    from flask import render_template as _rt
    return _rt("admin/announcements.html")


# =============================================================================
# CHALLENGE DOCS (DB-backed)
# =============================================================================

class ChallengeDoc(db.Model):
    __tablename__ = "ctf_challenge_doc"
    id             = db.Column(db.Integer, primary_key=True)
    challenge_name = db.Column(db.String(255), nullable=False)
    category       = db.Column(db.String(100), nullable=False, default="Web")
    vuln_type      = db.Column(db.String(100), default="")
    concept        = db.Column(db.Text, default="")
    steps          = db.Column(db.Text, default="[]")   # JSON array of strings
    code_examples  = db.Column(db.Text, default="[]")   # JSON array of {lang, code}
    flag_key       = db.Column(db.String(100), default="")
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


@bp.route("/admin/docs")
@admins_only
def list_docs():
    docs = ChallengeDoc.query.order_by(ChallengeDoc.id).all()
    return jsonify([_doc_to_dict(d) for d in docs])


@bp.route("/admin/docs/<int:doc_id>")
@admins_only
def get_doc(doc_id):
    d = ChallengeDoc.query.get_or_404(doc_id)
    return jsonify(_doc_to_dict(d))


@bp.route("/admin/docs", methods=["POST"])
@admins_only
def create_doc():
    data = request.get_json(silent=True) or {}
    name = (data.get("challenge_name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "challenge_name required"}), 400
    d = ChallengeDoc(
        challenge_name=name,
        category=(data.get("category") or "Web").strip(),
        vuln_type=(data.get("vuln_type") or "").strip(),
        concept=(data.get("concept") or "").strip(),
        steps=json.dumps(data.get("steps") or []),
        code_examples=json.dumps(data.get("code_examples") or []),
        flag_key=(data.get("flag_key") or "").strip(),
    )
    db.session.add(d)
    db.session.commit()
    return jsonify({"success": True, "id": d.id})


@bp.route("/admin/docs/<int:doc_id>", methods=["PUT"])
@admins_only
def update_doc(doc_id):
    d = ChallengeDoc.query.get_or_404(doc_id)
    data = request.get_json(silent=True) or {}
    if "challenge_name" in data: d.challenge_name = data["challenge_name"].strip()
    if "category"       in data: d.category       = data["category"].strip()
    if "vuln_type"      in data: d.vuln_type       = data["vuln_type"].strip()
    if "concept"        in data: d.concept         = data["concept"].strip()
    if "steps"          in data: d.steps           = json.dumps(data["steps"])
    if "code_examples"  in data: d.code_examples   = json.dumps(data["code_examples"])
    if "flag_key"       in data: d.flag_key        = data["flag_key"].strip()
    d.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/docs/<int:doc_id>", methods=["DELETE"])
@admins_only
def delete_doc(doc_id):
    d = ChallengeDoc.query.get_or_404(doc_id)
    db.session.delete(d)
    db.session.commit()
    return jsonify({"success": True})


def _doc_to_dict(d):
    return {
        "id":             d.id,
        "challenge_name": d.challenge_name,
        "category":       d.category,
        "vuln_type":      d.vuln_type,
        "concept":        d.concept,
        "steps":          json.loads(d.steps or "[]"),
        "code_examples":  json.loads(d.code_examples or "[]"),
        "flag_key":       d.flag_key,
        "updated_at":     d.updated_at.isoformat() if d.updated_at else None,
    }


# =============================================================================
# SCORE HISTORY (for live chart)
# =============================================================================

@bp.route("/score-history")
def score_history():
    """Return cumulative score-over-time for top N players (for Chart.js)."""
    try:
        top_users = (
            db.session.query(Users.id, Users.name,
                             db.func.sum(Challenges.value).label("total"))
            .join(Solves, Solves.user_id == Users.id)
            .join(Challenges, Challenges.id == Solves.challenge_id)
            .filter(Users.hidden == False, Users.banned == False)
            .group_by(Users.id, Users.name)
            .order_by(db.desc("total"))
            .limit(10)
            .all()
        )
        top_ids = [u.id for u in top_users]
        user_map = {u.id: u.name for u in top_users}

        solves = (
            db.session.query(Solves.user_id, Solves.date, Challenges.value)
            .join(Challenges, Challenges.id == Solves.challenge_id)
            .filter(Solves.user_id.in_(top_ids))
            .order_by(Solves.date.asc())
            .all()
        )

        # Build cumulative series per user
        series: dict = {uid: [] for uid in top_ids}
        running: dict = {uid: 0 for uid in top_ids}
        for s in solves:
            running[s.user_id] += s.value
            series[s.user_id].append({
                "x": s.date.isoformat(),
                "y": running[s.user_id],
            })

        return jsonify([{
            "user_id":  uid,
            "username": user_map[uid],
            "data":     series[uid],
        } for uid in top_ids])
    except Exception as e:
        logger.warning(f"[score-history] {e}")
        return jsonify([])


# =============================================================================
# PROGRESS (per-category solve status for current user)
# =============================================================================

@bp.route("/progress/me")
@authed_only
def my_progress():
    user = get_current_user()
    if not user:
        abort(401)
    solved_ids = {s.challenge_id for s in Solves.query.filter_by(user_id=user.id).all()}
    chals = Challenges.query.filter_by(state="visible").all()
    categories: dict = {}
    for c in chals:
        cat = c.category or "Unknown"
        if cat not in categories:
            categories[cat] = {"total": 0, "solved": 0}
        categories[cat]["total"] += 1
        if c.id in solved_ids:
            categories[cat]["solved"] += 1

    total = len(chals)
    solved = len(solved_ids & {c.id for c in chals})
    return jsonify({
        "total":      total,
        "solved":     solved,
        "categories": [{"name": k, **v} for k, v in sorted(categories.items())],
    })


# =============================================================================
# FEATURE: SUSPICIOUS ACTIVITY ALERTS
# =============================================================================

class SuspiciousActivity(db.Model):
    """Logs flag submissions where user had no active target container."""
    __tablename__ = "ctf_suspicious_activity"
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username     = db.Column(db.String(128), nullable=False)
    challenge_id = db.Column(db.Integer, nullable=False)
    challenge_name = db.Column(db.String(255), default="")
    flag_submitted = db.Column(db.String(512), default="")
    reason       = db.Column(db.String(255), default="no_container")
    ip_address   = db.Column(db.String(64), default="")
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    reviewed     = db.Column(db.Boolean, default=False)


def _has_active_container(username: str) -> bool:
    """Return True if user has a running ctfd-target or ctfd-kali container."""
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        safe = "".join(c if c.isalnum() else "-" for c in username.lower())
        for prefix in ("ctfd-target-", "ctfd-kali-"):
            try:
                c = client.containers.get(prefix + safe)
                if c.status == "running":
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def register_suspicious_activity_hook(app):
    @app.after_request
    def _check_suspicious(response):
        if request.method != "POST" or "attempt" not in request.path:
            return response
        if response.status_code != 200:
            return response
        try:
            import re
            m = re.match(r"^/api/v1/challenges/(\d+)/attempt$", request.path)
            if not m:
                return response
            body = response.get_json(silent=True) or {}
            if not (body.get("success") and body.get("data", {}).get("status") == "correct"):
                return response
            user = get_current_user()
            if not user or user.admin:
                return response
            if not _has_active_container(user.name):
                challenge_id = int(m.group(1))
                chal = Challenges.query.get(challenge_id)
                alert = SuspiciousActivity(
                    user_id=user.id,
                    username=user.name,
                    challenge_id=challenge_id,
                    challenge_name=chal.name if chal else f"#{challenge_id}",
                    flag_submitted=(request.get_json(silent=True) or {}).get("submission", "")[:200],
                    reason="no_container",
                    ip_address=request.remote_addr or "",
                )
                db.session.add(alert)
                db.session.commit()
                _push_sse("suspicious_activity", {
                    "username": user.name,
                    "challenge": chal.name if chal else f"#{challenge_id}",
                    "reason": "Solved without launching a target container",
                    "ts": datetime.utcnow().isoformat(),
                })
                logger.warning(f"[SuspiciousActivity] {user.name} solved #{challenge_id} with no container")
        except Exception as exc:
            logger.debug(f"[SuspiciousActivity hook] {exc}")
        return response


@bp.route("/admin/suspicious", methods=["GET"])
@admins_only
def list_suspicious():
    rows = SuspiciousActivity.query.order_by(SuspiciousActivity.created_at.desc()).limit(200).all()
    return jsonify([{
        "id":             r.id,
        "username":       r.username,
        "challenge_name": r.challenge_name,
        "reason":         r.reason,
        "ip_address":     r.ip_address,
        "created_at":     r.created_at.isoformat(),
        "reviewed":       r.reviewed,
    } for r in rows])


@bp.route("/admin/suspicious/<int:alert_id>/review", methods=["POST"])
@admins_only
def mark_reviewed(alert_id):
    r = SuspiciousActivity.query.get_or_404(alert_id)
    r.reviewed = True
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/suspicious/dashboard")
@admins_only
def suspicious_dashboard():
    from flask import render_template as _rt
    return _rt("admin/suspicious_activity.html")


# =============================================================================
# FEATURE: LIVE SOLVE FEED (push solve SSE on every correct submission)
# =============================================================================

def register_solve_feed_hook(app):
    @app.after_request
    def _push_solve_event(response):
        if request.method != "POST" or "attempt" not in request.path:
            return response
        if response.status_code != 200:
            return response
        try:
            import re
            m = re.match(r"^/api/v1/challenges/(\d+)/attempt$", request.path)
            if not m:
                return response
            body = response.get_json(silent=True) or {}
            if not (body.get("success") and body.get("data", {}).get("status") == "correct"):
                return response
            user = get_current_user()
            if user and not user.admin:
                challenge_id = int(m.group(1))
                chal = Challenges.query.get(challenge_id)
                _push_sse("solve", {
                    "username":       user.name,
                    "challenge_id":   challenge_id,
                    "challenge_name": chal.name if chal else f"#{challenge_id}",
                    "points":         chal.value if chal else 0,
                    "category":       chal.category if chal else "",
                    "ts":             datetime.utcnow().isoformat(),
                })
        except Exception as exc:
            logger.debug(f"[SolveFeed hook] {exc}")
        return response


@bp.route("/admin/solve-feed/dashboard")
@admins_only
def solve_feed_dashboard():
    from flask import render_template as _rt
    return _rt("admin/solve_feed.html")


@bp.route("/solves/recent")
@admins_only
def recent_solves():
    """Last 100 solves for the solve feed page."""
    rows = (
        db.session.query(Solves, Challenges, Users)
        .join(Challenges, Challenges.id == Solves.challenge_id)
        .join(Users, Users.id == Solves.user_id)
        .order_by(Solves.date.desc())
        .limit(100)
        .all()
    )
    return jsonify([{
        "username":       u.name,
        "challenge_name": c.name,
        "category":       c.category,
        "points":         c.value,
        "solved_at":      s.date.isoformat(),
    } for s, c, u in rows])


# =============================================================================
# FEATURE: PER-CHALLENGE LEADERBOARD
# =============================================================================

@bp.route("/leaderboard/challenge/<int:challenge_id>")
@admins_only
def challenge_leaderboard(challenge_id):
    """All solvers for a challenge, sorted by solve time."""
    rows = (
        db.session.query(Solves, Users)
        .join(Users, Users.id == Solves.user_id)
        .filter(Solves.challenge_id == challenge_id)
        .order_by(Solves.date.asc())
        .all()
    )
    return jsonify([{
        "rank":       i + 1,
        "username":   u.name,
        "solved_at":  s.date.isoformat(),
        "first_blood": i == 0,
    } for i, (s, u) in enumerate(rows)])


@bp.route("/leaderboard/all-challenges")
@admins_only
def all_challenge_leaderboard():
    """Per-challenge solve counts + first solver, for the leaderboard overview."""
    chals = Challenges.query.order_by(Challenges.value).all()
    first_bloods = {fb.challenge_id: fb for fb in FirstBlood.query.all()}
    fb_names = {u.id: u.name for u in Users.query.filter(
        Users.id.in_([fb.user_id for fb in first_bloods.values()])
    ).all()} if first_bloods else {}

    result = []
    for c in chals:
        solvers = (
            db.session.query(Solves, Users)
            .join(Users, Users.id == Solves.user_id)
            .filter(Solves.challenge_id == c.id)
            .order_by(Solves.date.asc())
            .limit(5)
            .all()
        )
        fb = first_bloods.get(c.id)
        result.append({
            "id":           c.id,
            "name":         c.name,
            "category":     c.category,
            "points":       c.value,
            "solve_count":  len(solvers),
            "first_blood":  fb_names.get(fb.user_id) if fb else None,
            "first_blood_at": fb.solved_at.isoformat() if fb else None,
            "top_solvers":  [{"username": u.name, "solved_at": s.date.isoformat()} for s, u in solvers],
        })
    return jsonify(result)


@bp.route("/admin/leaderboard/dashboard")
@admins_only
def leaderboard_dashboard():
    from flask import render_template as _rt
    return _rt("admin/challenge_leaderboard.html")


# =============================================================================
# FEATURE: CONTAINER HEALTH DASHBOARD
# =============================================================================

@bp.route("/admin/containers/health")
@admins_only
def container_health():
    """All running managed containers with RAM/CPU stats."""
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        managed = client.containers.list(all=True, filters={"label": "ctfd_target_user"})
        result = []
        for c in managed:
            cpu_pct = None
            mem_mb  = None
            mem_pct = None
            try:
                stats = c.stats(stream=False)
                cpu_delta = (stats["cpu_stats"]["cpu_usage"]["total_usage"]
                             - stats["precpu_stats"]["cpu_usage"]["total_usage"])
                sys_delta = (stats["cpu_stats"]["system_cpu_usage"]
                             - stats["precpu_stats"]["system_cpu_usage"])
                ncpu = stats["cpu_stats"].get("online_cpus") or 1
                if sys_delta > 0:
                    cpu_pct = round(cpu_delta / sys_delta * ncpu * 100, 1)
                mem_usage = stats["memory_stats"].get("usage", 0)
                mem_limit = stats["memory_stats"].get("limit", 1)
                mem_mb  = round(mem_usage / 1024 / 1024, 1)
                mem_pct = round(mem_usage / mem_limit * 100, 1)
            except Exception:
                pass
            ports = c.attrs.get("NetworkSettings", {}).get("Ports", {})
            host_port = None
            for bindings in ports.values():
                if bindings:
                    host_port = bindings[0].get("HostPort")
                    break
            result.append({
                "name":      c.name,
                "username":  c.labels.get("ctfd_target_user", "?"),
                "role":      c.labels.get("ctfd_target_role", "?"),
                "status":    c.status,
                "cpu_pct":   cpu_pct,
                "mem_mb":    mem_mb,
                "mem_pct":   mem_pct,
                "port":      host_port,
                "expires_at": c.labels.get("ctfd_target_expires"),
            })
        result.sort(key=lambda x: (x["username"], x["role"]))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/admin/containers/<name>/kill", methods=["DELETE"])
@admins_only
def kill_container(name):
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        c = client.containers.get(name)
        c.remove(force=True)
        logger.info(f"[ContainerHealth] Admin killed container: {name}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/admin/containers/dashboard")
@admins_only
def containers_dashboard():
    from flask import render_template as _rt
    return _rt("admin/container_health.html")


# =============================================================================
# FEATURE: ANNOUNCEMENT SCHEDULING (add scheduled_at column + filter)
# =============================================================================

def _ensure_scheduled_at_column(app):
    """Add scheduled_at column to ctf_announcement if not present (safe migration)."""
    with app.app_context():
        try:
            db.engine.execute("ALTER TABLE ctf_announcement ADD COLUMN scheduled_at DATETIME NULL")
            logger.info("[Announcements] Added scheduled_at column")
        except Exception:
            pass  # already exists or table not yet created


# Patch Announcement model to include scheduled_at
try:
    if not hasattr(Announcement, "scheduled_at"):
        Announcement.scheduled_at = db.Column(db.DateTime, nullable=True)
except Exception:
    pass


# Override list_announcements to filter scheduled ones
@bp.route("/announcements/v2")
def list_announcements_v2():
    """Announcements visible now (scheduled_at is null or in the past)."""
    now = datetime.utcnow()
    rows = Announcement.query.filter(
        db.or_(
            Announcement.scheduled_at == None,
            Announcement.scheduled_at <= now,
        )
    ).order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all()
    return jsonify([{
        "id":           a.id,
        "title":        a.title,
        "body":         a.body,
        "pinned":       a.pinned,
        "created_at":   a.created_at.isoformat(),
        "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
    } for a in rows])


@bp.route("/admin/announcements/v2", methods=["POST"])
@admins_only
def create_announcement_v2():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body  = (data.get("body")  or "").strip()
    if not title or not body:
        return jsonify({"success": False, "message": "title and body required"}), 400
    scheduled_at = None
    raw_sched = data.get("scheduled_at")
    if raw_sched:
        try:
            scheduled_at = datetime.fromisoformat(raw_sched.replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:
            pass
    user = get_current_user()
    a = Announcement(
        title=title, body=body,
        pinned=bool(data.get("pinned", False)),
        created_by=user.id if user else None,
    )
    try:
        a.scheduled_at = scheduled_at
    except Exception:
        pass
    db.session.add(a)
    db.session.commit()
    if scheduled_at is None or scheduled_at <= datetime.utcnow():
        _push_sse("announcement", {"id": a.id, "title": a.title, "body": a.body, "pinned": a.pinned})
    return jsonify({"success": True, "id": a.id, "scheduled": scheduled_at is not None})


@bp.route("/admin/announcements/v2/<int:ann_id>", methods=["DELETE"])
@admins_only
def delete_announcement_v2(ann_id):
    a = Announcement.query.get(ann_id)
    if not a:
        return jsonify({"success": False, "error": "Not found"}), 404
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/admin/announcements/all")
@admins_only
def list_all_announcements_admin():
    """All announcements including future-scheduled ones."""
    rows = Announcement.query.order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all()
    now = datetime.utcnow()
    return jsonify([{
        "id":           a.id,
        "title":        a.title,
        "body":         a.body,
        "pinned":       a.pinned,
        "created_at":   a.created_at.isoformat(),
        "scheduled_at": a.scheduled_at.isoformat() if getattr(a, "scheduled_at", None) else None,
        "live":         getattr(a, "scheduled_at", None) is None or a.scheduled_at <= now,
    } for a in rows])


# =============================================================================
# FEATURE: FIRST BLOOD PUBLIC API (for badge injection on challenge cards)
# =============================================================================

@bp.route("/first-blood/map")
def first_blood_map():
    """Dict of challenge_id -> {username, solved_at} for all first bloods."""
    rows = (
        db.session.query(FirstBlood, Users)
        .join(Users, Users.id == FirstBlood.user_id)
        .all()
    )
    return jsonify({
        str(fb.challenge_id): {
            "username":  u.name,
            "solved_at": fb.solved_at.isoformat(),
        } for fb, u in rows
    })


# =============================================================================
# FEATURE: FLAG ATTEMPT LOG
# =============================================================================

class FlagAttemptLog(db.Model):
    """Every flag submission attempt — correct and incorrect."""
    __tablename__ = "ctf_flag_attempt_log"
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username       = db.Column(db.String(128), nullable=False)
    challenge_id   = db.Column(db.Integer, db.ForeignKey("challenges.id"), nullable=False)
    challenge_name = db.Column(db.String(255), nullable=False, default="")
    flag_submitted = db.Column(db.String(512), nullable=False, default="")
    correct        = db.Column(db.Boolean, nullable=False, default=False)
    ip_address     = db.Column(db.String(64), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow, index=True)


def register_flag_attempt_hook(app):
    """Log every flag submission attempt after it is processed."""
    import re as _re

    @app.after_request
    def _log_flag_attempt(response):
        if request.method != "POST":
            return response
        m = _re.match(r"^/api/v1/challenges/(\d+)/attempt$", request.path)
        if not m:
            return response
        if response.status_code != 200:
            return response
        try:
            body = response.get_json(silent=True) or {}
            if not body.get("success"):
                return response
            status = (body.get("data") or {}).get("status", "")
            if status not in ("correct", "incorrect", "already_solved"):
                return response

            user = get_current_user()
            if not user or getattr(user, "type", "") == "admin":
                return response

            challenge_id = int(m.group(1))
            chal = Challenges.query.get(challenge_id)

            # Read submitted flag from request body (JSON or form)
            req_data = request.get_json(silent=True) or {}
            submitted = str(req_data.get("submission", ""))[:512]

            ip = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr
                or ""
            )

            entry = FlagAttemptLog(
                user_id=user.id,
                username=user.name,
                challenge_id=challenge_id,
                challenge_name=chal.name if chal else f"#{challenge_id}",
                flag_submitted=submitted,
                correct=(status in ("correct", "already_solved")),
                ip_address=ip,
            )
            db.session.add(entry)
            db.session.commit()
        except Exception as exc:
            logger.debug(f"[FlagAttemptLog hook] {exc}")
            try:
                db.session.rollback()
            except Exception:
                pass
        return response


@bp.route("/admin/flag-attempts")
@admins_only
def list_flag_attempts():
    """Return paginated flag attempt log, newest first."""
    page      = request.args.get("page", 1, type=int)
    per_page  = request.args.get("per_page", 100, type=int)
    user_q    = (request.args.get("user") or "").strip().lower()
    chal_q    = (request.args.get("challenge") or "").strip().lower()
    correct_f = request.args.get("correct")   # "1" | "0" | None

    q = FlagAttemptLog.query
    if user_q:
        q = q.filter(FlagAttemptLog.username.ilike(f"%{user_q}%"))
    if chal_q:
        q = q.filter(FlagAttemptLog.challenge_name.ilike(f"%{chal_q}%"))
    if correct_f == "1":
        q = q.filter(FlagAttemptLog.correct == True)
    elif correct_f == "0":
        q = q.filter(FlagAttemptLog.correct == False)

    total   = q.count()
    entries = q.order_by(FlagAttemptLog.created_at.desc()) \
               .offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        "total":   total,
        "page":    page,
        "per_page": per_page,
        "entries": [{
            "id":             e.id,
            "username":       e.username,
            "challenge_id":   e.challenge_id,
            "challenge_name": e.challenge_name,
            "flag_submitted": e.flag_submitted,
            "correct":        e.correct,
            "ip_address":     e.ip_address,
            "created_at":     e.created_at.isoformat(),
        } for e in entries],
    })


@bp.route("/admin/flag-attempts/stats")
@admins_only
def flag_attempt_stats():
    """Aggregate stats for the attempt log dashboard."""
    total      = FlagAttemptLog.query.count()
    correct    = FlagAttemptLog.query.filter_by(correct=True).count()
    wrong      = total - correct
    # Top 10 wrong-attempt users
    from sqlalchemy import func as _func
    wrong_users = (
        db.session.query(FlagAttemptLog.username,
                         _func.count(FlagAttemptLog.id).label("cnt"))
        .filter(FlagAttemptLog.correct == False)
        .group_by(FlagAttemptLog.username)
        .order_by(_func.count(FlagAttemptLog.id).desc())
        .limit(10).all()
    )
    # Challenges with most wrong attempts
    wrong_chals = (
        db.session.query(FlagAttemptLog.challenge_name,
                         _func.count(FlagAttemptLog.id).label("cnt"))
        .filter(FlagAttemptLog.correct == False)
        .group_by(FlagAttemptLog.challenge_name)
        .order_by(_func.count(FlagAttemptLog.id).desc())
        .limit(10).all()
    )
    return jsonify({
        "total":       total,
        "correct":     correct,
        "wrong":       wrong,
        "wrong_users": [{"username": u, "count": c} for u, c in wrong_users],
        "wrong_chals": [{"challenge": c, "count": n} for c, n in wrong_chals],
    })


@bp.route("/admin/flag-attempts/dashboard")
@admins_only
def flag_attempts_dashboard():
    from flask import render_template as _rt
    return _rt("admin/flag_attempts.html")


# =============================================================================
# FEATURE: HINT USAGE ANALYTICS
# =============================================================================

class HintUsageLog(db.Model):
    """Records every hint unlock event."""
    __tablename__ = "ctf_hint_usage_log"
    id             = db.Column(db.Integer, primary_key=True)
    hint_id        = db.Column(db.Integer, nullable=False)
    challenge_id   = db.Column(db.Integer, db.ForeignKey("challenges.id"), nullable=False)
    challenge_name = db.Column(db.String(255), nullable=False, default="")
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username       = db.Column(db.String(128), nullable=False)
    cost           = db.Column(db.Integer, default=0)
    unlocked_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)


def register_hint_usage_hook(app):
    """Log hint unlocks when users purchase hints via CTFd API."""
    import re as _re

    @app.after_request
    def _log_hint_usage(response):
        if request.method != "POST":
            return response
        # CTFd hint unlock: POST /api/v1/hints/<id>
        m = _re.match(r"^/api/v1/hints/(\d+)$", request.path)
        if not m:
            return response
        if response.status_code != 200:
            return response
        try:
            body = response.get_json(silent=True) or {}
            if not (body.get("success") and body.get("data")):
                return response

            hint_id   = int(m.group(1))
            hint_data = body["data"]

            user = get_current_user()
            if not user or getattr(user, "type", "") == "admin":
                return response

            challenge_id = hint_data.get("challenge_id", 0)
            chal         = Challenges.query.get(challenge_id)
            cost         = hint_data.get("cost", 0) or 0

            entry = HintUsageLog(
                hint_id=hint_id,
                challenge_id=challenge_id,
                challenge_name=chal.name if chal else f"#{challenge_id}",
                user_id=user.id,
                username=user.name,
                cost=cost,
            )
            db.session.add(entry)
            db.session.commit()
        except Exception as exc:
            logger.debug(f"[HintUsageLog hook] {exc}")
            try:
                db.session.rollback()
            except Exception:
                pass
        return response


@bp.route("/admin/hint-analytics")
@admins_only
def hint_analytics():
    """Aggregated hint usage stats."""
    from sqlalchemy import func as _func

    # Per-challenge totals
    per_chal = (
        db.session.query(
            HintUsageLog.challenge_name,
            _func.count(HintUsageLog.id).label("uses"),
            _func.count(_func.distinct(HintUsageLog.user_id)).label("users"),
            _func.sum(HintUsageLog.cost).label("pts_spent"),
        )
        .group_by(HintUsageLog.challenge_name)
        .order_by(_func.count(HintUsageLog.id).desc())
        .all()
    )
    # Per-user totals
    per_user = (
        db.session.query(
            HintUsageLog.username,
            _func.count(HintUsageLog.id).label("hints_used"),
            _func.sum(HintUsageLog.cost).label("pts_spent"),
        )
        .group_by(HintUsageLog.username)
        .order_by(_func.count(HintUsageLog.id).desc())
        .limit(20).all()
    )
    # Recent unlocks
    recent = (
        HintUsageLog.query
        .order_by(HintUsageLog.unlocked_at.desc())
        .limit(50).all()
    )

    total_uses     = HintUsageLog.query.count()
    total_pts_lost = db.session.query(_func.sum(HintUsageLog.cost)).scalar() or 0

    return jsonify({
        "total_uses":     total_uses,
        "total_pts_lost": int(total_pts_lost),
        "per_challenge":  [{"challenge": r.challenge_name, "uses": r.uses, "users": r.users, "pts_spent": int(r.pts_spent or 0)} for r in per_chal],
        "per_user":       [{"username": r.username, "hints_used": r.hints_used, "pts_spent": int(r.pts_spent or 0)} for r in per_user],
        "recent":         [{
            "username":       e.username,
            "challenge_name": e.challenge_name,
            "hint_id":        e.hint_id,
            "cost":           e.cost,
            "unlocked_at":    e.unlocked_at.isoformat(),
        } for e in recent],
    })


@bp.route("/admin/hint-analytics/dashboard")
@admins_only
def hint_analytics_dashboard():
    from flask import render_template as _rt
    return _rt("admin/hint_analytics.html")


# =============================================================================
# FEATURE: AUTO-PAUSE IDLE CONTAINERS
# =============================================================================

import threading as _threading

_container_last_seen: dict = {}   # {container_name: unix_timestamp}
_idle_lock = _threading.Lock()
_idle_thread = None

IDLE_THRESHOLD_SEC = 1800   # 30 minutes default
_idle_paused: set = set()   # container names currently paused


def record_container_activity(container_name: str):
    """Called by the target / kali endpoint wrappers to update last-seen time."""
    with _idle_lock:
        _container_last_seen[container_name] = time.time()
        if container_name in _idle_paused:
            _idle_paused.discard(container_name)
            _try_resume_container(container_name)


def _try_resume_container(name: str):
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        c = client.containers.get(name)
        if c.status == "paused":
            c.unpause()
            logger.info(f"[AutoPause] Resumed container: {name}")
    except Exception as exc:
        logger.debug(f"[AutoPause] resume error for {name}: {exc}")


def _idle_monitor_loop(threshold: int):
    """Background thread: pause containers idle longer than `threshold` seconds."""
    while True:
        try:
            import docker as _docker
            client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
            now = time.time()
            managed = client.containers.list(filters={"status": "running"})
            for c in managed:
                name = c.name
                if not (name.startswith("ctfd-target-") or name.startswith("ctfd-kali-")):
                    continue
                with _idle_lock:
                    last = _container_last_seen.get(name)
                # If never seen, seed to now (don't immediately pause brand-new containers)
                if last is None:
                    with _idle_lock:
                        _container_last_seen[name] = now
                    continue
                idle_sec = now - last
                if idle_sec >= threshold and name not in _idle_paused:
                    try:
                        c.pause()
                        with _idle_lock:
                            _idle_paused.add(name)
                        logger.info(f"[AutoPause] Paused idle container: {name} (idle {int(idle_sec)}s)")
                        _push_sse("container_paused", {
                            "name":     name,
                            "idle_sec": int(idle_sec),
                            "ts":       datetime.utcnow().isoformat(),
                        })
                    except Exception as exc:
                        logger.debug(f"[AutoPause] pause error for {name}: {exc}")
        except Exception as exc:
            logger.debug(f"[AutoPause] monitor loop error: {exc}")
        _threading.Event().wait(60)   # check every 60 seconds


def start_idle_monitor(threshold_sec: int = IDLE_THRESHOLD_SEC):
    global _idle_thread
    if _idle_thread and _idle_thread.is_alive():
        return
    _idle_thread = _threading.Thread(
        target=_idle_monitor_loop,
        args=(threshold_sec,),
        daemon=True,
        name="ctf-idle-monitor",
    )
    _idle_thread.start()
    logger.info(f"[AutoPause] Idle monitor started (threshold={threshold_sec}s)")


@bp.route("/admin/idle-containers")
@admins_only
def list_idle_containers():
    """Current idle / paused status of all managed containers."""
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        now = time.time()
        all_c = client.containers.list(all=True, filters={"name": "ctfd-"})
        result = []
        for c in all_c:
            name = c.name
            if not (name.startswith("ctfd-target-") or name.startswith("ctfd-kali-")):
                continue
            with _idle_lock:
                last = _container_last_seen.get(name)
                paused = name in _idle_paused
            idle_sec = int(now - last) if last else None
            result.append({
                "name":     name,
                "status":   c.status,
                "paused":   paused or c.status == "paused",
                "idle_sec": idle_sec,
                "last_seen": datetime.utcfromtimestamp(last).isoformat() if last else None,
            })
        result.sort(key=lambda x: x["idle_sec"] or 0, reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/admin/idle-containers/<name>/resume", methods=["POST"])
@admins_only
def resume_container(name):
    """Force-resume a paused container."""
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        c = client.containers.get(name)
        if c.status == "paused":
            c.unpause()
        with _idle_lock:
            _idle_paused.discard(name)
            _container_last_seen[name] = time.time()
        logger.info(f"[AutoPause] Admin manually resumed: {name}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/admin/idle-containers/<name>/pause", methods=["POST"])
@admins_only
def pause_container_manual(name):
    """Force-pause a running container immediately."""
    try:
        import docker as _docker
        client = _docker.DockerClient(base_url="unix:///var/run/docker.sock")
        c = client.containers.get(name)
        if c.status == "running":
            c.pause()
        with _idle_lock:
            _idle_paused.add(name)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/admin/idle-containers/config", methods=["GET", "POST"])
@admins_only
def idle_config():
    """Get/set the idle threshold."""
    global IDLE_THRESHOLD_SEC
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        secs = int(data.get("threshold_sec", IDLE_THRESHOLD_SEC))
        if 60 <= secs <= 86400:
            IDLE_THRESHOLD_SEC = secs
            return jsonify({"success": True, "threshold_sec": IDLE_THRESHOLD_SEC})
        return jsonify({"success": False, "error": "Must be 60–86400 seconds"}), 400
    return jsonify({"threshold_sec": IDLE_THRESHOLD_SEC})


@bp.route("/admin/idle-containers/dashboard")
@admins_only
def idle_containers_dashboard():
    from flask import render_template as _rt
    return _rt("admin/idle_containers.html")


# Register activity tracking on target/kali container start
def register_container_activity_hook(app):
    """Update last-seen timestamp when users interact with their containers."""
    import re as _re

    @app.after_request
    def _track_container_activity(response):
        if response.status_code not in (200, 201):
            return response
        # target start/stop/extend + kali start/stop
        if not _re.search(r"/plugins/ctfd-(target|kali)/", request.path):
            return response
        try:
            body = response.get_json(silent=True) or {}
            container_name = (
                body.get("container_name")
                or body.get("name")
                or body.get("hostname")
            )
            if container_name:
                record_container_activity(container_name)
            else:
                # Fallback: refresh last-seen for all containers owned by this user
                user = get_current_user()
                if user:
                    safe = user.name.lower().replace(" ", "_")
                    for prefix in (f"ctfd-target-{safe}", f"ctfd-kali-{safe}"):
                        with _idle_lock:
                            if prefix in _container_last_seen:
                                _container_last_seen[prefix] = time.time()
        except Exception as exc:
            logger.debug(f"[AutoPause activity hook] {exc}")
        return response


# =============================================================================
# SPECTATOR / PROJECTOR MODE
# =============================================================================

@bp.route("/spectator")
def spectator():
    """Public full-screen projector page — no login required."""
    from flask import render_template
    return render_template("spectator.html")


# =============================================================================
# FEATURE: DATA ERASER — granular & bulk delete for admin
# =============================================================================

@bp.route("/admin/challenges/<int:chal_id>/solves", methods=["DELETE"])
@admins_only
def admin_clear_challenge_solves(chal_id):
    """Clear all solve records for a challenge (keeps the challenge intact)."""
    c = Challenges.query.get_or_404(chal_id)
    count = Solves.query.filter_by(challenge_id=chal_id).count()
    Solves.query.filter_by(challenge_id=chal_id).delete(synchronize_session=False)
    db.session.commit()
    logger.info(f"[DataEraser] Admin cleared {count} solves for challenge #{chal_id} '{c.name}'")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/challenges/<int:chal_id>/first-blood", methods=["DELETE"])
@admins_only
def admin_clear_challenge_first_blood(chal_id):
    """Remove the first-blood record for a specific challenge."""
    fb = FirstBlood.query.filter_by(challenge_id=chal_id).first()
    if fb:
        db.session.delete(fb)
        db.session.commit()
        return jsonify({"success": True, "deleted": 1})
    return jsonify({"success": True, "deleted": 0})


@bp.route("/admin/challenges/<int:chal_id>/attempts", methods=["DELETE"])
@admins_only
def admin_clear_challenge_attempts(chal_id):
    """Clear flag attempt log entries for a specific challenge."""
    count = FlagAttemptLog.query.filter_by(challenge_id=chal_id).count()
    FlagAttemptLog.query.filter_by(challenge_id=chal_id).delete(synchronize_session=False)
    db.session.commit()
    logger.info(f"[DataEraser] Admin cleared {count} flag attempts for challenge #{chal_id}")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-solves", methods=["DELETE"])
@admins_only
def admin_reset_all_solves():
    """Wipe every solve record (full scoreboard reset). Cannot be undone."""
    count = Solves.query.count()
    Solves.query.delete(synchronize_session=False)
    db.session.commit()
    logger.warning(f"[DataEraser] Admin wiped ALL {count} solve records")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-first-bloods", methods=["DELETE"])
@admins_only
def admin_reset_all_first_bloods():
    """Wipe every first-blood record."""
    count = FirstBlood.query.count()
    FirstBlood.query.delete(synchronize_session=False)
    db.session.commit()
    logger.warning(f"[DataEraser] Admin wiped ALL {count} first-blood records")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-attempts", methods=["DELETE"])
@admins_only
def admin_reset_all_attempts():
    """Wipe every flag attempt log entry."""
    count = FlagAttemptLog.query.count()
    FlagAttemptLog.query.delete(synchronize_session=False)
    db.session.commit()
    logger.warning(f"[DataEraser] Admin wiped ALL {count} flag attempt log entries")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/challenges/<int:chal_id>/writeups", methods=["DELETE"])
@admins_only
def admin_clear_challenge_writeups(chal_id):
    count = Writeup.query.filter_by(challenge_id=chal_id).count()
    Writeup.query.filter_by(challenge_id=chal_id).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/challenges/<int:chal_id>/votes", methods=["DELETE"])
@admins_only
def admin_clear_challenge_votes(chal_id):
    count = DifficultyVote.query.filter_by(challenge_id=chal_id).count()
    DifficultyVote.query.filter_by(challenge_id=chal_id).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-writeups", methods=["DELETE"])
@admins_only
def admin_reset_all_writeups():
    count = Writeup.query.count()
    Writeup.query.delete(synchronize_session=False)
    db.session.commit()
    logger.warning(f"[DataEraser] Wiped ALL {count} writeups")
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-votes", methods=["DELETE"])
@admins_only
def admin_reset_all_votes():
    count = DifficultyVote.query.count()
    DifficultyVote.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-chat", methods=["DELETE"])
@admins_only
def admin_reset_all_chat():
    count = TeamChatMessage.query.count()
    TeamChatMessage.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-suspicious", methods=["DELETE"])
@admins_only
def admin_reset_all_suspicious():
    count = SuspiciousActivity.query.count()
    SuspiciousActivity.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-hint-logs", methods=["DELETE"])
@admins_only
def admin_reset_all_hint_logs():
    count = HintUsageLog.query.count()
    HintUsageLog.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-announcements", methods=["DELETE"])
@admins_only
def admin_reset_all_announcements():
    count = Announcement.query.count()
    Announcement.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-events", methods=["DELETE"])
@admins_only
def admin_reset_all_events():
    count = ContestEvent.query.count()
    ContestEvent.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


@bp.route("/admin/data/reset-all-wrong-keys", methods=["DELETE"])
@admins_only
def admin_reset_all_wrong_keys():
    count = 0
    try:
        from CTFd.models import WrongKeys
        count = WrongKeys.query.count()
        WrongKeys.query.delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        pass
    return jsonify({"success": True, "deleted": count})


def _sql_wipe(tables):
    """Delete all rows from each table name, return total count. Ignores missing tables."""
    from sqlalchemy import text as _text
    total = 0
    for tbl in tables:
        try:
            with db.engine.connect() as conn:
                r = conn.execute(_text(f"SELECT COUNT(*) FROM `{tbl}`"))
                total += r.scalar() or 0
                conn.execute(_text(f"DELETE FROM `{tbl}`"))
                conn.commit()
        except Exception:
            pass
    return total


@bp.route("/admin/data/reset-all-notifications", methods=["DELETE"])
@admins_only
def admin_reset_all_notifications():
    deleted = _sql_wipe(["notification_log"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-all-analytics", methods=["DELETE"])
@admins_only
def admin_reset_all_analytics():
    deleted = _sql_wipe(["analytics_data", "solve_timeline", "resource_metrics"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-all-sessions", methods=["DELETE"])
@admins_only
def admin_reset_all_sessions():
    deleted = _sql_wipe(["persistent_sessions"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-all-rate-limits", methods=["DELETE"])
@admins_only
def admin_reset_all_rate_limits():
    deleted = _sql_wipe(["api_rate_limit"])
    try:
        r = _redis()
        if r:
            keys = list(r.keys("ctf:rl:*")) + list(r.keys("ctfd:bf:*"))
            if keys:
                r.delete(*keys)
    except Exception:
        pass
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-all-scoring", methods=["DELETE"])
@admins_only
def admin_reset_all_scoring():
    deleted = _sql_wipe(["scoring_config", "dynamic_scores"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-audit-logs", methods=["DELETE"])
@admins_only
def admin_reset_audit_logs():
    deleted = _sql_wipe(["audit_logs_impl", "audit_log"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-player-progress", methods=["DELETE"])
@admins_only
def admin_reset_player_progress():
    deleted = _sql_wipe(["player_streak", "user_achievement", "leaderboard_snapshot",
                          "user_onboarding_impl", "user_approval_requests"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-team-data", methods=["DELETE"])
@admins_only
def admin_reset_team_data():
    deleted = _sql_wipe(["team_analytics", "team_contribution",
                          "team_collaboration", "mission_team_feed_note"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/reset-dynamic-data", methods=["DELETE"])
@admins_only
def admin_reset_dynamic_data():
    deleted = _sql_wipe(["dynamic_environment", "container_snapshot", "health_check"])
    return jsonify({"success": True, "deleted": deleted})


@bp.route("/admin/data/scoreboard-full-reset", methods=["DELETE"])
@admins_only
def admin_scoreboard_full_reset():
    """Wipe every record that contributes to or affects the live scoreboard."""
    try:
        backup_path = _create_database_backup("before-full-scoreboard-reset")
    except Exception as exc:
        logger.error("[DatabaseBackup] Full reset cancelled: %s", exc)
        return jsonify({"success": False, "message": f"Backup failed; reset cancelled: {exc}"}), 503
    deleted = 0
    deleted += Solves.query.count()
    Solves.query.delete(synchronize_session=False)
    deleted += FirstBlood.query.count()
    FirstBlood.query.delete(synchronize_session=False)
    db.session.commit()
    try:
        from CTFd.models import WrongKeys
        deleted += WrongKeys.query.count()
        WrongKeys.query.delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        pass
    deleted += _sql_wipe(["dynamic_scores", "player_streak", "leaderboard_snapshot"])
    try:
        r = _redis()
        if r:
            keys = list(r.keys("ctf:rl:*")) + list(r.keys("ctfd:bf:*"))
            if keys:
                r.delete(*keys)
    except Exception:
        pass
    logger.warning(f"[DataEraser] FULL SCOREBOARD RESET — {deleted} records wiped")
    return jsonify({"success": True, "deleted": deleted, "backup": backup_path})


@bp.route("/admin/backups", methods=["POST"])
@admins_only
def admin_create_backup():
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "manual-admin-backup").strip()
    try:
        path = _create_database_backup(reason)
        return jsonify({"success": True, "backup": path})
    except Exception as exc:
        logger.error("[DatabaseBackup] Manual backup failed: %s", exc)
        return jsonify({"success": False, "message": str(exc)}), 503


@bp.route("/admin/backups", methods=["GET"])
@admins_only
def admin_list_backups():
    backup_dir = os.environ.get("CTFD_BACKUP_DIR", "/var/backups")
    try:
        rows = []
        for name in sorted(os.listdir(backup_dir), reverse=True):
            if not name.endswith(".sql.gz"):
                continue
            path = os.path.join(backup_dir, name)
            rows.append({
                "name": name,
                "size": os.path.getsize(path),
                "created_at": datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat() + "Z",
            })
        return jsonify({"success": True, "backups": rows[:100]})
    except FileNotFoundError:
        return jsonify({"success": True, "backups": []})


@bp.route("/admin/data-eraser/dashboard")
@admins_only
def data_eraser_dashboard():
    from flask import render_template as _rt
    return _rt("admin/data_eraser.html")


@bp.route("/user/change-password", methods=["POST"])
@authed_only
def change_password():
    """User-side password change with verification and immediate session sync."""
    from CTFd.utils.crypto import verify_password, hash_password
    try:
        from CTFd.utils.security.auth import update_user
    except ImportError:
        def update_user(u): pass

    user = get_current_user()
    if not user:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password") or data.get("confirm") or "").strip()
    new_password = str(data.get("new_password") or data.get("password") or "").strip()
    confirm_password = str(data.get("confirm_password") or data.get("new_password_confirm") or "").strip()

    if not current_password:
        return jsonify({"success": False, "message": "Current password is required"}), 400

    if not new_password:
        return jsonify({"success": False, "message": "New password is required"}), 400

    min_len = int(get_config("password_min_length", default=6) or 6)
    if len(new_password) < min_len:
        return jsonify({
            "success": False,
            "message": f"Password must be at least {min_len} characters long"
        }), 400

    if confirm_password and confirm_password != new_password:
        return jsonify({"success": False, "message": "New passwords do not match"}), 400

    if current_password == new_password:
        return jsonify({"success": False, "message": "New password must be different from current password"}), 400

    if not verify_password(plaintext=current_password, ciphertext=user.password):
        return jsonify({"success": False, "message": "Your current password is incorrect"}), 400

    try:
        user.password = new_password
        db.session.commit()
        update_user(user)
        logger.info(f"[PasswordChange] User {user.name} (id={user.id}) successfully updated their password")
        return jsonify({
            "success": True,
            "message": "Your password has been changed successfully. You can now use your new password."
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[PasswordChange] Failed to update password for user {user.id}: {e}")
        return jsonify({"success": False, "message": "Database error while updating password"}), 500


# =============================================================================
# INIT
# =============================================================================

def init_tables(app):
    """Create tables if they don't exist. Falls back to raw SQL on InnoDB tablespace conflicts."""
    with app.app_context():
        try:
            db.create_all()
            logger.info("[CTFFeatures] Tables created/verified")
        except Exception as e:
            logger.warning(f"[CTFFeatures] Table init warning: {e}")
            # Fallback: create ctf_challenge_doc directly if tablespace orphan blocks create_all
            try:
                from sqlalchemy import text as _text
                with db.engine.connect() as _conn:
                    _conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS ctf_challenge_doc (
                            id INT NOT NULL AUTO_INCREMENT,
                            challenge_name VARCHAR(200) NOT NULL,
                            category VARCHAR(100) DEFAULT 'Web',
                            vuln_type VARCHAR(200) DEFAULT '',
                            concept TEXT,
                            steps TEXT DEFAULT '[]',
                            code_examples TEXT DEFAULT '[]',
                            flag_key VARCHAR(100) DEFAULT '',
                            updated_at DATETIME(6),
                            PRIMARY KEY (id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """))
                logger.info("[CTFFeatures] ctf_challenge_doc created via fallback SQL")
            except Exception as e2:
                logger.warning(f"[CTFFeatures] Fallback table creation also failed: {e2}")
