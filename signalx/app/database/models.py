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
    UniqueConstraint,
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
    # Referral code: deterministic from user_id (e.g. SX1A2B3C). Generated
    # lazily on first GET /referral/me call. Stored on User so we don't have
    # to JOIN on every signup attempt to look up which referrer a code belongs to.
    referral_code = Column(String(16), nullable=True, unique=True, index=True)
    # TOTP 2FA: secret stored only after the user completes /auth/2fa/verify.
    # Until totp_enabled=True, /auth/2fa/setup may rotate the secret freely.
    # Mandatory for VIP and Auto-Pro tiers before /autotrade/{id}/go-live —
    # see app/api/routes_autotrade.py:go_live for the gate.
    totp_secret = Column(String(64), nullable=True)
    totp_enabled = Column(Boolean, nullable=False, default=False)
    # Risk acknowledgement: monotonically-increasing version that the user
    # has accepted. Bump RISK_ACK_VERSION in app/compliance/risk_ack.py
    # when the disclosures page changes materially — clients are then
    # re-prompted before the next state-mutating compliance gate.
    risk_ack_version = Column(Integer, nullable=False, default=0)
    risk_ack_at = Column(DateTime, nullable=True)


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


class Payment(Base):
    """User payment record. Currently TON / Wallet Pay only.

    `purpose` ∈ {subscription, vault_deposit, other}. Subscription
    payments fund the SaaS billing; vault_deposit credits flow through
    the on-chain vault contract (added in phase-2). All payments
    require the user's KYC profile to be `approved` before invoice
    creation (enforced at endpoint level via `require_kyc()`).

    `status` lifecycle: pending → paid | failed.
    """

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False, default="ton_wallet_pay")
    external_id = Column(String(64), nullable=False, unique=True, index=True)
    amount_usdt = Column(Float, nullable=False)
    purpose = Column(String(32), nullable=False, default="subscription")
    status = Column(String(16), nullable=False, default="pending", index=True)
    paid_at = Column(DateTime, nullable=True)


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


