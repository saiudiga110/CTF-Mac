"""
CTF Live Plugin — real-time features for Lloyds CTF
Provides:
  • First blood notifications (poll-based, Redis-backed)
  • Admin broadcast messages (shown as toasts on all player pages)
  • Challenge difficulty ratings (1-5 stars, stored in Redis)
  • Target auto-restart alerts (per-user Redis key set by target-plugin)
  • Admin live solve feed (paginated JSON + HTML page)
  • Team progress grid (N×M solved/unsolved matrix + HTML page)
  • Scoreboard freeze control (wraps CTFd's built-in freeze config)
"""

import json
import logging
import os
import threading
import time
import uuid

import jinja2
from flask import Blueprint, jsonify, render_template, request

from CTFd.plugins import register_admin_plugin_menu_bar
from CTFd.utils import get_config, set_config
from CTFd.utils import user as current_user
from CTFd.utils.decorators import admins_only, authed_only

logger = logging.getLogger(__name__)

# ── Redis helper ──────────────────────────────────────────────────────────────

def _redis():
    try:
        import redis as _r
        client = _r.from_url(
            os.environ.get("REDIS_URL", "redis://cache:6379"),
            socket_connect_timeout=1, socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception:
        return None


def load(app):
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    tmpl_dir   = os.path.join(plugin_dir, "templates")

    bp = Blueprint(
        "ctf-live", __name__,
        template_folder="templates",
        url_prefix="/plugins/ctf-live",
    )

    def _render(template_name, **ctx):
        orig = app.jinja_loader
        try:
            app.jinja_loader = jinja2.ChoiceLoader([
                jinja2.FileSystemLoader(tmpl_dir), orig
            ])
            return render_template(template_name, **ctx)
        finally:
            app.jinja_loader = orig

    # ─────────────────────────────────────────────────────────────────────────
    # FIRST BLOOD
    # ─────────────────────────────────────────────────────────────────────────

    def _init_fb():
        """Pre-seed announced set with all challenges that already have solves."""
        try:
            from CTFd.models import Solves
            r = _redis()
            if not r:
                return
            existing = Solves.query.with_entities(Solves.challenge_id).distinct().all()
            for (cid,) in existing:
                r.sadd("ctfd:fb:announced", str(cid))
        except Exception as exc:
            logger.warning(f"[CTFLive] FB init: {exc}")

    def _check_first_bloods():
        """Return list of new first-blood dicts (challenge just got its 1st solve)."""
        try:
            from CTFd.models import Solves, Challenges
            r = _redis()
            challenges = Challenges.query.filter_by(state="visible").all()
            bloods = []
            for chal in challenges:
                cid = str(chal.id)
                if r and r.sismember("ctfd:fb:announced", cid):
                    continue
                first = (Solves.query
                         .filter_by(challenge_id=chal.id)
                         .order_by(Solves.date.asc())
                         .first())
                if first:
                    if r:
                        r.sadd("ctfd:fb:announced", cid)
                    solver = first.user.name if first.user else "Someone"
                    bloods.append({
                        "id":         f"fb-{cid}",
                        "challenge":  chal.name,
                        "category":   chal.category or "",
                        "solver":     solver,
                        "points":     chal.value,
                        "ts":         first.date.isoformat() if first.date else "",
                    })
            return bloods
        except Exception as exc:
            logger.warning(f"[CTFLive] FB check: {exc}")
            return []

    def _delayed_fb_init():
        time.sleep(8)
        with app.app_context():
            _init_fb()

    threading.Thread(target=_delayed_fb_init, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # BROADCASTS
    # ─────────────────────────────────────────────────────────────────────────

    def _get_broadcasts():
        r = _redis()
        if not r:
            return []
        try:
            raw = r.get("ctfd:broadcasts")
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def _save_broadcasts(data):
        r = _redis()
        if r:
            r.set("ctfd:broadcasts", json.dumps(data))

    # ─────────────────────────────────────────────────────────────────────────
    # DIFFICULTY RATINGS
    # ─────────────────────────────────────────────────────────────────────────

    def _all_ratings():
        r = _redis()
        if not r:
            return {}
        try:
            keys = r.keys("ctfd:rating:chal:*")
            out = {}
            for key in keys:
                cid = key.split(":")[-1]
                s = int(r.hget(key, "sum") or 0)
                c = int(r.hget(key, "count") or 0)
                if c > 0:
                    out[cid] = {"avg": round(s / c, 1), "count": c}
            return out
        except Exception:
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # NEWLY SOLVED  (triggers rating prompt on client)
    # ─────────────────────────────────────────────────────────────────────────

    def _newly_solved(user_id):
        try:
            from CTFd.models import Solves, Challenges
            r = _redis()
            solves = (Solves.query
                      .filter_by(user_id=user_id)
                      .with_entities(Solves.challenge_id)
                      .all())
            current = {str(s.challenge_id) for s in solves}
            notified_key = f"ctfd:rating_prompted:{user_id}"
            already = set(r.smembers(notified_key)) if r else set()
            new_ids = current - already
            if new_ids and r:
                r.sadd(notified_key, *new_ids)
            result = []
            for cid in new_ids:
                chal = Challenges.query.get(int(cid))
                if chal:
                    result.append({"id": int(cid), "name": chal.name})
            return result
        except Exception as exc:
            logger.warning(f"[CTFLive] newly_solved: {exc}")
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTES — player-facing
    # ─────────────────────────────────────────────────────────────────────────

    @bp.route("/events")
    @authed_only
    def events():
        user = current_user.get_current_user()
        r    = _redis()

        restart_alert = None
        if r and user:
            raw = r.get(f"ctfd:restart_alert:{user.name}")
            if raw:
                r.delete(f"ctfd:restart_alert:{user.name}")
                try:
                    restart_alert = json.loads(raw)
                except Exception:
                    pass

        return jsonify({
            "first_bloods":   _check_first_bloods(),
            "broadcasts":     _get_broadcasts(),
            "restart_alert":  restart_alert,
            "newly_solved":   _newly_solved(user.id) if user else [],
            "ratings":        _all_ratings(),
        })

    @bp.route("/rating", methods=["POST"])
    @authed_only
    def submit_rating():
        user = current_user.get_current_user()
        if not user:
            return jsonify({"success": False})
        data   = request.get_json() or {}
        cid    = str(data.get("challenge_id", ""))
        rating = int(data.get("rating", 0))
        if not cid or not (1 <= rating <= 5):
            return jsonify({"success": False, "msg": "Invalid"})
        r = _redis()
        if not r:
            return jsonify({"success": False, "msg": "Unavailable"})
        user_key = f"ctfd:rating:user:{user.id}:{cid}"
        if r.exists(user_key):
            return jsonify({"success": False, "msg": "Already rated"})
        r.set(user_key, rating)
        chal_key = f"ctfd:rating:chal:{cid}"
        r.hincrby(chal_key, "sum", rating)
        r.hincrby(chal_key, "count", 1)
        s = int(r.hget(chal_key, "sum") or rating)
        c = int(r.hget(chal_key, "count") or 1)
        return jsonify({"success": True, "avg": round(s / c, 1), "count": c})

    @bp.route("/ratings")
    def get_ratings():
        return jsonify(_all_ratings())

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTES — admin: broadcast
    # ─────────────────────────────────────────────────────────────────────────

    @bp.route("/admin/broadcast", methods=["POST"])
    @admins_only
    def admin_broadcast_send():
        data = request.get_json() or {}
        msg  = (data.get("message") or "").strip()
        if not msg:
            return jsonify({"success": False, "msg": "Empty"})
        bc = {"id": str(uuid.uuid4())[:8], "message": msg, "ts": time.time()}
        broadcasts = _get_broadcasts()
        broadcasts.append(bc)
        _save_broadcasts(broadcasts[-20:])
        return jsonify({"success": True, "broadcast": bc})

    @bp.route("/admin/broadcast/<bid>", methods=["DELETE"])
    @admins_only
    def admin_broadcast_delete(bid):
        _save_broadcasts([b for b in _get_broadcasts() if b.get("id") != bid])
        return jsonify({"success": True})

    @bp.route("/admin/broadcast/clear", methods=["POST"])
    @admins_only
    def admin_broadcast_clear():
        _save_broadcasts([])
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTES — admin: scoreboard freeze
    # ─────────────────────────────────────────────────────────────────────────

    @bp.route("/admin/freeze", methods=["GET"])
    @admins_only
    def admin_freeze_get():
        val = get_config("freeze")
        return jsonify({
            "freeze":  int(val) if val else None,
            "active":  bool(val and time.time() > float(val)),
        })

    @bp.route("/admin/freeze", methods=["POST"])
    @admins_only
    def admin_freeze_set():
        data = request.get_json() or {}
        if data.get("action") == "clear":
            set_config("freeze", None)
            return jsonify({"success": True, "freeze": None})
        ts = data.get("timestamp")
        if ts:
            set_config("freeze", int(ts))
            return jsonify({"success": True, "freeze": int(ts)})
        return jsonify({"success": False, "msg": "Provide timestamp or action=clear"})

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTES — admin: live dashboard page
    # ─────────────────────────────────────────────────────────────────────────

    @bp.route("/admin/live")
    @admins_only
    def admin_live():
        return _render("live_dashboard.html")

    @bp.route("/admin/live/data")
    @admins_only
    def admin_live_data():
        try:
            from CTFd.models import Solves, Challenges, Users
            solves = (Solves.query
                      .order_by(Solves.date.desc())
                      .limit(100).all())
            rows = []
            # cache challenge/user lookups
            chal_cache = {}
            user_cache = {}
            for s in solves:
                if s.challenge_id not in chal_cache:
                    chal_cache[s.challenge_id] = Challenges.query.get(s.challenge_id)
                if s.user_id not in user_cache:
                    user_cache[s.user_id] = Users.query.get(s.user_id)
                chal = chal_cache[s.challenge_id]
                user = user_cache[s.user_id]

                # count solves for this challenge to detect first blood
                solve_count = Solves.query.filter_by(challenge_id=s.challenge_id).count()

                rows.append({
                    "ts":         s.date.strftime("%H:%M:%S") if s.date else "",
                    "user":       user.name if user else "?",
                    "challenge":  chal.name if chal else "?",
                    "category":   chal.category if chal else "?",
                    "points":     chal.value if chal else 0,
                    "first_blood": solve_count == 1,
                })
            return jsonify(rows)
        except Exception as exc:
            return jsonify({"error": str(exc)})

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTES — admin: team progress grid
    # ─────────────────────────────────────────────────────────────────────────

    @bp.route("/admin/progress")
    @admins_only
    def admin_progress():
        return _render("progress_grid.html")

    @bp.route("/admin/progress/data")
    @admins_only
    def admin_progress_data():
        try:
            from CTFd.models import Solves, Challenges, Users
            from CTFd.utils.scores import get_standings

            challenges = (Challenges.query
                          .filter_by(state="visible")
                          .order_by(Challenges.category, Challenges.name)
                          .all())

            standings = get_standings()
            user_scores = {s.account_id: int(s.score) for s in standings}

            # All users with at least 1 solve or on scoreboard
            all_uid = set(user_scores.keys())
            users_q = (Users.query
                       .filter(Users.id.in_(list(all_uid)),
                               Users.banned == False,
                               Users.hidden == False)
                       .all()) if all_uid else []
            users_sorted = sorted(users_q,
                                  key=lambda u: user_scores.get(u.id, 0),
                                  reverse=True)

            all_solves = Solves.query.with_entities(
                Solves.user_id, Solves.challenge_id).all()
            solved_pairs = {(uid, cid) for uid, cid in all_solves}

            return jsonify({
                "challenges": [
                    {"id": c.id, "name": c.name,
                     "category": c.category or "", "value": c.value}
                    for c in challenges
                ],
                "users": [
                    {"id": u.id, "name": u.name,
                     "score": user_scores.get(u.id, 0)}
                    for u in users_sorted
                ],
                "solved": [
                    [uid, cid] for (uid, cid) in solved_pairs
                    if uid in {u.id for u in users_sorted}
                ],
            })
        except Exception as exc:
            logger.error(f"[CTFLive] progress data: {exc}")
            return jsonify({"error": str(exc)})

    # ─────────────────────────────────────────────────────────────────────────
    # Register
    # ─────────────────────────────────────────────────────────────────────────

    register_admin_plugin_menu_bar("Live Feed",      "/plugins/ctf-live/admin/live")
    register_admin_plugin_menu_bar("Progress Grid",  "/plugins/ctf-live/admin/progress")

    app.register_blueprint(bp)
