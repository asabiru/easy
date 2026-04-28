# Monetization / Revenue Agent

## Mission
Turn SignalX into a sustainable revenue engine without compromising signal
quality. Work shoulder-to-shoulder with Marketing (M-001..M-005) and
Compliance (C-001..C-003).

## Active proposals

### MZ-001 · Tiered subscription
Status: proposed.

| Tier         | Price (mo) | Features                                                                            |
| ------------ | ---------: | ----------------------------------------------------------------------------------- |
| Free         |        $0  | Public Telegram channel; redacted signals (action only, no entry/SL/TP); 30-min delay |
| Starter      |       $29  | Real-time core universe (NVDA, TSLA, AAPL, AMZN, MSFT, COIN); full entry/SL/TP      |
| Pro          |       $99  | Full universe (incl. energy XOM/CVX/SHEL/OXY/COP/SLB/NEE/FSLR/ENPH/BP); 5 web hooks |
| Desk         |      $499  | Up to 5 seats; raw `/signals` API; webhooks; private Telegram channel              |
| Enterprise   |     custom | SLA, dedicated infra, customised universe + custom event rules                       |

Acquisition: Free → Starter conversion via 7-day Pro trial when a signal scores >85.

### MZ-002 · API monetisation
Status: proposed.
Once the API is publicly mounted (already exists), expose `/signals` and
`/performance/summary` behind:
- Per-call pricing: $0.001 per signal read (Stripe metered).
- Bulk: $499 / mo for unlimited reads up to 1M / mo.
Quants and prop desks will buy the API directly.

### MZ-003 · Sponsored tickers (advertising, not advice)
Status: proposed (Compliance C-002 must clear).
Allow listed companies / IR firms to "sponsor" a ticker so their official
press releases are mirrored into our pipeline with a `sponsored=true` tag
and a clear disclosure banner in Telegram messages. Revenue: $5k–$25k / mo
per sponsored ticker. **Hard rule**: sponsorship NEVER alters impact_score
or fake_risk; only affects routing priority for the company's own posts.

### MZ-004 · Exchange affiliate revenue
Status: proposed.
Every signal in Telegram includes a "Trade it" deep link to the user's
preferred exchange (Bybit / Binance / OKX / Backpack / Aster). Affiliate
revenue averages 20–30 bps of the user's first-year volume; that's the
single largest passive revenue line for products of this shape.

### MZ-005 · Referral program
Status: proposed (joint with Marketing M-004).
30% commission for 6 months on referred Pro / Desk subscriptions; payable in
USDT. Track via Stripe Customer Metadata `ref=<handle>`.

### MZ-006 · Backtest report packs
Status: proposed.
One-off $99 pack: "Last 90 days of NVDA / TSLA / OPEC signals with full
audit trail, charts, and win/loss decomposition." Market-research positioning,
not investment advice.

### MZ-007 · White-label
Status: parked (revisit after 1k Pro subs).
License the engine to crypto exchanges so they can offer a "stocks-on-X
exchange" newsfeed natively. Recurring 6-figure deals.

## Decision log
- 2025-04-28: No revenue feature touches risk_engine or signal_engine logic.
  Sponsored tickers are routing-only. Compliance C-002 must approve every
  paid mechanic before launch.
- 2025-04-28: Stripe is the billing system of record from MVP. Crypto
  payments come later (USDT on TRON / Solana via NowPayments) once the
  legal entity is set up.

## Open questions
- Which entity issues invoices for non-US customers? (Compliance / legal.)
- Ad inventory: do we hard-rate-limit sponsored posts so they never exceed
  10% of channel volume? Marketing strongly prefers yes.
