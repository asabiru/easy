"""FastAPI dependencies: current_user, require_role.

Token is read from `Authorization: Bearer …` header OR the `signalx_session`
cookie (browser flow). Returns the User row, raising 401/403 as appropriate.
"""
from __future__ import annotations

import logging
from typing import Iterable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.database.models import User
from app.database.session import get_db

log = logging.getLogger(__name__)
SESSION_COOKIE = "signalx_session"


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    cookie = request.cookies.get(SESSION_COOKIE)
    return cookie or None


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        claims = decode_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
    try:
        user_id = int(claims.get("sub", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token subject")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="user not found or inactive")
    return user


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def require_role(*allowed: str):
    """Returns a dependency that asserts current user's role is in `allowed`.
    'admin' always passes. Use as `Depends(require_role('admin'))` etc."""
    allowed_set = set(allowed)

    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role == "admin" or user.role in allowed_set:
            return user
        raise HTTPException(status_code=403, detail=f"role '{user.role}' not permitted")

    return _checker
