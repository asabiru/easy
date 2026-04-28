"""Manager (sales) endpoints — investor-lead CRM."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.database.models import InvestorLead, User
from app.database.session import get_db

router = APIRouter()
_STATUSES = ("new", "contacted", "qualified", "onboarded", "lost")


class LeadUpdate(BaseModel):
    status: Literal["new", "contacted", "qualified", "onboarded", "lost"] | None = None
    note_append: str | None = Field(default=None, max_length=2000)
    assign_to_me: bool = False


@router.get("/manager/leads")
def list_leads(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
    status: str | None = None,
    mine: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = db.query(InvestorLead)
    if status and status in _STATUSES:
        q = q.filter(InvestorLead.status == status)
    if mine:
        q = q.filter(InvestorLead.assigned_manager_id == user.id)
    rows = q.order_by(InvestorLead.created_at.desc()).limit(limit).all()
    return [_serialize(r) for r in rows]


@router.get("/manager/leads/{lead_id}")
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
) -> dict[str, Any]:
    lead = db.query(InvestorLead).filter(InvestorLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return _serialize(lead)


@router.post("/manager/leads/{lead_id}")
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
) -> dict[str, Any]:
    lead = db.query(InvestorLead).filter(InvestorLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if payload.assign_to_me:
        lead.assigned_manager_id = user.id
    if payload.status:
        lead.status = payload.status
    if payload.note_append:
        prefix = f"\n[{user.email}] " if lead.notes else f"[{user.email}] "
        lead.notes = (lead.notes or "") + prefix + payload.note_append
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return _serialize(lead)


def _serialize(r: InvestorLead) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "email": r.email,
        "capital_band": r.capital_band,
        "track": r.track,
        "exchange": r.exchange,
        "timeline": r.timeline,
        "message": r.message,
        "status": r.status,
        "assigned_manager_id": r.assigned_manager_id,
        "notes": r.notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
