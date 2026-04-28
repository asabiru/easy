"""TOTP 2FA helpers.

Wraps `pyotp` with provisioning-URI generation (RFC 6238) for any
TOTP-compatible authenticator (Google Authenticator, Authy, 1Password,
Bitwarden, Aegis). The provisioning URI is rendered to a QR code on the
client side via the `qrcode` JS lib in /app — we do NOT generate a PNG
server-side (avoids shipping pillow + qrcode binary deps).

Verification uses a ±1 step window (≈30s) to absorb minor clock drift
between the user's device and our server. Replay protection is not
implemented at this layer (would require persisting last-used counters
per user); the brute-force surface is bounded by the standard auth
rate-limit and the 6-digit code's 1e-6 collision probability per try.
"""
from __future__ import annotations

import base64
import os
import secrets

import pyotp


_ISSUER = "SignalX"


def new_secret() -> str:
    """Return a fresh base32-encoded TOTP secret (160 bits = 32 chars)."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(*, email: str, secret: str) -> str:
    """Return an `otpauth://totp/...` URI to be rendered as a QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_ISSUER)


def verify(secret: str, code: str) -> bool:
    """Return True if `code` is a valid 6-digit TOTP for `secret`.

    Strips whitespace from the input (users frequently paste with spaces)
    and accepts a ±1 step (30s) window to absorb clock drift.
    """
    if not secret or not code:
        return False
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != 6:
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=1)
