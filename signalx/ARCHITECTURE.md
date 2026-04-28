# SignalX — Architecture (MVP v0.1)

## Goal
Event-driven, *short-horizon* trading signals for tokenized stock perpetual
futures (NVDAUSDT, TSLAUSDT, …) based on real-time news. **Not** for long-term
investing. **Not** auto-trading in v0.1.

## Pipeline

```
News (POST/RSS/TG/mock)
        │
        ▼
┌───────────────────┐     ┌─────────────────┐
│ News Normalizer   │────▶│ Deduplicator    │
└───────────────────┘     └─────────────────┘
        │                         │
        ▼                         ▼
┌───────────────────┐     ┌─────────────────┐
│ Company Mapper    │────▶│ Event Classifier│
└───────────────────┘     └─────────────────┘
                                  │
                                  ▼
                          ┌─────────────────┐
                          │ Sentiment +     │
                          │ Impact Score    │
                          └─────────────────┘
                                  │
                                  ▼
                          ┌─────────────────┐
                          │ Market Checker  │  ← ccxt / mock
                          └─────────────────┘
                                  │
                                  ▼
                          ┌─────────────────┐
                          │ Risk Engine     │
                          └─────────────────┘
                                  │
                                  ▼
                          ┌─────────────────┐
                          │ Signal Engine   │
                          └─────────────────┘
                                  │
              ┌───────────────────┼─────────────────────┐
              ▼                   ▼                     ▼
     ┌─────────────────┐ ┌─────────────────┐  ┌──────────────────┐
     │ Telegram alert  │ │ Postgres        │  │ Performance      │
     └─────────────────┘ │ (events,        │  │ Tracker          │
                         │  snapshots,     │  │ (Celery beat:    │
                         │  signals,       │  │  1m/3m/5m/15m)   │
                         │  results)       │  └──────────────────┘
                         └─────────────────┘
```

## Modules

| Layer        | Module                                | Responsibility                                                  |
|--------------|---------------------------------------|-----------------------------------------------------------------|
| API          | `app/api/routes_*.py`                 | FastAPI HTTP endpoints                                          |
| News         | `app/news/`                           | normalize / dedup / source reliability / collectors             |
| Companies    | `app/companies/`                      | universe + keyword mapper                                       |
| Analysis     | `app/analysis/`                       | rule classifier / sentiment refinement / impact_score           |
| Market       | `app/market/`                         | ccxt exchange client (with mock fallback) + spread/liquidity    |
| Signals      | `app/signals/`                        | risk engine / signal engine / Telegram formatter                |
| Notifications| `app/notifications/telegram.py`       | best-effort Telegram delivery                                   |
| Database     | `app/database/`                       | SQLAlchemy models + session                                     |
| Performance  | `app/performance/`                    | Celery beat sweep + win/loss metrics                            |
| Config       | `app/config/settings.py`              | pydantic-settings, .env-driven                                  |

## Data flow contracts

`app.news.normalizer.NormalizedNews`
```json
{
  "source": "...", "source_url": "...",
  "raw_text": "...", "normalized_text": "...",
  "received_at": "...", "published_at": "..."
}
```

`app.signals.signal_engine.SignalDecision`
```json
{
  "action": "LONG|SHORT|WATCH|SKIP",
  "ticker": "NVDA", "symbol": "NVDAUSDT",
  "direction": "bullish|bearish|neutral|unclear",
  "impact_score": 0-100, "confidence": 0-100,
  "signal_score": 0-100, "risk_level": "low|medium|high",
  "reason": "...", "max_holding_minutes": 15,
  "entry_price": 0, "stop_loss": 0, "take_profit": 0
}
```

## Scoring

```
final_score = impact_score
            + 0.2 × source_reliability        (0..20)
            + market_confirmation_score       (-5..+10)
            - spread_risk
            - liquidity_risk
            - late_entry_risk
            - fake_news_risk
```

| score   | bucket          |
|---------|-----------------|
| 0–39    | SKIP            |
| 40–59   | WATCH           |
| 60–74   | weak signal     |
| 75–89   | strong signal   |
| 90–100  | very strong     |

If risk engine emits `SKIP`/`WATCH` override, the bucket is overridden too.

## Persistence

- `news_events` — every received news item, linked to its analysis.
- `market_snapshots` — 1:1 with news_events when a company matched.
- `signals` — 1:1 with news_events when an action was decided.
- `signal_results` — 1:1 with signals; filled by Celery beat at +1/3/5/15m.

## Safety

- **No live trading.** `enable_autotrade` defaults to `false`; v0.1 has no
  order-execution code.
