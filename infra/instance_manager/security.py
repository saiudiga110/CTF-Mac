"""Simple HMAC authentication helpers for service-to-service APIs."""

from __future__ import annotations

import hashlib
import hmac
import time


def sign_body(secret: str, body: bytes, timestamp: str | None = None) -> tuple[str, str]:
    ts = timestamp or str(int(time.time()))
    msg = ts.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return ts, digest


def verify_signature(secret: str, body: bytes, timestamp: str, signature: str, max_age_seconds: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > max_age_seconds:
        return False
    _, expected = sign_body(secret, body, timestamp=str(ts))
    return hmac.compare_digest(expected, signature or "")

