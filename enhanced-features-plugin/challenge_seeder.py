"""
Lloyds CTF — Challenge Seeder
Keeps the 17 canonical vBank challenge cards from challenge-catalog.json.
Flags use vbank_dynamic type — validated against HMAC values from the vBank app.
"""

import logging

try:
    from .challenge_catalog import challenges as catalog_challenges
except ImportError:
    from challenge_catalog import challenges as catalog_challenges

logger = logging.getLogger(__name__)

CHALLENGES = [
    {
        "name": row["name"],
        "category": row["category"],
        "value": row["value"],
        "flag_key": row["flag_key"],
        "description": row.get("description") or row.get("description_md") or "",
        "hints": [(h["text"], h["cost"]) for h in row.get("hints", [])],
    }
    for row in catalog_challenges()
]


def seed_challenges(app):
    """
    Idempotent: create/update the 17 canonical vBank challenges.
    Purges every other challenge (and related solves/hints/flags).
    """
    with app.app_context():
        try:
            from CTFd.models import Challenges, Flags, Hints, Solves, db

            canonical_names = {meta["name"] for meta in CHALLENGES}
            created = 0
            updated = 0

            all_chals = Challenges.query.all()
            purged = 0
            for c in all_chals:
                if c.name not in canonical_names:
                    Solves.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
                    Hints.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
                    Flags.query.filter_by(challenge_id=c.id).delete(synchronize_session=False)
                    db.session.delete(c)
                    purged += 1
            if purged:
                db.session.commit()
                logger.info(f"[ChallengeSeeder] Purged {purged} non-canonical challenge(s)")

            for pos, meta in enumerate(CHALLENGES, start=1):
                existing = Challenges.query.filter_by(name=meta["name"]).first()

                if existing:
                    changed = False
                    for attr in ("category", "value", "description"):
                        if getattr(existing, attr) != meta[attr]:
                            setattr(existing, attr, meta[attr])
                            changed = True
                    try:
                        if getattr(existing, "position", None) != pos:
                            existing.position = pos
                            changed = True
                    except Exception:
                        pass
                    if existing.state != "visible":
                        existing.state = "visible"
                        changed = True
                    if changed:
                        db.session.commit()
                        updated += 1
                    chal = existing
                else:
                    kwargs = dict(
                        name=meta["name"],
                        category=meta["category"],
                        description=meta["description"],
                        value=meta["value"],
                        type="standard",
                        state="visible",
                    )
                    try:
                        kwargs["position"] = pos
                    except Exception:
                        pass
                    chal = Challenges(**kwargs)
                    db.session.add(chal)
                    db.session.flush()
                    created += 1

                existing_flags = Flags.query.filter_by(challenge_id=chal.id).all()
                has_correct = any(
                    f.type == "vbank_dynamic" and f.content == meta["flag_key"]
                    for f in existing_flags
                )
                if not has_correct:
                    for f in existing_flags:
                        db.session.delete(f)
                    db.session.flush()
                    db.session.add(Flags(
                        challenge_id=chal.id,
                        type="vbank_dynamic",
                        content=meta["flag_key"],
                        data="",
                    ))

                if Hints.query.filter_by(challenge_id=chal.id).count() == 0:
                    for hint_text, cost in meta.get("hints", []):
                        db.session.add(Hints(
                            challenge_id=chal.id,
                            content=hint_text,
                            cost=cost,
                        ))

            db.session.commit()
            logger.info(
                f"[ChallengeSeeder] Done — {created} created, {updated} updated, "
                f"{len(CHALLENGES)} canonical challenges"
            )
        except Exception:
            logger.exception("[ChallengeSeeder] Failed")
