---
name: compliance
description: |
  Compliance gate for SignalX. Trigger on any change touching trading
  execution, custody, capital flows, marketing claims, or user data.
---

# /compliance — Regulatory + legal review

## Hard rules (never break)

1. **No custody.** SignalX never holds client funds. Period.
2. **No discretionary management without a license.** Paper-mode
   default + per-sub `live_trading_enabled` flag preserve this.
3. **No guaranteed-returns marketing.** Headlines are descriptive,
   not predictive. "Past performance" disclaimer on every page that
   shows P&L numbers.
4. **No unsolicited investment advice in jurisdictions we are not
   registered in.** Country-block via IP at minimum (phase 2).
5. **No PII in logs.** Email may appear in audit log but never a
   raw API key, password, or session token.

## Track A — Investor IB rev-share

- We are an **introducer**, not an advisor. Investors trade on their
  own exchange account.
- Compensation disclosure: "SignalX receives a commission from the
  exchange when you trade on our IB code. This may create an incentive
  for us to encourage trading volume."
- KYC pass-through: the exchange handles KYC. We collect only contact
  info + capital band for our own qualification.

## Track B — Retail auto-trade bot

- We are an **execution-tooling provider**, not an investment advisor
  or broker-dealer.
- Client provides API keys with **trade-only** scope. We surface this
  requirement during onboarding.
- 14-day money-back, paper-mode default 7 days, kill switch always
  available.
- Performance claims: only show actual results from paper or live
  orders, never hypothetical backtests in marketing.

## Phase-2 white-label

When ARR > $1M, partner with:
- US: a registered RIA (Series 65 / 66) — Carta / Vanilla-style
- EU: a MiFID-passported broker-dealer (Lithuania, Estonia popular)
- KYC: Sumsub or Persona

## Output

Per finding: `severity:` (info / minor / major / blocker),
`jurisdiction:` (US / EU / global), `recommendation:` (concrete copy
change, code change, or process change).
