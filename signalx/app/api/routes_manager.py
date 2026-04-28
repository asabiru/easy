"""Manager (sales) endpoints — investor-lead CRM."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.database.models import AuditLog, InvestorLead, LeadActivity, User
from app.database.session import get_db

router = APIRouter()
_STATUSES = ("new", "contacted", "qualified", "onboarded", "lost")
_ACTIVITY_KINDS = ("note", "call_logged", "email_sent", "status_change", "assignment", "score_recompute")


# Lead scoring rubric — additive, capped at 100. Calibrated against
# the investor-onboarding-intent form's enum values. Higher score = hotter
# lead. Not a probability; just a sort-order helper for the kanban.
# Capital-band scoring uses the same literals as
# `app/api/routes_investors.py` InvestorIntent.capital_band — keep these
# in sync if that enum changes.
_CAPITAL_BAND_SCORE = {
    "lt_100k": 5,
    "100k_500k": 20,
    "500k_2m": 45,
    "2m_10m": 70,
    "gt_10m": 90,
}
_TIMELINE_SCORE = {"lt_30d": 30, "1_3m": 20, "3_6m": 10, "research": 5}
_TRACK_SCORE = {
    "managed_account": 25,
    "ib_self_directed": 20,
    "prop_co_invest": 18,
    "licensing": 12,
    "other": 5,
}


def _compute_score(lead: InvestorLead) -> int:
    score = 0
    score += _CAPITAL_BAND_SCORE.get(lead.capital_band or "", 0)
    score += _TIMELINE_SCORE.get(lead.timeline or "", 0)
    score += _TRACK_SCORE.get(lead.track or "", 0)
    return min(score, 100)


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
    # Capture pre-state for the audit record so reviewers can reconstruct
    # what changed without diffing two snapshots.
    changes: dict[str, Any] = {}
    if payload.assign_to_me and lead.assigned_manager_id != user.id:
        changes["assigned_manager_id"] = {"from": lead.assigned_manager_id, "to": user.id}
        lead.assigned_manager_id = user.id
        db.add(LeadActivity(
            lead_id=lead.id, actor_id=user.id, kind="assignment",
            body=json.dumps({"to_user_id": user.id, "to_email": user.email}),
        ))
    if payload.status and payload.status != lead.status:
        changes["status"] = {"from": lead.status, "to": payload.status}
        old_status = lead.status
        lead.status = payload.status
        db.add(LeadActivity(
            lead_id=lead.id, actor_id=user.id, kind="status_change",
            body=json.dumps({"from": old_status, "to": payload.status}),
        ))
    if payload.note_append:
        prefix = f"\n[{user.email}] " if lead.notes else f"[{user.email}] "
        lead.notes = (lead.notes or "") + prefix + payload.note_append
        changes["note_appended"] = payload.note_append[:200]
        db.add(LeadActivity(
            lead_id=lead.id, actor_id=user.id, kind="note",
            body=payload.note_append[:2000],
        ))
    db.add(lead)
    if changes:
        db.add(
            AuditLog(
                actor_user_id=user.id,
                action="lead.update",
                target_type="investor_lead",
                target_id=lead.id,
                payload=json.dumps(changes),
            )
        )
    db.commit()
    db.refresh(lead)
    return _serialize(lead)


@router.get("/manager/leads/{lead_id}/activity")
def list_activity(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return the lead's activity timeline newest-first."""
    rows = (
        db.query(LeadActivity)
        .filter(LeadActivity.lead_id == lead_id)
        .order_by(LeadActivity.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "actor_id": a.actor_id,
            "kind": a.kind,
            "body": a.body,
        }
        for a in rows
    ]


class ActivityCreate(BaseModel):
    kind: Literal["note", "call_logged", "email_sent"] = "note"
    body: str = Field(..., min_length=1, max_length=2000)


@router.post("/manager/leads/{lead_id}/activity")
def add_activity(
    lead_id: int,
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
) -> dict[str, Any]:
    """Add a note / call / email-sent record to the lead's timeline.

    Status changes and assignment changes are recorded automatically by
    /manager/leads/{id} POST — this endpoint is for free-form activity
    that doesn't mutate the lead row itself.
    """
    lead = db.query(InvestorLead).filter(InvestorLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    activity = LeadActivity(
        lead_id=lead_id, actor_id=user.id, kind=payload.kind, body=payload.body,
    )
    db.add(activity)
    db.add(AuditLog(
        actor_user_id=user.id, action=f"lead.activity.{payload.kind}",
        target_type="investor_lead", target_id=lead_id,
        payload=json.dumps({"body_preview": payload.body[:200]}),
    ))
    db.commit()
    db.refresh(activity)
    return {
        "id": activity.id,
        "created_at": activity.created_at.isoformat(),
        "actor_id": activity.actor_id,
        "kind": activity.kind,
        "body": activity.body,
    }


@router.get("/manager/pipeline")
def pipeline_kanban(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("manager")),
    mine: bool = False,
) -> dict[str, Any]:
    """Return leads bucketed by status for the kanban view.

    Each bucket is sorted by lead score desc so the hottest leads sit
    at the top. Includes per-bucket totals so the UI can display
    badges (`new (12)`, `contacted (5)`...).
    """
    q = db.query(InvestorLead)
    if mine:
        q = q.filter(InvestorLead.assigned_manager_id == user.id)
    rows = q.all()
    buckets: dict[str, list[dict]] = {s: [] for s in _STATUSES}
    for r in rows:
        if r.status in buckets:
            buckets[r.status].append(_serialize(r))
    for s in _STATUSES:
        buckets[s].sort(key=lambda x: x["score"], reverse=True)
    return {
        "buckets": buckets,
        "totals": {s: len(buckets[s]) for s in _STATUSES},
    }


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
        "score": _compute_score(r),
        "assigned_manager_id": r.assigned_manager_id,
        "notes": r.notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
