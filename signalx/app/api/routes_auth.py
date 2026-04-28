"""User auth endpoints — registration, login, logout, /me."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import SESSION_COOKIE, get_current_user
from app.auth.security import hash_password, issue_token, verify_password
from app.config.settings import get_settings
from app.database.models import User
from app.database.session import get_db

router = APIRouter()


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = None
    role: Literal["client"] = "client"  # always start as client; only admin can elevate


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _set_cookie(response: Response, token: str, ttl: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        # HTTPS-only in staging/prod. We terminate TLS at the Fly.io
        # edge and the upstream is plain HTTP, so the cookie must
        # still be `secure=True` — the browser only sees the HTTPS
        # connection. `dev` is the only non-HTTPS environment we run.
        secure=get_settings().app_env != "dev",
    )


@router.post("/auth/register")
def register(payload: RegisterIn, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    existing = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    role = "client"
    if s.bootstrap_admin_email and str(payload.email).lower() == s.bootstrap_admin_email.lower():
        role = "admin"
    user = User(
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = issue_token(user.id, user.role, ttl_sec=s.jwt_session_ttl_sec)
    _set_cookie(response, token, s.jwt_session_ttl_sec)
    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "token": token,
    }


@router.post("/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="user is disabled")
    token = issue_token(user.id, user.role, ttl_sec=s.jwt_session_ttl_sec)
    _set_cookie(response, token, s.jwt_session_ttl_sec)
    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "token": token,
    }


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged_out"}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name,
        "is_active": user.is_active,
    }
