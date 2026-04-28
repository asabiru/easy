"""Password hashing (bcrypt) + JWT token issuance/verification.

JWT secret comes from settings.jwt_secret; in dev we fall back to a
deterministic-but-warning-tagged value. Tokens carry: sub (user id), role,
iat, exp.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config.settings import get_settings

log = logging.getLogger(__name__)

JWT_ALGO = "HS256"
DEFAULT_TTL_SEC = 60 * 60 * 12  # 12h


def _resolve_secret() -> str:
    s = (get_settings().jwt_secret or "").strip()
    if not s:
        return "signalx-dev-jwt-do-not-use-in-prod"
    return s


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def issue_token(user_id: int, role: str, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + ttl_sec,
    }
    return jwt.encode(claims, _resolve_secret(), algorithm=JWT_ALGO)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, _resolve_secret(), algorithms=[JWT_ALGO])
