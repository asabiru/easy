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
