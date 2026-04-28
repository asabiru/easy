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


class User(Base):
    """Authenticated user. Roles: client (default), manager, admin."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    email = Column(String(256), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="client", index=True)
    full_name = Column(String(128), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # Geo captured at registration. ISO 3166-1 alpha-2.
    country = Column(String(2), nullable=True, index=True)
    sub_region = Column(String(8), nullable=True)


class KycProfile(Base):
    """Per-user KYC state. Created lazily on first /kyc/start call.

    `status` lifecycle:
        unverified  ── /kyc/start ──>  submitted
        submitted   ── webhook    ──>  pending_review | approved | rejected
        approved    (cached; user can hit /kyc/refresh to re-verify after expiry)
        rejected    (terminal; admin override required to retry)

    `level` is the Sumsub workflow level (basic-kyc-level, enhanced-dd, etc.).
    """

    __tablename__ = "kyc_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    provider = Column(String(32), nullable=False, default="mock")
    applicant_id = Column(String(64), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="unverified", index=True)
    level = Column(String(32), nullable=False, default="basic-kyc-level")
    sanctions_hit = Column(Boolean, nullable=False, default=False)
    pep_hit = Column(Boolean, nullable=False, default=False)
    sof_required = Column(Boolean, nullable=False, default=False)
    document_country = Column(String(2), nullable=True)
    reject_reasons = Column(Text, nullable=True)  # JSON array
    last_event_id = Column(String(64), nullable=True, index=True)  # idempotency
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    verdict_at = Column(DateTime, nullable=True)


class AmlEvent(Base):
    """Append-only AML / compliance audit-log.

    Every state-change touching KYC, sanctions screening, geo-block,
    deposit thresholds, or compliance overrides writes a row here.
    Read-only from the application; only the compliance-officer
    dashboard renders it. Required for FATF Recommendation 11.
    """

    __tablename__ = "aml_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # admin / compliance officer
    kind = Column(String(32), nullable=False, index=True)  # kyc_start, kyc_verdict, sanctions_hit, geo_block, override, sof_request
    detail = Column(Text, nullable=True)  # JSON serialised payload


class InvestorLead(Base):
    """Manager-tracked CRM record for HNW investor outreach.

    Created either explicitly via /investors/onboarding-intent (which also
    spawns a SupportTicket) or imported manually by a manager. Status moves
    new → contacted → qualified → onboarded → lost. Notes is append-only
    free text."""

    __tablename__ = "investor_leads"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    name = Column(String(256), nullable=False)
    email = Column(String(256), nullable=False, index=True)
    capital_band = Column(String(16), nullable=True)
    track = Column(String(32), nullable=True)
    exchange = Column(String(32), nullable=True)
    timeline = Column(String(16), nullable=True)
    message = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="new", index=True)
    assigned_manager_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    notes = Column(Text, nullable=True)


class AuditLog(Base):
    """Append-only audit trail for sensitive admin/manager actions."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True)
    target_id = Column(Integer, nullable=True)
    payload = Column(Text, nullable=True)


class AutoTradeSubscription(Base):
    """A paying client's auto-trade configuration. We store the encrypted
    API key/secret (Fernet) on this row plus risk parameters and the
    paper-vs-live status. Live trading requires `live_trading_enabled=True`
    AND the global `Settings.enable_autotrade=True`."""

    __tablename__ = "autotrade_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    email = Column(String(256), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tier = Column(String(16), nullable=False)  # manual_plus / auto_lite / auto_pro / vip

    exchange_id = Column(String(32), nullable=False)  # bybit / binance / okx / ...
    api_key_encrypted = Column(Text, nullable=True)
    api_secret_encrypted = Column(Text, nullable=True)
    api_passphrase_encrypted = Column(Text, nullable=True)  # OKX-style needs passphrase

    # Risk parameters; defaults filled from Settings on create.
    max_position_pct = Column(Float, nullable=False, default=0.10)
    daily_loss_limit_pct = Column(Float, nullable=False, default=0.05)
    min_signal_score = Column(Integer, nullable=False, default=60)
    max_fake_risk = Column(Integer, nullable=False, default=49)
    allowed_symbols = Column(Text, nullable=True)  # JSON list; null = all

    # Status: paper, live, paused, killed
    status = Column(String(16), nullable=False, default="paper", index=True)
    live_trading_enabled = Column(Boolean, nullable=False, default=False)
    paper_until = Column(DateTime, nullable=True)
    last_paused_reason = Column(Text, nullable=True)


class AutoTradeOrder(Base):
    """Per-signal execution record (paper or live)."""

    __tablename__ = "autotrade_orders"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("autotrade_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    signal_id = Column(Integer, ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    mode = Column(String(8), nullable=False)  # paper / live
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # buy / sell
    qty = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="filled")  # filled / rejected / closed
    rejected_reason = Column(Text, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    balance_before = Column(Float, nullable=True)
    balance_after = Column(Float, nullable=True)
    exchange_order_id = Column(String(64), nullable=True)


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