class LeadActivity(Base):
    """Per-lead activity timeline (notes, calls, emails, status changes,
    assignment changes). Manager CRM uses this to render the right-rail
    timeline on `/manager.html`. Append-only.

    `kind` ∈ {note, call_logged, email_sent, status_change, assignment, score_recompute}
    """

    __tablename__ = "lead_activities"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("investor_leads.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind = Column(String(32), nullable=False, index=True)
    body = Column(Text, nullable=True)


class Referral(Base):
    """Tracks referrer→referee relationships and accrued earnings.

    `status` lifecycle:
      pending      → referee signed up but no first paid sub yet
      qualified    → referee converted to paid sub (first_paid_at populated)
      revoked      → user blocked / refund / fraud → not paid out

    `earnings_usdt` is the total accrued (lifetime) referral commission. The
    actual payout flow runs via `app/payments/*` once the user requests it.
    Default: 20% of referee's gross revenue for first 12 months.
    """

    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    referee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True, unique=True)
    code = Column(String(16), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    first_paid_at = Column(DateTime, nullable=True)
    earnings_usdt = Column(Float, nullable=False, default=0.0)


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


class LeadCapture(Base):
    """Top-of-funnel email capture from the public landing page.

    Distinct from `InvestorLead` (HNW / institutional pipeline) and
    `User` (registered authenticated accounts). LeadCapture is purely
    a marketing list: just an email + the ref/utm context. No PII
    beyond what the visitor types in voluntarily.

    The Marketing agent broadcasts to this list via /admin/announce
    (when BROADCAST_ENABLED=true). Unsubscribe is implicit — we drop
    rows on /leads/unsubscribe and never re-add."""

    __tablename__ = "lead_captures"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    email = Column(String(256), nullable=False, unique=True, index=True)
    referral_code = Column(String(16), nullable=True, index=True)
    utm_source = Column(String(64), nullable=True)
    utm_medium = Column(String(64), nullable=True)
    utm_campaign = Column(String(64), nullable=True)
    # Was this email confirmed via double-opt-in? Default False until the
    # user clicks the confirmation link sent by the broadcast worker.
    confirmed = Column(Boolean, nullable=False, default=False)


# ─────────────────────── Custody / managed-pool models ─────────────────────── #
#
# These power the Phase-2 pivot: SignalX accepts USDT from clients, holds it
# in a single treasury, and trades the aggregated pool. Each client owns
# `shares` of the pool whose USDT-denominated price (`share_price`) is
# recomputed at every NavSnapshot. Profits and losses propagate
# proportionally across all share-holders. Performance fees are accrued at
# HWM on each NavSnapshot.
#
# IMPORTANT — regulatory posture: Until SignalX holds an investment-management
# / VASP / collective-investment-scheme licence in the operating jurisdiction,
# the public deposit endpoint MUST be gated behind
# `CUSTODY_LIVE_DEPOSITS_ENABLED=true`. The code-level gate lives in
# `routes_wallet.py`. See `docs/legal/disclosures.md` and the discussion in
# PR #15 for the explicit risk-acknowledgement text every client must accept
# at the moment of first deposit.


class ClientWallet(Base):
    """Per-client position in the managed pool.

    `balance_usdt` mirrors the most-recent USDT-equivalent of `shares` at
    the latest NAV (denormalized for fast reads on /wallet/me); the
    authoritative ownership unit is `shares`. `hwm_share_price` is the
    high-water mark used to gate performance-fee accrual."""

    __tablename__ = "client_wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    shares = Column(Float, default=0.0, nullable=False)
    balance_usdt = Column(Float, default=0.0, nullable=False)
    # HWM is share-price at last fee accrual. New fees only accrue on the
    # delta above HWM, never twice for the same gain.
    hwm_share_price = Column(Float, default=1.0, nullable=False)
    last_fee_at = Column(DateTime, nullable=True)
    # Total deposits / withdrawals for the lifetime of this wallet — used
    # by the /wallet/me equity → P&L breakdown on the client UI.
    lifetime_deposit_usdt = Column(Float, default=0.0, nullable=False)
    lifetime_withdraw_usdt = Column(Float, default=0.0, nullable=False)


class DepositAddress(Base):
    """Chain-specific deposit address allocated to a single client.

    For MVP we use one shared treasury address per chain with the user's
    wallet id as the on-chain memo / tag (Tron, TON, Solana support memos;
    EVM uses a per-user counterfactual address derived from a master
    HD-key). The `external_address` field is what we present to the
    client; `derivation_path` documents how it's derived for ops.

    `expires_at` lets us rotate addresses if the chain integration
    changes. `used` flips True after first observed inbound TX."""

    __tablename__ = "deposit_addresses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chain = Column(String(16), nullable=False, index=True)  # trc20 / erc20 / ton / sol / bsc
    external_address = Column(String(128), nullable=False)
    memo = Column(String(64), nullable=True)  # for chains that need user-routing memo
    derivation_path = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    used = Column(Boolean, default=False, nullable=False)


class Deposit(Base):
    """Inbound USDT deposit observed on-chain and credited to a wallet.

    Idempotent on `(chain, tx_hash)`. `credited` flips True only after
    the wallet's `shares` is updated and the audit log entry is written
    in the same transaction; the credited path is the single source of
    truth for share-issuance accounting.

    Concurrency note
    ----------------
    `record_inbound_deposit()` does a check-then-insert for idempotency.
    Without a DB-level uniqueness guarantee on `(chain, tx_hash)`, two
    concurrent webhook deliveries for the same on-chain TX could both
    pass the existence check and double-credit shares (TOCTOU race).
    The composite UniqueConstraint below makes the second insert raise
    IntegrityError, which the caller catches and converts to an
    idempotent return."""

    __tablename__ = "custody_deposits"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", name="uq_custody_deposits_chain_tx_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    chain = Column(String(16), nullable=False, index=True)
    tx_hash = Column(String(128), nullable=False, index=True)
    amount_usdt = Column(Float, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    credited = Column(Boolean, default=False, nullable=False, index=True)
    credited_at = Column(DateTime, nullable=True)
    share_price_at_credit = Column(Float, nullable=True)
    shares_credited = Column(Float, nullable=True)


class Withdrawal(Base):
    """Outbound USDT withdrawal request from a client.

    Lifecycle: queued → approved → sent (or → cancelled). Manual two-step
    operator approval in MVP — automation comes once we have multi-sig
    treasury + automated NAV reconciliation.

    Uniqueness note
    ---------------
    `(chain, tx_hash)` is unique when tx_hash is not null — i.e. two
    different withdrawal rows cannot record the same on-chain TX as
    `sent`. This prevents an operator from accidentally tagging the
    wrong row when reconciling on-chain transactions. Multiple NULL
    tx_hash rows (queued / approved / cancelled) are still allowed
    because SQL treats NULLs as distinct in unique constraints."""

    __tablename__ = "custody_withdrawals"
    __table_args__ = (
        UniqueConstraint(
            "chain", "tx_hash",
            name="uq_custody_withdrawals_chain_tx_hash",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    chain = Column(String(16), nullable=False, index=True)
    destination_address = Column(String(128), nullable=False)
    amount_usdt = Column(Float, nullable=False)
    shares_burned = Column(Float, nullable=False)
    share_price_at_request = Column(Float, nullable=False)
    status = Column(String(16), default="queued", nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    tx_hash = Column(String(128), nullable=True)
    cancelled_reason = Column(Text, nullable=True)


class NavSnapshot(Base):
    """Periodic snapshot of the pool's NAV.

    `share_price = total_aum_usdt / total_shares` (capped at minimum 1e-6
    to avoid div-by-zero). Snapshots are append-only — never mutated.
    Used for: (a) deposit share-issuance pricing, (b) withdrawal pricing,
    (c) performance-fee HWM reference, (d) UI charts."""

    __tablename__ = "custody_nav_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    total_aum_usdt = Column(Float, nullable=False)
    total_shares = Column(Float, nullable=False)
    share_price = Column(Float, nullable=False)
    # Free-form note set by ops at manual snapshot time, e.g.
    # "after-fee accrual" / "BTC dump -8% reconciliation".
    note = Column(String(256), nullable=True)


class PerformanceFee(Base):
    """Accrued performance / management fee event.

    `kind="performance"`: 20% of share-price gain above HWM × user shares.
    `kind="management"`: 2%/year (daily-pro-rata) of user_balance.

    Both are accrued by minting `fee_shares` to the treasury wallet and
    burning the same number of shares from the client. This way pool TVL
    stays constant; only the share-of-pool reallocates."""

    __tablename__ = "custody_performance_fees"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    kind = Column(String(16), nullable=False)  # "performance" | "management"
    hwm_before = Column(Float, nullable=True)
    hwm_after = Column(Float, nullable=True)
    share_price = Column(Float, nullable=False)
    fee_shares = Column(Float, nullable=False)
    fee_usdt_equiv = Column(Float, nullable=False)
