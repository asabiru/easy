"""Signals API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.models import Signal, SignalResult
from app.database.session import get_db
from app.performance.metrics import summary

router = APIRouter()


def _signal_to_dict(s: Signal) -> dict[str, Any]:
    return {
        "id": s.id,
        "event_id": s.event_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "ticker": s.ticker,
        "symbol": s.symbol,
        "direction": s.direction,
        "action": s.action,
        "signal_score": s.signal_score,
        "entry_price": s.entry_price,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "max_holding_minutes": s.max_holding_minutes,
        "risk_level": s.risk_level,
        "reason": s.reason,
        "status": s.status,
    }


@router.get("/signals")
def list_signals(limit: int = 50, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (
        db.query(Signal)
        .order_by(Signal.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [_signal_to_dict(s) for s in rows]


@router.get("/signals/{signal_id}")
def get_signal(signal_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Signal, signal_id)
    if s is None:
        raise HTTPException(404, "signal not found")
    out = _signal_to_dict(s)
    if s.result is not None:
        out["result"] = {
            "price_after_1m": s.result.price_after_1m,
            "price_after_3m": s.result.price_after_3m,
            "price_after_5m": s.result.price_after_5m,
            "price_after_15m": s.result.price_after_15m,
            "max_favorable_move": s.result.max_favorable_move,
            "max_adverse_move": s.result.max_adverse_move,
            "result": s.result.result,
            "notes": s.result.notes,
        }
    return out


class ResultUpdate(BaseModel):
    price_after_1m: float | None = None
    price_after_3m: float | None = None
    price_after_5m: float | None = None
    price_after_15m: float | None = None
    max_favorable_move: float | None = None
    max_adverse_move: float | None = None
    result: str | None = Field(None, pattern="^(win|loss|neutral|unknown)$")
    notes: str | None = None


@router.post("/signals/{signal_id}/result/update")
def update_result(signal_id: int, payload: ResultUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Signal, signal_id)
    if s is None:
        raise HTTPException(404, "signal not found")
    if s.result is None:
        s.result = SignalResult(signal_id=s.id)
        db.add(s.result)
        db.flush()
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(s.result, k, v)
    db.commit()
    db.refresh(s.result)
    return {"signal_id": s.id, "updated": True}


@router.get("/performance/summary")
def performance_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return summary(db)
