from __future__ import division
import math
from datetime import datetime
import json

from flask import Blueprint

from CTFd.models import (
    db,
    Solves,
    Fails,
    Flags,
    Challenges,
    ChallengeFiles,
    Tags,
    Hints,
)
from CTFd.plugins.flags import BaseFlag, get_flag_class
from CTFd.plugins.challenges import BaseChallenge
from CTFd.utils import user as current_user
from CTFd.utils.modes import get_model
from CTFd.utils.uploads import delete_file
from CTFd.utils.user import get_ip


class DynamicValueDockerChallenge(BaseChallenge):
    id = "dynamic_docker"
    name = "dynamic_docker"
    templates = {
        "create": "/plugins/ctfd-whale/assets/create.html",
        "update": "/plugins/ctfd-whale/assets/update.html",
        "view": "/plugins/ctfd-whale/assets/view.html",
    }
    scripts = {
        "create": "/plugins/ctfd-whale/assets/create.js",
        "update": "/plugins/ctfd-whale/assets/update.js",
        "view": "/plugins/ctfd-whale/assets/view.js",
    }
    route = "/plugins/ctfd-whale/assets/"
    blueprint = Blueprint(
        "ctfd-whale-challenge",
        __name__,
        template_folder="templates",
        static_folder="assets",
    )

    @staticmethod
    def create(request):
        data = request.form or request.get_json()
        def _int(v, default=0):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _float(v, default=0.0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        initial = _int(data.get('value', 0))
        challenge = DynamicDockerChallenge(
            name=data.get('name', ''),
            description=data.get('description', ''),
            category=data.get('category', ''),
            value=initial,
            state=data.get('state', 'hidden'),
            max_attempts=_int(data.get('max_attempts', 0)),
            type='dynamic_docker',
            initial=initial,
            minimum=_int(data.get('minimum', 0)),
            decay=_int(data.get('decay', 0)),
            docker_image=data.get('docker_image', ''),
            redirect_type=data.get('redirect_type', 'direct'),
            redirect_port=_int(data.get('redirect_port', 80)),
            memory_limit=data.get('memory_limit', '128m'),
            cpu_limit=_float(data.get('cpu_limit', 0.5)),
            dynamic_score=_int(data.get('dynamic_score', 0)),
            flags_required=_int(data.get('flags_required', 1)) or 1,
        )
        db.session.add(challenge)
        db.session.commit()
        return challenge

    @staticmethod
    def read(challenge):
        challenge = DynamicDockerChallenge.query.filter_by(id=challenge.id).first()
        data = {
            "id": challenge.id,
            "name": challenge.name,
            "value": challenge.value,
            "initial": challenge.initial,
            "decay": challenge.decay,
            "minimum": challenge.minimum,
            "description": challenge.description,
            "category": challenge.category,
            "state": challenge.state,
            "max_attempts": challenge.max_attempts,
            "type": challenge.type,
            "flags_required": challenge.flags_required,
            "type_data": {
                "id": DynamicValueDockerChallenge.id,
                "name": DynamicValueDockerChallenge.name,
                "templates": DynamicValueDockerChallenge.templates,
                "scripts": DynamicValueDockerChallenge.scripts,
            },
        }
        return data

    @staticmethod
    def update(challenge, request):
        data = request.form or request.get_json()

        _int_fields = {'redirect_port', 'dynamic_score', 'decay', 'minimum',
                       'initial', 'flags_required', 'max_attempts', 'value'}
        _float_fields = {'cpu_limit'}
        _str_fields = {'name', 'description', 'category', 'state',
                       'docker_image', 'redirect_type', 'memory_limit'}

        for attr, val in data.items():
            if attr in _int_fields:
                try:
                    setattr(challenge, attr, int(val))
                except (TypeError, ValueError):
                    pass
            elif attr in _float_fields:
                try:
                    setattr(challenge, attr, float(val))
                except (TypeError, ValueError):
                    pass
            elif attr in _str_fields:
                setattr(challenge, attr, val)

        Model = get_model()

        solve_count = (
            Solves.query.join(Model, Solves.account_id == Model.id)
                .filter(
                Solves.challenge_id == challenge.id,
                Model.hidden == False,
                Model.banned == False,
            )
                .count()
        )

        value = (
                        ((challenge.minimum - challenge.initial) / (challenge.decay ** 2))
                        * (solve_count ** 2)
                ) + challenge.initial

        value = math.ceil(value)

        if value < challenge.minimum:
            value = challenge.minimum

        challenge.value = value

        db.session.commit()
        return challenge

    @staticmethod
    def delete(challenge):
        Fails.query.filter_by(challenge_id=challenge.id).delete()
        Solves.query.filter_by(challenge_id=challenge.id).delete()
        Flags.query.filter_by(challenge_id=challenge.id).delete()
        files = ChallengeFiles.query.filter_by(challenge_id=challenge.id).all()
        for f in files:
            delete_file(f.id)
        ChallengeFiles.query.filter_by(challenge_id=challenge.id).delete()
        Tags.query.filter_by(challenge_id=challenge.id).delete()
        Hints.query.filter_by(challenge_id=challenge.id).delete()
        DynamicDockerChallenge.query.filter_by(id=challenge.id).delete()
        Challenges.query.filter_by(id=challenge.id).delete()
        db.session.commit()


    @staticmethod
    def attempt(challenge, request):
        data = request.form or request.get_json()
        submission = data.get("submission", "").strip()

        # Check submission against any stored static flag — any match wins
        flags = Flags.query.filter_by(challenge_id=challenge.id).all()
        if flags:
            for flag in flags:
                flag_class = get_flag_class(flag.type)
                if flag_class and flag_class.compare(flag, submission):
                    return True, "Correct"
            # No static flag matched — fall through to container flag below
            # (only if there are no static flags at all should we use container flag)
            return False, "Incorrect"

        # No static flags configured: fall back to per-user container flag (whale mode)
        user_id = current_user.get_current_user().id
        q = db.session.query(WhaleContainer)
        q = q.filter(WhaleContainer.user_id == user_id)
        q = q.filter(WhaleContainer.challenge_id == challenge.id)
        records = q.all()
        if not records:
            return False, "Please solve it while your container is running."
        if records[0].flag == submission:
            return True, "Correct"
        return False, "Incorrect"

    @staticmethod
    def solve(user, team, challenge, request):
        chal = DynamicDockerChallenge.query.filter_by(id=challenge.id).first()
        data = request.form or request.get_json()
        submission = data["submission"].strip()

        Model = get_model()

        solve = Solves(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(req=request),
            provided=submission,
        )
        db.session.add(solve)

        if chal.dynamic_score == 1:

            solve_count = (
                Solves.query.join(Model, Solves.account_id == Model.id)
                .filter(
                    Solves.challenge_id == challenge.id,
                    Model.hidden == False,
                    Model.banned == False,
                )
                .count()
            )

            solve_count -= 1

            value = (
                            ((chal.minimum - chal.initial) / (chal.decay ** 2)) * (solve_count ** 2)
                    ) + chal.initial

            value = math.ceil(value)

            if value < chal.minimum:
                value = chal.minimum

            chal.value = value

        db.session.commit()

    @staticmethod
    def fail(user, team, challenge, request):
        data = request.form or request.get_json()
        submission = data["submission"].strip()
        wrong = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(request),
            provided=submission,
        )
        db.session.add(wrong)
        db.session.commit()


