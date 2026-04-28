"""Symmetric encryption for client API keys.

Uses Fernet (AES-128-CBC + HMAC) — boring, audited, fast enough.

The encryption key comes from `AUTOTRADE_ENCRYPTION_KEY` (settings). For
dev/test we fall back to a deterministic dev key so the test suite works
out of the box; production deployments MUST set the env var to a fresh
32-byte url-safe base64 value (`cryptography.fernet.Fernet.generate_key()`).
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import get_settings

log = logging.getLogger(__name__)

_DEV_KEY_SEED = "signalx-dev-encryption-key-do-not-use-in-prod"


def _resolve_key() -> bytes:
    raw = (get_settings().autotrade_encryption_key or "").strip()
    if raw:
        # Caller may pass either a raw 32-byte url-safe-b64 Fernet key
        # or any string — we accept both, hashing the latter to 32 bytes.
        try:
            Fernet(raw.encode("ascii"))
            return raw.encode("ascii")
        except (ValueError, TypeError):
            digest = hashlib.sha256(raw.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(digest)
    # Dev fallback — never use in production.
    digest = hashlib.sha256(_DEV_KEY_SEED.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return Fernet(_resolve_key()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return Fernet(_resolve_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        log.error("autotrade decrypt failed: %s", exc)
        raise