- Secrets only via `.env`; `.env.example` is committed.
- Errors in collectors / exchange / Telegram are caught and logged — they do
  not break the pipeline.

## CRM, Compliance & Security layers (added post-v0.1 round 1–2)

| Layer            | Module                                | Responsibility                                                                |
|------------------|---------------------------------------|-------------------------------------------------------------------------------|
| Auth             | `app/auth/`                           | bcrypt+JWT issuance, role middleware, `require_role`/`get_current_user`       |
| Auth-2FA         | `app/security/totp.py` + `routes_2fa` | TOTP enrolment & verification (mandatory for VIP/Auto-Pro live trading)       |
| KYC              | `app/kyc/`                            | provider abstraction (Mock + Sumsub HMAC), `KycProfile`, `AmlEvent` audit log |
| Compliance       | `app/compliance/risk_ack.py`          | risk-disclosure version gate (412 until accepted)                             |
| Payments         | `app/payments/ton.py`                 | Wallet Pay invoice + idempotent HMAC webhook                                  |
| CRM-Manager      | `routes_manager` + `LeadActivity`     | sales pipeline, kanban, activity timeline                                     |
| CRM-Client       | `routes_client` + `routes_referral`   | self-serve subs, referral leaderboard                                         |
| CRM-Admin        | `routes_admin`                        | revenue, signal-quality, compliance, referral metrics                         |
| Anti-Fake        | `app/analysis/fake_risk.py` + `/signals/filter-stats` | per-tier visibility cap, transparency rail        |
| Trading-Risk     | `app/autotrade/risk_guard.py`         | daily-loss kill switch, win-rate alert                                        |
| Marketing/Growth | `routes_leads` + `LeadCapture`        | top-of-funnel email capture with utm + ref attribution                        |
| Rate-limit       | `app/security/rate_limit.py`          | sliding-window per-IP guards on referral/test-keys                            |

## State-mutating endpoint gates (in order)

For any caller hitting `/autotrade/subscribe` or `/autotrade/{id}/go-live`:

1. `Depends(get_current_user)` — every state-mutating endpoint authenticates
   (CLAUDE.md Rule 3). No `_optional` variants on writes.
2. `require_risk_ack(user)` — 412 unless user has accepted current
   `RISK_ACK_VERSION` (admin/manager bypass).
3. KYC (when `KYC_REQUIRED=true`) — 403 unless `KycProfile.status="approved"`
   and no sanctions hit.
4. 2FA (VIP / Auto-Pro only on go-live) — 403 unless `User.totp_enabled=True`.
5. Global kill switch — 409 if `ENABLE_AUTOTRADE=false`.
6. Subscription state guards — 409 if killed/paused/in paper window.
7. Per-symbol & risk caps — risk-engine gates inside the executor.

## Custody / managed-pool flow (Mode B)

Added in custody round 1–3 commits (`31fec1b`, `c727399`, `8160d32`).
This is the optional fund-style mode where SignalX accepts USDT
deposits, pools them under one treasury, and issues fractional shares
priced at NAV. Mode B is **off by default** — see the gate stack
below before deploying.

### Module map

| Module                                      | Responsibility                                                                |
|--------------------------------------------|-------------------------------------------------------------------------------|
| `app/custody/shares.py`                    | Pure share-math: `issue_shares`, `burn_shares`, `share_price_from_aum`, `performance_fee_shares` |
| `app/custody/addresses.py`                 | Per-user / per-chain deposit address allocation (deterministic mock; wired for HD-wallet derivation later) |
| `app/custody/webhooks.py`                  | Five chain-specific parsers (TronGrid / Alchemy / Helius / BSCscan / TON) + shared idempotent `record_inbound_deposit()` + sentinel-routing for unmatched deposits |
| `app/api/routes_wallet.py`                 | `/wallet/me`, `/wallet/deposit-address`, `/wallet/deposits`, `/wallet/withdraw`, `/wallet/withdrawals` — client-facing |
| `app/api/routes_treasury.py`               | `/admin/treasury/*` — pool overview, withdrawal queue (approve/send/cancel), NAV snapshot, manual credit, **pending deposits queue, deposit reattribute, custody audit log** |
| `app/api/routes_payments_webhooks.py`      | `POST /payments/{trongrid,alchemy,helius,bsc,ton-pool}/webhook` — provider-facing |

### Gate stack (custody endpoints, in order)

For any caller hitting `/wallet/deposit-address` or `/wallet/withdraw`:

