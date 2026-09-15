"""
vBank CTF — Dynamic HMAC Flag Plugin for CTFd
Each scoring owner receives a unique flag. In individual mode the owner is the
user ID; in explicit teams mode it is the team ID.
Flag content field stores the challenge key (e.g. "sqli_login").
"""
import hmac
import hashlib
import os

from flask import Blueprint
from CTFd.plugins.flags import BaseFlag, FlagException, FLAG_CLASSES


def _get_flag_secret() -> str:
    """Read from DB config (same source target-plugin uses for containers), fall back to env."""
    try:
        from CTFd.utils import get_config
        val = get_config("ctfd_target:flag_secret")
        if val:
            return val
    except Exception:
        pass
    return os.environ.get("FLAG_SECRET", "vbank_ctf_default_2024")


def _compute(team_id: str, challenge_key: str) -> str:
    secret = _get_flag_secret()
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{team_id}:{challenge_key}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"LYD{{{digest}}}"


class VBankDynamicFlag(BaseFlag):
    name = "vbank_dynamic"
    templates = {
        "create": "/plugins/vbank_flags/assets/create.html",
        "update": "/plugins/vbank_flags/assets/update.html",
    }

    @staticmethod
    def compare(flag_obj, provided: str) -> bool:
        try:
            from CTFd.utils.user import get_current_user

            user = get_current_user()
            if user is None:
                return False

            from CTFd.utils import get_config
            # Optional membership must not change an individual's flag owner.
            if get_config("user_mode") == "teams" and getattr(user, "team_id", None):
                team_id = str(user.team_id)
            else:
                team_id = str(user.id)
            challenge_key = flag_obj.content.strip()
            expected = _compute(team_id, challenge_key)
            return provided.strip() == expected
        except Exception:
            return False


# Register at import time so it's available even before load() is called
FLAG_CLASSES["vbank_dynamic"] = VBankDynamicFlag


def load(app):
    # Re-register in case of any edge-case reload ordering
    FLAG_CLASSES["vbank_dynamic"] = VBankDynamicFlag

    # Register a utility blueprint so admins can preview a team's flag
    preview_bp = Blueprint("vbank_flags", __name__)

    @preview_bp.route("/admin/vbank/preview-flag")
    def preview_flag():
        from flask import request, jsonify
        from CTFd.utils.decorators import admins_only

        @admins_only
        def _inner():
            team_id = request.args.get("team_id", "1")
            key = request.args.get("key", "sqli_login")
            return jsonify({"flag": _compute(team_id, key), "team_id": team_id, "key": key})

        return _inner()

    try:
        app.register_blueprint(preview_bp)
    except Exception:
        pass  # Blueprint already registered on worker restart
