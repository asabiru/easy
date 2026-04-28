"""Investor onboarding intent — Track A / HNW.

Captures interest from family-offices, prop firms, HNW individuals who want
to invest **through** their own exchange account using SignalX's signals
(IB / capital-introduction model — no custody on our side). Stored as a
SupportTicket with `category="investor_onboarding"` so the existing inbox
serves both flows; optionally forwarded to Telegram if configured.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.models import InvestorLead, SupportTicket
from app.database.session import get_db
from app.notifications.telegram import send as telegram_send

router = APIRouter()
log = logging.getLogger(__name__)


class InvestorIntentIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    email: EmailStr
    capital_band: Literal["lt_100k", "100k_500k", "500k_2m", "2m_10m", "gt_10m"]
    track: Literal["ib_self_directed", "managed_account", "prop_co_invest", "licensing", "other"] = "ib_self_directed"
    exchange: str | None = None
    timeline: Literal["lt_30d", "1_3m", "3_6m", "research"] = "lt_30d"
    message: str | None = Field(default=None, max_length=2000)


@router.post("/investors/onboarding-intent")
def onboarding_intent(payload: InvestorIntentIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    body = (
        f"track={payload.track} capital={payload.capital_band} exchange={payload.exchange or '-'}"
        f" timeline={payload.timeline}\n\n{payload.message or ''}"
    )
    ticket = SupportTicket(
        email=str(payload.email),
        category="investor_onboarding",
        message=f"[{payload.name}] {body}",
    )
    db.add(ticket)
    lead = InvestorLead(
        name=payload.name,
        email=str(payload.email),
        capital_band=payload.capital_band,
        track=payload.track,
        exchange=payload.exchange,
        timeline=payload.timeline,
        message=payload.message,
        status="new",
    )
    db.add(lead)
    db.commit()
    db.refresh(ticket)
    db.refresh(lead)

    s = get_settings()
    if s.telegram_enabled:
        try:
            telegram_send(
                "💼 New investor intent\n"
                f"Name: {payload.name}\nEmail: {payload.email}\n"
                f"Track: {payload.track}\nCapital: {payload.capital_band}\n"
                f"Timeline: {payload.timeline}\nExchange: {payload.exchange or '-'}\n\n"
                f"{payload.message or ''}"
            )
        except Exception as exc:  # pragma: no cover
            log.warning("telegram_send (investor) raised: %s", exc)

    return {"ticket_id": ticket.id, "lead_id": lead.id, "status": "received"}
