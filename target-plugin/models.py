"""Per-challenge Docker target configuration."""
from CTFd.models import db


class ChallengeTargetConfig(db.Model):
    __tablename__ = "ctfd_challenge_target_config"

    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    image_name = db.Column(db.String(255), default="vbank-ctf", nullable=False)
    internal_port = db.Column(db.Integer, default=80, nullable=False)
    needs_analytics = db.Column(db.Boolean, default=False, nullable=False)
    mem_limit = db.Column(db.String(32), default="")
    cpu_quota = db.Column(db.String(32), default="")
    challenge_key = db.Column(db.String(64), default="", nullable=False)
    target_profile = db.Column(db.String(64), default="", nullable=False)
    health_path = db.Column(db.String(255), default="/", nullable=False)

    def to_dict(self):
        return {
            "challenge_id": self.challenge_id,
            "enabled": bool(self.enabled),
            "image_name": self.image_name or "vbank-ctf",
            "internal_port": int(self.internal_port or 80),
            "needs_analytics": bool(self.needs_analytics),
            "mem_limit": self.mem_limit or "",
            "cpu_quota": self.cpu_quota or "",
            "challenge_key": self.challenge_key or "",
            "target_profile": self.target_profile or "",
            "health_path": self.health_path or "/",
        }
