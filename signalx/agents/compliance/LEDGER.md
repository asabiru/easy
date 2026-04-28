# Compliance Agent

## Mission
Keep SignalX on the right side of "this is research, not investment advice".
Enforce disclaimers, KYC where required, and clean separation between paid
content and paid ads.

## Active proposals

### C-001 · Disclaimer on every signal
Status: implemented in formatter (footer line). Every Telegram message and
every API response includes:
> **Not investment advice. Research-only product. SignalX never executes
> trades on your behalf.**

### C-002 · Sponsored-content disclosure
Status: proposed (gates MZ-003).
If a signal originates from a sponsored ticker, the Telegram message MUST
prefix `[SPONSORED]` and the API response must include `"sponsored": true`.
Sponsored content cannot alter `impact_score` or `fake_risk`.

### C-003 · Geo-block for restricted jurisdictions
Status: proposed.
Block paid subscriptions from US retail (we are not registered as an
investment adviser) and from sanctioned countries. UI banner + Stripe
country deny-list.

### C-004 · Data retention
Status: proposed.
- News events / market snapshots: 365 days.
- Support tickets: 730 days.
- Telegram message logs: 90 days.
- Personal account data (email): until account deletion + 30 days legal hold.

### C-005 · Autotrade hard-disable
Status: implemented.
`ENABLE_AUTOTRADE` defaults to `false`; codebase has zero order-placement
calls. CI must fail if anyone adds an exchange `create_order` call.

## Decision log
- 2025-04-28: SignalX MVP v0.1 has explicit non-execution stance. All
  marketing copy must say "signals", not "trades". This is a hard rule.

## Open questions
- Do we need an MSB / VASP licence in any jurisdiction once we add Pro
  subscriptions paid in USDT?
- Does the EU MiCA framework treat news-signal subscriptions as financial
  research? Likely no, but verify.
