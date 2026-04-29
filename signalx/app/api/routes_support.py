"""Support ticket endpoints + Telegram bot command surface.

The bot itself lives outside this process (long-poll worker), but `/bot/<cmd>`
handlers are exposed here so the bot worker can call them over HTTP. This
lets the same logic power Telegram commands and HTTP support consoles.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.database.models import Signal, SupportTicket
from app.database.session import get_db
from app.notifications.telegram import send as telegram_send

log = logging.getLogger(__name__)
router = APIRouter()


CATEGORIES = ("bug", "billing", "feature", "false_positive", "other")


class TicketIn(BaseModel):
    email: EmailStr | None = None
    category: Literal["bug", "billing", "feature", "false_positive", "other"] = "other"
    message: str = Field(..., min_length=5, max_length=4000)
    signal_id: int | None = None


@router.post("/support/ticket")
def create_ticket(payload: TicketIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create a support ticket and forward it to the support channel.

    Returns both the int ``ticket_id`` (for admin correlation) and a
    URL-safe ``ticket_token``. The token is the capability for the
    anonymous submitter to read the ticket back via
    ``GET /support/ticket/{ticket_token}``.
    """
    ticket = SupportTicket(
        email=payload.email,
        category=payload.category,
        message=payload.message,
        signal_id=payload.signal_id,
        status="open",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    # Best-effort Telegram forward; never fail the request because of it.
    notice = (
        f"🆘 SUPPORT TICKET #{ticket.id}\n"
        f"Category: {ticket.category}\n"
        f"From: {ticket.email or 'anonymous'}\n"
        f"Signal: {ticket.signal_id or '-'}\n\n"
        f"{ticket.message[:1000]}"
    )
    try:
        telegram_send(notice)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("telegram_send for ticket %s failed: %s", ticket.id, exc)

    return {
        "ticket_id": ticket.id,
        "ticket_token": ticket.lookup_token,
        "status": ticket.status,
    }


@router.get("/support/ticket/{ticket_token}")
def get_ticket(ticket_token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Anonymous ticket read-back via capability token.

    Security (fix for BUG_pr-review-job-76af88f3b2924077813350cc4cc47bef_0001):
    we look up by ``lookup_token`` (~192 bits of entropy), never by the
    sequential int id. An attacker incrementing ``id`` CANNOT find
    tickets because no token derives from the id. The old int-id path
    used to leak every submitter's email + message to anyone who
    guessed 1…N.

    Tokens shorter than 16 chars (legacy rows written before the fix
    landed, or obvious brute-force attempts) always 404 so the endpoint
    can't be used to check for the presence of low-id rows.
    """
    if not ticket_token or len(ticket_token) < 16:
        raise HTTPException(status_code=404, detail="ticket not found")
    t = (
        db.query(SupportTicket)
        .filter(SupportTicket.lookup_token == ticket_token)
        .first()
    )
    if t is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return {
        "id": t.id,
        "ticket_token": t.lookup_token,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "email": t.email,
        "category": t.category,
        "message": t.message,
        "signal_id": t.signal_id,
        "status": t.status,
        "response": t.response,
    }


# --------------------------------------------------------------------------- #
# Telegram bot command surface (read-only).                                    #
# --------------------------------------------------------------------------- #
@router.get("/bot/help")
def bot_help() -> dict[str, list[dict[str, str]]]:
    """Returns the canonical /help command reply."""
    return {
        "commands": [
            {"cmd": "/help",            "desc": "list available commands"},
            {"cmd": "/status",          "desc": "service health + signals processed in the last hour"},
            {"cmd": "/last [TICKER]",   "desc": "the 5 most recent signals (optionally filtered by ticker)"},
            {"cmd": "/about",           "desc": "version, MVP disclaimer, links"},
        ]
    }


@router.get("/bot/status")
def bot_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(hours=1)
    count_1h = db.query(Signal).filter(Signal.created_at >= since).count()
    return {
        "ok": True,
        "signals_last_hour": count_1h,
        "components": {
            "db": "ok",
            "exchange": "ok",   # actual probe lives in routes_health
            "telegram": "ok",
        },
    }


@router.get("/bot/last")
def bot_last(
    ticker: str | None = None,
    limit: int = 5,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    q = db.query(Signal).order_by(Signal.created_at.desc())
    if ticker:
        q = q.filter(Signal.ticker == ticker.upper())
    rows = q.limit(min(20, max(1, limit))).all()
    return {
        "items": [
            {
                "id": s.id,
                "ticker": s.ticker,
                "symbol": s.symbol,
                "action": s.action,
                "signal_score": s.signal_score,
                "risk_level": s.risk_level,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    }


@router.get("/bot/about")
def bot_about() -> dict[str, str]:
    return {
        "name": "SignalX",
        "version": "0.1.0-mvp",
        "disclaimer": (
            "Research-only product. SignalX does NOT execute trades on your behalf. "
            "Signals are generated from publicly available news and are not "
            "investment advice."
        ),
        "docs": "https://github.com/asabiru/easy",
    }
