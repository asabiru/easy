"""SQLAlchemy ORM models for SignalX MVP v0.1."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.session import Base


class NewsEvent(Base):
    __tablename__ = "news_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    published_at = Column(DateTime, nullable=True)
    source = Column(String(64), nullable=False, index=True)
    source_url = Column(String(1024), nullable=True)
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=False)
    text_hash = Column(String(64), nullable=False, index=True)

    company = Column(String(128), nullable=True, index=True)
    ticker = Column(String(16), nullable=True, index=True)
    event_type = Column(String(64), nullable=True, index=True)
    direction = Column(String(16), nullable=True)
    confidence = Column(Float, nullable=True)
    impact_score = Column(Float, nullable=True)
    urgency = Column(String(16), nullable=True)
    is_duplicate = Column(Boolean, default=False, nullable=False)
    fake_risk = Column(Float, default=0.0, nullable=False)

    market_snapshot = relationship(
        "MarketSnapshot", uselist=False, back_populates="event", cascade="all, delete-orphan"
    )
    signal = relationship(
        "Signal", uselist=False, back_populates="event", cascade="all, delete-orphan"
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange = Column(String(32), nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    price = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)
    volume_1m = Column(Float, nullable=True)
    volume_5m = Column(Float, nullable=True)
    price_change_1m = Column(Float, nullable=True)
    price_change_5m = Column(Float, nullable=True)
    funding_rate = Column(Float, nullable=True)
    open_interest = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("NewsEvent", back_populates="market_snapshot")


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ticker = Column(String(16), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    signal_score = Column(Float, nullable=False)
    action = Column(String(8), nullable=False, index=True)  # LONG / SHORT / WATCH / SKIP
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    max_holding_minutes = Column(Integer, nullable=True)
    risk_level = Column(String(16), nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(String(16), default="new", nullable=False)

    event = relationship("NewsEvent", back_populates="signal")
    result = relationship(
        "SignalResult", uselist=False, back_populates="signal", cascade="all, delete-orphan"
    )


class SignalResult(Base):
    __tablename__ = "signal_results"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, ForeignKey("signals.id", ondelete="CASCADE"), nullable=False, index=True)
    price_after_1m = Column(Float, nullable=True)
    price_after_3m = Column(Float, nullable=True)
    price_after_5m = Column(Float, nullable=True)
    price_after_15m = Column(Float, nullable=True)
    max_favorable_move = Column(Float, nullable=True)
    max_adverse_move = Column(Float, nullable=True)
    result = Column(String(16), nullable=True)  # win / loss / neutral / unknown
    notes = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    signal = relationship("Signal", back_populates="result")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    email = Column(String(256), nullable=True, index=True)
    category = Column(String(32), nullable=False, index=True)  # bug / billing / feature / false_positive / other
    message = Column(Text, nullable=False)
    signal_id = Column(Integer, ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(16), default="open", nullable=False, index=True)  # open / triaged / resolved
    response = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