1. **Master toggle** — `CUSTODY_LIVE_DEPOSITS_ENABLED=true`. Default
   off; deposit endpoint returns 503 with explicit message until
   operator opts in.
2. **Licence attestation** — either both `CUSTODY_LICENSE_JURISDICTION`
   and `CUSTODY_LICENSE_NUMBER` set, OR `CUSTODY_SELF_ATTEST_OVERRIDE=true`
   (operator accepts unlicensed-operation risk). Otherwise 503.
3. `Depends(get_current_user)` — write endpoints always authenticate.
4. `require_risk_ack(user)` — `RISK_ACK_VERSION=2` (custody-aware
   disclosures). Clients on v1 see 412 until they re-accept.
5. KYC — fund-flow endpoints are KYC-gated regardless of the global
   `KYC_REQUIRED` toggle. 403 until `KycProfile.status="approved"`
   and no sanctions hit.
6. **Per-chain webhook secret** (webhook routes only) — chain
   returns 503 until its own `CUSTODY_*_WEBHOOK_SECRET` is set, even
   if the master toggle is on. Forces an explicit per-chain go-live
   decision.
7. Withdrawal cooldown — 24h post-deposit window blocks deposit→
   withdraw round-trips (anti-flush guard).

### Inbound deposit lifecycle

```
chain provider POST /payments/<chain>/webhook
   │
   ▼
verify HMAC / Bearer signature  ─── 401 on mismatch
   │
   ▼
parse_<chain>(payload) → InboundDeposit
   │
   ▼
record_inbound_deposit(db, dep)
   │   ├── existing (chain, tx_hash) row? → return idempotently, no double-credit
   │   ├── resolve user via memo or address match
   │   ├── if matched + confirmed + ≥ chain min:
   │   │       issue shares using current NAV, write audit row
   │   ├── if matched but below-min: row stored, credited=False (operator review)
   │   └── if unmatched: anchor to unassigned@signalx.internal sentinel
   │                     (operator manually reattributes via
   │                     POST /admin/treasury/deposits/{id}/reattribute)
   ▼
return 200 to provider with deposit_id + credited flag
```

All webhook handlers always return 200 once signature passes — provider
retry logic is happy, and operators clear orphans / below-min through
the audit-log + reattribute flow.

### Performance + management fees

NAV snapshot (`POST /admin/treasury/nav/snapshot`) recomputes
`share_price = AUM / total_shares`. For each client wallet whose share
price exceeds its high-water mark, `performance_fee_shares()` transfers
20% of the gain (in shares) from the client to the
`treasury@signalx.internal` internal user — never burned, so
`sum(shares)` is invariant. `PerformanceFee` rows record `hwm_before`
and `hwm_after` for audit. Management fee (2% of equity per year,
daily-pro-rata) accrues on the same path.

### Operator runbook

| Scenario                                     | Action                                                                  |
|---------------------------------------------|-------------------------------------------------------------------------|
| Webhook stuck retrying (4xx in our logs)     | Check `CUSTODY_LIVE_DEPOSITS_ENABLED` + per-chain secret env vars       |
| Client says "I deposited but no balance"     | `/admin/treasury/deposits/pending` → if listed, reattribute or wait for confirm |
| Address mismatch on a real deposit           | `/admin/treasury/deposits/{id}/reattribute` with note ≥ 4 chars         |
| Suspicious withdrawal in queue               | `/admin/treasury/withdrawals/{id}/cancel` with reason; shares re-credit |
| Daily AUM update                             | `/admin/treasury/nav/snapshot` with `aum_usdt` after exchange + on-chain reconcile |
| Audit query on a specific user               | `/admin/treasury/audit-log?kind=custody_deposit_credited&limit=100`     |
| Suspected secret leak                        | Rotate `CUSTODY_<chain>_WEBHOOK_SECRET`; no client impact (we never expose it client-side) |

## Roadmap (post-MVP)

1. Real-time collectors (RSS scheduler, Telegram channel reader).
2. Replace rule classifier with a small LLM classifier; upgrade sentiment.
3. Live OHLCV-based price_change_1m/5m via `fetch_ohlcv`.
4. Optional autotrader module behind `enable_autotrade=true` with strict
   per-symbol limits and kill switch.
5. Web UI for signal review, label-correctness feedback, and PnL dashboard.
6. Sumsub live integration (Mock provider in CI; Sumsub adapter is HMAC-ready).
7. TON Wallet Pay live merchant key (sandbox flow already wired).
8. On-chain vault (Solana primary, TON secondary) per `docs/vault-spec.md`.
9. White-label sub-advisor partnerships per `docs/white-label-spec.md`.
