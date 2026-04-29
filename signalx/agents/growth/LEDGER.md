# Growth Agent

## Mission
Distribution, partnerships, and virality. Find leverage that compounds:
every dollar of marketing should buy more reach a month later than it does today.

## Active proposals

### G-001 · Exchange partnerships
Status: proposed.
Co-marketing deals with Bybit / Backpack / Aster / Hyperliquid: they offer
their tokenized-stock-perp users a free month of SignalX Starter; we promote
their stock-perp product to our list. Mutual list growth + affiliate revenue
(see MZ-004).

### G-002 · Open-data hooks
Status: proposed.
Public read-only `/signals?action=LONG&since=24h` endpoint (rate-limited to
60 req/h). External devs build dashboards and bots on top → free distribution.

### G-003 · Quant community presence
Status: proposed.
Sponsor a small monthly hackathon ($1k bounty) where developers build new
event_rules entries. Winners get free Pro tier + handle credit in
data/event_rules.json. Builds an ecosystem of contributors.

### G-004 · Newsletter cross-promo
Status: proposed.
Trade ad slots with three top finance-Twitter newsletters (The Daily Upside,
Bespoke Investment, etc.). Cost ~$1.5k–$3k per send, expected CTR 1.5–2.5%.

### G-005 · Influencer affiliate
Status: proposed.
Top 20 crypto-Twitter educators get a custom referral link with 30%
commission (MZ-005) plus a permanent free Pro account. Self-funding once
each partner brings 4+ Pro conversions / month.

## Decision log
- 2025-04-28: Growth tactics that require modifying signal logic (e.g. "boost
  signals for tokens our partner exchange lists") are rejected on principle.

## Open questions
- Where do we host the public read-only API? Same backend (rate-limited) or
  separate edge cache?