class DynamicDockerChallenge(Challenges):
    __mapper_args__ = {"polymorphic_identity": "dynamic_docker"}
    id = db.Column(None, db.ForeignKey("challenges.id"), primary_key=True)

    initial = db.Column(db.Integer, default=0)
    minimum = db.Column(db.Integer, default=0)
    decay = db.Column(db.Integer, default=0)
    memory_limit = db.Column(db.Text, default="128m")
    cpu_limit = db.Column(db.Float, default=0.5)
    dynamic_score = db.Column(db.Integer, default=0)
    flags_required = db.Column(db.Integer, default=1)

    docker_image = db.Column(db.Text, default=0)
    redirect_type = db.Column(db.Text, default=0)
    redirect_port = db.Column(db.Integer, default=0)

    def __init__(self, *args, **kwargs):
        super(DynamicDockerChallenge, self).__init__(**kwargs)
        # Ensure initial mirrors value when not set explicitly
        if 'initial' not in kwargs and 'value' in kwargs:
            try:
                self.initial = int(kwargs['value'])
            except (TypeError, ValueError):
                pass


class WhaleConfig(db.Model):
    key = db.Column(db.String(length=128), primary_key=True)
    value = db.Column(db.Text)

    def __init__(self, key, value):
        self.key = key
        self.value = value
        
class WhaleChallengeProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id"))
    flag_id = db.Column(db.Integer) # ID of the specific flag solved
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'challenge_id', 'flag_id'),)

    def __init__(self, user_id, challenge_id, flag_id):
        self.user_id = user_id
        self.challenge_id = challenge_id
        self.flag_id = flag_id

    def __repr__(self):
        return f"<WhaleChallengeProgress id={self.id} user={self.user_id} challenge={self.challenge_id} flag={self.flag_id}>"


class WhaleContainer(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(None, db.ForeignKey("users.id"))
    challenge_id = db.Column(None, db.ForeignKey("challenges.id"))
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    renew_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.Integer, default=1)
    uuid = db.Column(db.String(256))
    port = db.Column(db.Integer, nullable=True, default=0)
    flag = db.Column(db.String(128), nullable=False)

    # Relationships
    user = db.relationship("Users", foreign_keys="WhaleContainer.user_id", lazy="select")
    challenge = db.relationship(
        "Challenges", foreign_keys="WhaleContainer.challenge_id", lazy="select"
    )

    def __init__(self, user_id, challenge_id, flag, uuid, port):
        self.user_id = user_id
        self.challenge_id = challenge_id
        self.start_time = datetime.now()
        self.renew_count = 0
        self.flag = flag
        self.uuid = str(uuid)
        self.port = port

    def __repr__(self):
        return "<WhaleContainer ID:{0} {1} {2} {3} {4}>".format(self.id, self.user_id, self.challenge_id,
                                                                self.start_time, self.renew_count)


class WhaleFlag(BaseFlag):
    id = "whale"
    name = "Whale Flag (Ordered)"
    templates = {
        "create": "/plugins/ctfd-whale/assets/flag-whale.html",
        "update": "/plugins/ctfd-whale/assets/flag-whale.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        return chal_key_obj.content == provided


class SequentialStaticFlag(BaseFlag):
    """
    Standard static flag behavior but with support for explicit 'order' in metadata.
    Stores metadata as JSON: {"case": "case_insensitive", "order": 1}
    Backward compatible with legacy string metadata: "case_insensitive"
    """
    id = "static"
    name = "static"
    templates = {
        "create": "/plugins/ctfd-whale/assets/flag-static-seq.html",
        "update": "/plugins/ctfd-whale/assets/flag-static-seq.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        saved = chal_key_obj.content
        data = chal_key_obj.data or ''

        # Determine case sensitivity
        is_case_insensitive = False

        # Legacy format: data == "case_insensitive"
        if data.strip() == "case_insensitive":
            is_case_insensitive = True
        else:
            # JSON format: {"case": "case_insensitive", "order": N}
            try:
                meta = json.loads(data)
                if isinstance(meta, dict) and meta.get("case") == "case_insensitive":
                    is_case_insensitive = True
            except (ValueError, TypeError):
                pass

        if is_case_insensitive:
            return saved.lower() == provided.lower()

        return saved == provided
