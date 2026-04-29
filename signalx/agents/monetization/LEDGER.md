# Monetization Agent — LEDGER

> Pairs with **Marketing** (M-*). Each row evolves with `Status: proposed → accepted → in_progress → live → retired`.
> The mission moved from SaaS-selling to a **dual-track** model:
>   * **Track A (HNW / family-office) — IB & capital introduction.** No custody. Investor trades through their own exchange account; we earn affiliate rev-share.
>   * **Track B (retail / prosumer) — Auto-trade bot subscription.** Client gives the bot a **trade-only** API key (no withdraw, IP-whitelisted). Bot executes signals automatically; client pays subscription + (VIP) performance fee.

## Mission
Maximise dollars per signal-hour without ever holding client funds.

---

## Track A — HNW / IB rev-share

### MZ-001 · Exchange IB / affiliate rev-share *(primary)*
- **Status:** proposed
- **Mechanism:** sign VIP IB agreements with Bybit, Binance, Bitget, Backpack, Aster, Hyperliquid. Std rate **20–40 bps of taker-volume + 10–20% of paid fees**. We own the referral code, investor links account.
- **Unit economics:** $10M/mo volume × 30 bps = $30k/mo from one investor.
- **Owner:** monetization
- **Next action:** apply to Bybit Affiliates VIP and Binance Mega-Influencer programs in week 1.

### MZ-002 · Performance fee on managed flow *(secondary)*
- **Status:** proposed
- **Mechanism:** SMA / sub-account on the investor's exchange. We get read-only API + (optional) trade-only key. High-Water-Mark, **20% of alpha**, billed quarterly. Fund-of-one variant for >$1M tickets via white-label RIA partner.
- **Owner:** monetization × compliance
- **Blockers:** white-label RIA partnership (see C-006).

### MZ-003 · PFOF / market-maker rebates
- **Status:** proposed
- **Mechanism:** as a flow aggregator we earn **0.5–2 bps** rebate from biz on routed taker volume.
- **Owner:** monetization

### MZ-004 · Co-investment with prop firms
- **Status:** proposed
- **Mechanism:** SignalX provides signals → prop firm allocates capital to the trader. **30/70 to 50/50** P&L split.
- **Target:** 50 prop-traders, $5k avg monthly P&L → $1.25M–$2M ARR.

### MZ-005 · Capital-introduction one-shots
- **Status:** proposed
- **Mechanism:** $5k–$50k bonuses from biz per VHP (>$1M deposit) we introduce.

### MZ-006 · Strategy licensing for hedge funds / family offices
- **Status:** proposed
- **Pricing:** $25k–$250k/year per desk for read-only API + custom event-rules.

### MZ-007 · Tokenised on-chain vault
- **Status:** parked (phase-2)
- **Mechanism:** Solana / Base USDC vault. 2% AUM + 20% perf. Heavy regulatory lift; defer until ARR > $1M.

---

## Track B — Auto-trade bot subscription

### MZ-008 · Tier ladder *(primary retail revenue)*
- **Status:** in_progress (code shipped; pricing live in landing)

| Tier | Price | What it does |
| --- | --- | --- |
| **Manual+** | $99 / mo | Telegram alert + 1-click "Execute on my Bybit". No autopilot. |
| **Auto-Lite** | $249 / mo | Full auto, fixed position size (10% / signal), top-3 alerts/day. |
| **Auto-Pro** | $499 / mo | Full auto, adaptive sizing by `signal_score`, all signals, custom filters (min confidence, max fake_risk). |
| **VIP** | $999 / mo + 15% performance fee | Auto-Pro + priority execution + custom risk profile + monthly performance review. |

- **Default safety:** every new sub starts in **paper-mode for 7 days** (`autotrade_default_paper_days`).
- **Risk caps default:** `max_position_pct=10%`, `daily_loss_limit_pct=5%`. Either trip pauses the sub.
- **Blocked unless** global `ENABLE_AUTOTRADE=true` + per-sub `live_trading_enabled=true`.

### MZ-009 · Founding-100 lifetime discount
- **Status:** proposed (paired with M-006)
- **Offer:** first 100 paying subs lock $99/mo Manual+ → $49/mo lifetime + VIP discord.
- **Why:** seed social-proof base + create urgency.

### MZ-010 · 14-day money-back guarantee
- **Status:** accepted
- **Why:** kills the biggest objection; refund-rate budget set to 8%.

### MZ-011 · "First $5,000 traded free"
- **Status:** proposed
- **Mechanism:** waive subscription fee until client has cleared $5k notional. Our IB rebate (≈ $5–$10) covers cost-of-service.

### MZ-012 · Affiliate / referral
- **Status:** proposed
- **Rates:** 30% recurring for users, **40% recurring** for verified trading-creators (>10k followers), **$200 one-shot** for VIP conversion.
- **Implementation:** Stripe referral metadata + cookie 90 days.

### MZ-013 · Annual pre-pay discount
- **Status:** proposed
- **Offer:** -20% for annual upfront. Cash-flow positive day-one.

### MZ-014 · B2B latency feed
- **Status:** parked (phase-2)
- **Mechanism:** sub-second push of normalised events to HFT desks. $5k–$20k / mo.

### MZ-015 · Sponsored research notes
- **Status:** proposed
- **Mechanism:** issuer IR teams pay for **distribution** ($5k–$25k/post). **Never** changes `impact_score`. Disclosed inline.

---

## Decision log
- **2024-Q4** → Pivot from "SaaS only" to dual-track HNW IB + retail bot subs.
- **2024-Q4** → Auto-trade bot model approved with 4 tiers + 7-day paper default + 14-day refund.
- **2024-Q4** → MZ-001 promoted to primary revenue line.

## Open questions
1. Stripe vs Lemon Squeezy for SaaS billing? (LS handles VAT, less onboarding friction.)
2. Can we offer **fee rebate sharing** (the IB share) back to retail users to undercut competitors?
3. Which exchange has the most permissive trade-only API key model for OAuth onboarding?
