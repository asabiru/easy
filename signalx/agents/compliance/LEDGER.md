# Compliance Agent — LEDGER

> Mission: keep both monetisation tracks **off the regulatory hot list**.
> Hard rules — **never violated**:
>   1. We never hold client funds. (Track A: investor's own exchange. Track B: trade-only API key on client's exchange.)
>   2. No tier of the product is marketed as "guaranteed returns" or "investment advice".
>   3. Every paid client passes KYC before live execution is allowed.

---

## C-001 · Disclaimer surface
- **Status:** live
- All UI surfaces (Telegram, web, API) display "Not financial advice. Auto-trade requires explicit per-account opt-in."

## C-002 · Global kill-switch
- **Status:** live
- `Settings.enable_autotrade=False` ⇒ no live exchange call ever leaves the box.

## C-003 · Per-subscription opt-in
- **Status:** live
- `AutoTradeSubscription.live_trading_enabled` AND `paper_until <= now` AND global flag must all be true. Three-of-three.

## C-004 · No-credentials-in-PR check
- **Status:** live (CI grep)
- Repo blocks merging any file containing real-looking exchange API keys.

## C-005 · API-key permission validation
- **Status:** proposed
- On `/autotrade/subscribe` we will (phase-1.1) call the exchange's "info" endpoint to assert the supplied key has **trade-only** permission and **no withdraw**. Reject the subscription if withdraw is allowed.

---

## C-006 · White-label RIA / MiFID partnership *(unblocks MZ-002 + MZ-004)*
- **Status:** proposed
- **US:** white-label under a registered RIA (~$10k setup + $2k/mo). Avoids needing our own Form ADV until ARR > $1M.
- **EU:** sub-license under a MiFID-licensed broker-dealer, e.g. through B2B regulated infra such as Sumsub-affiliated entities or Ostrum-style cross-border vehicles.
- **Crypto:** for Track A IB-only flow, no licence is required because the investor trades on their own exchange account; we only collect referral revenue.

## C-007 · KYC / AML on every paid sub
- **Status:** proposed (vendor select pending)
- **Vendor candidates:** Sumsub, Persona, Veriff. Sumsub has best crypto-exchange parity.
- **Trigger:** required before `go-live` (i.e. paper-mode is unkyced; live-trade is kyced).
- **Storage:** PII never touches our DB; we keep only the verification reference id.

## C-008 · No-custody hard rule (codified)
- **Status:** live
- Codified in:
  - `Settings.enable_autotrade` global flag
  - `AutoTradeSubscription.api_key_encrypted` is **trade-only**
  - executor never calls withdraw endpoints, no withdraw method exists in the codebase
  - landing-site copy explicitly states "we never hold your money"

## C-009 · Geo-block list
- **Status:** proposed
- Block subscribers from sanctioned jurisdictions (OFAC list) at signup. Maintained against US Treasury SDN list refresh weekly.

## C-010 · Performance-claims governance
- **Status:** proposed
- All public win-rate / latency / P&L claims must come from `/performance/summary` and be timestamped in the public PDF. Ad creatives reviewed against this source of truth before publication.

## C-011 · Sponsored content disclosure *(MZ-015)*
- **Status:** proposed
- Sponsored research notes carry a banner "PAID PLACEMENT — does NOT alter signal scores", visible in Telegram and on the dashboard. FTC §255 compliant.

---

## Open questions
1. Crypto-broker MSB / FinCEN registration: required for our IB rev-share or only for custody? (Lean toward "not required" per legal note; needs counsel sign-off.)
2. Do we need separate UK FCA AR coverage for ads to UK retail?
3. Tokenised vault (MZ-007) — Cayman or BVI for the SPV?
